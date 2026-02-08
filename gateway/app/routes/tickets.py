"""
티켓 라우터 (Ticket Routes)
- 고객 지원 티켓 생성, 조회, 상태 확인
- AI 에이전트가 처리하는 비동기 워크플로우
"""
from datetime import datetime
from typing import Annotated, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from shared import (
    get_redis_client,
    get_kafka_producer,
    TicketCreate,
    TicketResponse,
    TicketStatusResponse,
    TicketStatus,
    TicketCreatedEvent,
    TokenPayload,
    TOPIC_TICKET_EVENTS,
)
from app.auth import get_current_user


router = APIRouter()

# ============== 임시 인메모리 DB (TODO: PostgreSQL로 교체) ==============
fake_tickets_db: dict[str, dict] = {}


class TicketCreateResponse(BaseModel):
    """티켓 생성 응답 스키마"""
    ticket_id: str
    status: str
    message: str


# ============== 티켓 생성 ==============
@router.post("", response_model=TicketCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_ticket(
    request: Request,
    ticket: TicketCreate,
    user: Annotated[TokenPayload, Depends(get_current_user)]
):
    """
    새 고객 지원 티켓 생성 (비동기 처리)
    
    처리 흐름:
    1. 티켓 ID 생성 (t-xxx 형식)
    2. DB에 티켓 저장 (상태: pending)
    3. Redis에 초기 상태 저장 (폴링용)
    4. Kafka에 이벤트 발행 → Orchestrator가 소비
    5. 202 Accepted 응답 (처리는 백그라운드에서)
    
    사용자는 /tickets/{id}/status 로 처리 진행 상황 확인 가능
    """
    redis = await get_redis_client()
    kafka = await get_kafka_producer()
    
    # 티켓 ID 생성 (t- 접두사 + UUID 일부)
    ticket_id = f"t-{uuid.uuid4().hex[:12]}"
    now = datetime.utcnow()
    
    # 티켓 데이터 구성
    ticket_data = {
        "id": ticket_id,
        "user_id": user.sub,           # JWT에서 추출한 사용자 ID
        "content": ticket.content,      # 고객 문의 내용
        "metadata": ticket.metadata or {},
        "category": None,               # AI가 분류 예정
        "priority": None,               # AI가 분류 예정
        "status": TicketStatus.PENDING, # 초기 상태
        "response": None,               # AI가 생성 예정
        "created_at": now,
        "updated_at": now,
    }
    
    # DB 저장 (현재는 인메모리)
    fake_tickets_db[ticket_id] = ticket_data
    
    # Redis에 처리 상태 저장 (실시간 폴링용)
    # Redis Key: status:{ticket_id} → {stage, progress, updated_at}
    await redis.set_ticket_status(ticket_id, TicketStatus.PENDING.value, progress=0)
    
    # Kafka에 티켓 생성 이벤트 발행
    # Orchestrator의 Consumer가 이 이벤트를 소비하여 AI 처리 시작
    # Kafka Topic: ticket-events
    event = TicketCreatedEvent(
        ticket_id=ticket_id,
        user_id=user.sub,
        content=ticket.content,
        metadata=ticket.metadata,
        created_at=now,
    )
    await kafka.send_ticket_event(event)
    
    # 202 Accepted: 요청 수락됨, 처리 진행 중
    return TicketCreateResponse(
        ticket_id=ticket_id,
        status="pending",
        message="Ticket has been submitted and is being processed."
    )


# ============== 티켓 상세 조회 ==============
@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: str,
    user: Annotated[TokenPayload, Depends(get_current_user)]
):
    """
    티켓 상세 정보 조회
    
    - 본인 티켓만 조회 가능 (관리자는 모든 티켓 가능)
    - AI 처리 완료 후 response 필드에 답변 포함
    """
    # DB에서 티켓 조회
    ticket = fake_tickets_db.get(ticket_id)
    
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found"
        )
    
    # 소유권 확인 (본인 또는 관리자만 접근 가능)
    if ticket["user_id"] != user.sub and user.role.value != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this ticket"
        )
    
    return TicketResponse(
        id=ticket["id"],
        user_id=ticket["user_id"],
        content=ticket["content"],
        category=ticket["category"],    # AI가 분류한 카테고리
        priority=ticket["priority"],    # AI가 분류한 우선순위
        status=ticket["status"],
        response=ticket["response"],    # AI가 생성한 응답
        created_at=ticket["created_at"],
        updated_at=ticket["updated_at"],
    )


# ============== 티켓 처리 상태 조회 (폴링용) ==============
@router.get("/{ticket_id}/status", response_model=TicketStatusResponse)
async def get_ticket_status(
    ticket_id: str,
    user: Annotated[TokenPayload, Depends(get_current_user)]
):
    """
    티켓 처리 상태 실시간 조회 (폴링 엔드포인트)
    
    Redis에서 현재 처리 단계 조회:
    - pending (0%): 대기 중
    - classifying (25%): AI가 카테고리/우선순위 분류 중
    - generating (50%): AI가 응답 생성 중
    - validating (75%): AI가 품질 검증 중
    - completed (100%): 처리 완료
    - escalated (100%): 상담사 에스컬레이션
    
    클라이언트는 이 엔드포인트를 주기적으로 호출하여 진행 상황 표시
    """
    redis = await get_redis_client()
    
    # 티켓 존재 확인
    ticket = fake_tickets_db.get(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found"
        )
    
    # 소유권 확인
    if ticket["user_id"] != user.sub and user.role.value != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this ticket"
        )
    
    # Redis에서 실시간 상태 조회
    # Redis Key: status:{ticket_id} → Hash {stage, progress, updated_at}
    status_data = await redis.get_ticket_status(ticket_id)
    
    if status_data:
        return TicketStatusResponse(
            ticket_id=ticket_id,
            stage=TicketStatus(status_data["stage"]),
            progress=status_data["progress"],
            updated_at=datetime.fromtimestamp(status_data["updated_at"])
        )
    
    # Redis에 없으면 DB 상태로 fallback
    return TicketStatusResponse(
        ticket_id=ticket_id,
        stage=ticket["status"],
        progress=0 if ticket["status"] == TicketStatus.PENDING else 100,
        updated_at=ticket["updated_at"]
    )


# ============== 티켓 목록 조회 ==============
@router.get("", response_model=list[TicketResponse])
async def list_tickets(
    user: Annotated[TokenPayload, Depends(get_current_user)],
    status_filter: Optional[TicketStatus] = None,
    limit: int = 20,
    offset: int = 0
):
    """
    사용자의 티켓 목록 조회
    
    - 일반 사용자: 본인 티켓만
    - 관리자: 모든 티켓
    - 상태별 필터링 및 페이징 지원
    """
    # 사용자 권한에 따라 티켓 필터링
    user_tickets = [
        t for t in fake_tickets_db.values()
        if t["user_id"] == user.sub or user.role.value == "admin"
    ]
    
    # 상태 필터 적용
    if status_filter:
        user_tickets = [t for t in user_tickets if t["status"] == status_filter]
    
    # 최신순 정렬
    user_tickets.sort(key=lambda t: t["created_at"], reverse=True)
    
    # 페이징 적용
    paginated = user_tickets[offset:offset + limit]
    
    return [
        TicketResponse(
            id=t["id"],
            user_id=t["user_id"],
            content=t["content"],
            category=t["category"],
            priority=t["priority"],
            status=t["status"],
            response=t["response"],
            created_at=t["created_at"],
            updated_at=t["updated_at"],
        )
        for t in paginated
    ]
