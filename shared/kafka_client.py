"""
Kafka 클라이언트 유틸리티
멀티-AI 에이전트 시스템의 이벤트 발행/소비 처리

사용 토픽:
- ticket-events: 새 티켓 생성 이벤트 (Gateway → Orchestrator)
- agent-results: AI 처리 완료 결과 (Orchestrator → Gateway)
- dead-letter: 처리 실패 이벤트 (TODO)
"""
import json
from typing import Any, Callable, Optional
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from aiokafka.errors import KafkaError
from pydantic import BaseModel
from .config import get_settings


# ============================================================
# Kafka Topic 상수
# ============================================================

TOPIC_TICKET_EVENTS = "ticket-events"   # 티켓 생성 이벤트
TOPIC_AGENT_RESULTS = "agent-results"   # AI 처리 결과
TOPIC_DEAD_LETTER = "dead-letter"       # 처리 실패 이벤트


# ============================================================
# Kafka Producer (이벤트 발행)
# ============================================================

class KafkaProducerClient:
    """
    비동기 Kafka Producer 클라이언트
    
    사용처:
    - Gateway: 티켓 생성 시 ticket-events 발행
    - Orchestrator: 처리 완료 시 agent-results 발행
    """
    
    def __init__(self):
        self.settings = get_settings()
        self._producer: Optional[AIOKafkaProducer] = None
    
    async def start(self) -> None:
        """
        Kafka Producer 시작
        
        연결 설정:
        - bootstrap_servers: Kafka 브로커 주소 (localhost:9092)
        - value_serializer: dict → JSON bytes 변환
        - key_serializer: 문자열 → bytes 변환 (파티션 키용)
        """
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        )
        await self._producer.start()
    
    async def stop(self) -> None:
        """Kafka Producer 종료"""
        if self._producer:
            await self._producer.stop()
    
    async def send(self, topic: str, value: dict | BaseModel, key: Optional[str] = None) -> None:
        """
        Kafka 토픽에 메시지 발행
        
        Args:
            topic: 토픽 이름 (예: "ticket-events")
            value: 메시지 내용 (dict 또는 Pydantic 모델)
            key: 파티션 키 (같은 key는 같은 파티션으로 → 순서 보장)
        
        Key 사용 예:
        - ticket_id를 key로 → 같은 티켓의 이벤트가 순서대로 처리됨
        """
        if not self._producer:
            raise RuntimeError("Kafka producer not started. Call start() first.")
        
        # Pydantic 모델이면 dict로 변환
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        
        # 메시지 발행 및 ACK 대기
        await self._producer.send_and_wait(topic, value=value, key=key)
    
    async def send_ticket_event(self, event: BaseModel) -> None:
        """
        티켓 생성 이벤트 발행
        
        Gateway에서 호출 → Orchestrator가 소비
        Topic: ticket-events
        Key: ticket_id (같은 티켓 이벤트 순서 보장)
        """
        await self.send(TOPIC_TICKET_EVENTS, event, key=event.ticket_id)
    
    async def send_agent_result(self, event: BaseModel) -> None:
        """
        AI 처리 결과 발행
        
        Orchestrator에서 호출 → Gateway가 소비
        Topic: agent-results
        Key: ticket_id
        """
        await self.send(TOPIC_AGENT_RESULTS, event, key=event.ticket_id)


# ============================================================
# Kafka Consumer (이벤트 소비)
# ============================================================

class KafkaConsumerClient:
    """
    비동기 Kafka Consumer 클라이언트
    
    사용처:
    - Orchestrator: ticket-events 구독하여 AI 처리 시작
    - Gateway (TODO): agent-results 구독하여 DB 업데이트
    """
    
    def __init__(self, topics: list[str], group_id: str):
        """
        Args:
            topics: 구독할 토픽 목록 (예: ["ticket-events"])
            group_id: 컨슈머 그룹 ID (같은 그룹 내 컨슈머가 파티션 분배)
        """
        self.settings = get_settings()
        self.topics = topics
        self.group_id = group_id
        self._consumer: Optional[AIOKafkaConsumer] = None
        self._running = False
    
    async def start(self) -> None:
        """
        Kafka Consumer 시작
        
        연결 설정:
        - group_id: 컨슈머 그룹 (로드 밸런싱)
        - value_deserializer: JSON bytes → dict 변환
        - auto_offset_reset: 처음 연결 시 어디서 읽을지 (earliest = 처음부터)
        - enable_auto_commit: 오프셋 자동 커밋 (메시지 처리 완료 표시)
        """
        self._consumer = AIOKafkaConsumer(
            *self.topics,
            bootstrap_servers=self.settings.kafka_bootstrap_servers,
            group_id=self.group_id,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="earliest",  # 처음 연결 시 가장 오래된 메시지부터
            enable_auto_commit=True,       # 자동 오프셋 커밋
        )
        await self._consumer.start()
        self._running = True
    
    async def stop(self) -> None:
        """Kafka Consumer 종료"""
        self._running = False
        if self._consumer:
            await self._consumer.stop()
    
    async def consume(self, handler: Callable[[dict], Any]) -> None:
        """
        메시지 무한 루프로 소비
        
        Args:
            handler: 각 메시지를 처리할 비동기 함수
        
        동작 방식:
        1. Kafka에서 새 메시지 대기
        2. 메시지 도착 → handler 함수 호출
        3. 에러 발생 시 로깅 후 계속 진행 (TODO: Dead Letter Queue)
        4. stop() 호출 시 루프 종료
        """
        if not self._consumer:
            raise RuntimeError("Kafka consumer not started. Call start() first.")
        
        try:
            # 무한 루프로 메시지 소비
            async for msg in self._consumer:
                if not self._running:
                    break
                try:
                    # 메시지 처리 (예: AI 워크플로우 실행)
                    await handler(msg.value)
                except Exception as e:
                    # 에러 발생해도 다음 메시지 계속 처리
                    print(f"Error processing message: {e}")
                    # TODO: Dead Letter Queue로 전송
        except KafkaError as e:
            print(f"Kafka consumer error: {e}")
            raise


# ============================================================
# 싱글톤 인스턴스 관리
# ============================================================

_kafka_producer: Optional[KafkaProducerClient] = None


async def get_kafka_producer() -> KafkaProducerClient:
    """
    Kafka Producer 싱글톤 반환
    
    전역에서 하나의 연결만 유지하여 리소스 절약
    """
    global _kafka_producer
    if _kafka_producer is None:
        _kafka_producer = KafkaProducerClient()
        await _kafka_producer.start()
    return _kafka_producer
