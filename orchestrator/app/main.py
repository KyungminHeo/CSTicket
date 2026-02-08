"""
LangGraph Orchestrator 메인 엔트리포인트
Kafka에서 티켓 이벤트를 소비하고 LangGraph 워크플로우로 처리

실행 방법:
    cd orchestrator
    python -m app.main

처리 흐름:
1. Kafka "ticket-events" 토픽 구독
2. 새 티켓 이벤트 수신
3. LangGraph 워크플로우 실행 (classify → generate → validate)
4. Redis에 실시간 상태 업데이트
5. Kafka "agent-results" 토픽에 결과 발행
"""
import asyncio
import signal
import sys
from pathlib import Path
from datetime import datetime

# shared 패키지 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared import (
    get_settings,
    get_redis_client,
    KafkaConsumerClient,
    KafkaProducerClient,
    TOPIC_TICKET_EVENTS,
    TOPIC_AGENT_RESULTS,
    TicketStatus,
    TicketCategory,
    TicketPriority,
    AgentResultEvent,
)
from app.graph import TicketState, create_initial_state, app as workflow_app


class Orchestrator:
    """
    메인 오케스트레이터 클래스
    
    역할:
    - Kafka Consumer로 티켓 이벤트 수신
    - LangGraph 워크플로우 실행
    - Redis로 실시간 상태 업데이트
    - Kafka Producer로 결과 발행
    """
    
    def __init__(self):
        self.settings = get_settings()
        
        # Kafka Consumer 설정
        # - 토픽: ticket-events (새 티켓 이벤트)
        # - 그룹: orchestrator-group (같은 그룹 내 인스턴스가 파티션 분배)
        self.consumer = KafkaConsumerClient(
            topics=[TOPIC_TICKET_EVENTS],
            group_id="orchestrator-group"
        )
        
        # Kafka Producer (처리 결과 발행용)
        self.producer = KafkaProducerClient()
        
        self.redis = None
        self._running = False
    
    async def start(self):
        """
        Orchestrator 시작
        
        1. Redis 연결
        2. Kafka Consumer 시작
        3. Kafka Producer 시작
        4. 이벤트 소비 루프 시작
        """
        print("🚀 Starting LangGraph Orchestrator...")
        
        # Redis 연결 (상태 저장용)
        self.redis = await get_redis_client()
        print("✅ Redis connected")
        
        # Kafka Consumer 시작 (이벤트 수신용)
        await self.consumer.start()
        print("✅ Kafka consumer started")
        
        # Kafka Producer 시작 (결과 발행용)
        await self.producer.start()
        print("✅ Kafka producer started")
        
        self._running = True
        print("🎯 Listening for ticket events...")
        
        # 무한 루프로 이벤트 소비 시작
        # 새 이벤트가 들어올 때마다 _handle_ticket_event 호출
        await self.consumer.consume(self._handle_ticket_event)
    
    async def stop(self):
        """
        Orchestrator 종료
        
        모든 연결 정리 (Graceful Shutdown)
        """
        print("\n🛑 Stopping orchestrator...")
        self._running = False
        
        await self.consumer.stop()
        await self.producer.stop()
        if self.redis:
            await self.redis.disconnect()
        
        print("✅ Orchestrator stopped")
    
    async def _handle_ticket_event(self, event: dict):
        """
        Kafka 티켓 이벤트 처리
        
        Gateway가 발행한 TicketCreatedEvent를 처리
        
        Args:
            event: Kafka 메시지 페이로드
                {
                    "ticket_id": "t-abc123",
                    "user_id": "user-uuid",
                    "content": "결제가 안 됩니다",
                    "metadata": {...},
                    "created_at": "2026-02-06T..."
                }
        """
        ticket_id = event.get("ticket_id")
        user_id = event.get("user_id")
        content = event.get("content")
        metadata = event.get("metadata", {})
        
        print(f"\n📥 Processing ticket: {ticket_id}")
        
        try:
            # 1. 초기 상태 생성
            initial_state = create_initial_state(
                ticket_id=ticket_id,
                user_id=user_id,
                content=content,
                metadata=metadata
            )
            
            # 2. Redis 상태 업데이트 (폴링용)
            await self.redis.set_ticket_status(ticket_id, "classifying", progress=10)
            
            # 3. LangGraph 워크플로우 실행
            final_state = await self._run_workflow(initial_state)
            
            # 4. 결과를 Kafka에 발행
            await self._publish_result(final_state)
            
            print(f"✅ Ticket {ticket_id} processed: {final_state.status}")
            
        except Exception as e:
            print(f"❌ Error processing ticket {ticket_id}: {e}")
            # 실패 상태로 업데이트
            await self.redis.set_ticket_status(ticket_id, "failed", progress=0)
    
    async def _run_workflow(self, initial_state: TicketState) -> TicketState:
        """
        LangGraph 워크플로우 실행
        
        워크플로우 노드:
        1. classify: 티켓 분류 (카테고리, 우선순위, 태그)
        2. generate: RAG 기반 응답 생성
        3. validate: 품질 검증
           - 통과 → complete
           - 재시도 필요 → generate로 돌아감
           - 3회 실패 → escalate
        
        Args:
            initial_state: 초기 티켓 상태
            
        Returns:
            최종 처리 상태 (completed, escalated, failed 중 하나)
        """
        ticket_id = initial_state.ticket_id
        state_dict = initial_state.model_dump()
        
        # 각 단계별 진행률 매핑
        progress_map = {
            "classifying": 25,   # 분류 중
            "generating": 50,    # 응답 생성 중
            "validating": 75,    # 품질 검증 중
            "completed": 100,    # 처리 완료
            "escalated": 100,    # 에스컬레이션
            "failed": 0,         # 실패
        }
        
        # LangGraph 워크플로우 실행 (스트리밍)
        # astream: 각 노드 실행 후 중간 상태 yield
        async for event in workflow_app.astream(state_dict):
            # event: {노드이름: 노드실행후상태}
            for node_name, node_state in event.items():
                status = node_state.get("status", "pending")
                progress = progress_map.get(status, 0)
                
                # Redis 상태 업데이트 (클라이언트 폴링용)
                await self.redis.set_ticket_status(ticket_id, status, progress)
                
                # 체크포인트 저장 (장애 복구용)
                await self.redis.save_agent_state(ticket_id, node_state)
                
                # 최종 상태 유지
                state_dict = node_state
        
        # 처리 완료 후 체크포인트 삭제
        await self.redis.delete_agent_state(ticket_id)
        
        return TicketState(**state_dict)
    
    async def _publish_result(self, state: TicketState):
        """
        처리 결과를 Kafka에 발행
        
        Topic: agent-results
        Gateway가 이 이벤트를 소비하여 DB 업데이트 (TODO)
        
        Args:
            state: 최종 티켓 상태
        """
        event = AgentResultEvent(
            ticket_id=state.ticket_id,
            category=TicketCategory(state.category) if state.category else TicketCategory.OTHER,
            priority=TicketPriority(state.priority) if state.priority else TicketPriority.MEDIUM,
            response=state.final_response or state.draft_response or "",
            quality_score=state.quality_score,
            status=TicketStatus(state.status),
            completed_at=datetime.utcnow()
        )
        
        await self.producer.send_agent_result(event)


# ============================================================
# 메인 엔트리포인트
# ============================================================

async def main():
    """
    Orchestrator 실행 메인 함수
    
    Ctrl+C (SIGINT) 또는 SIGTERM 시 graceful shutdown
    """
    orchestrator = Orchestrator()
    
    # 종료 시그널 핸들러 등록
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        asyncio.create_task(orchestrator.stop())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows는 add_signal_handler 미지원
            pass
    
    try:
        await orchestrator.start()
    except KeyboardInterrupt:
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())
