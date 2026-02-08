"""
LangGraph 상태 정의
티켓 처리 워크플로우에서 노드 간 전달되는 상태 객체

모든 AI 에이전트(Classifier, Generator, Validator)가
이 상태 객체를 읽고 수정함
"""
from typing import Literal, Optional, Annotated
from pydantic import BaseModel, Field
import operator


class TicketState(BaseModel):
    """
    티켓 처리 워크플로우 상태 객체
    
    LangGraph의 모든 노드에서 공유됨
    각 노드가 실행될 때마다 상태가 업데이트되고 다음 노드로 전달됨
    
    데이터 흐름:
    [초기 입력] → classify → [분류 결과 추가] → generate → [응답 추가] → validate → [검증 결과 추가]
    """
    
    # ============================================================
    # 입력 데이터 (Gateway에서 전달)
    # ============================================================
    ticket_id: str                           # 티켓 ID (예: "t-abc123")
    user_id: str                             # 사용자 ID
    content: str                             # 고객 문의 내용
    metadata: dict = Field(default_factory=dict)  # 추가 정보 (예: 브라우저, OS)
    
    # ============================================================
    # 분류 결과 (Classifier Agent가 채움)
    # ============================================================
    category: Optional[str] = None           # 카테고리: billing, technical, general, complaint, other
    priority: Optional[Literal["low", "medium", "high", "urgent"]] = None  # 우선순위
    tags: list[str] = Field(default_factory=list)  # 태그 목록 (예: ["결제", "환불"])
    sentiment: Optional[str] = None          # 감정 분석: positive, neutral, negative
    
    # ============================================================
    # 응답 생성 결과 (Generator Agent가 채움)
    # ============================================================
    context_docs: list[str] = Field(default_factory=list)  # RAG로 검색된 문서들
    draft_response: Optional[str] = None     # 생성된 초안 응답
    
    # ============================================================
    # 품질 검증 결과 (Validator Agent가 채움)
    # ============================================================
    quality_score: float = 0.0               # 품질 점수 (0.0 ~ 1.0)
    quality_feedback: Optional[str] = None   # 개선 피드백
    policy_compliant: bool = True            # 정책 준수 여부
    tone_appropriate: bool = True            # 톤 적절성 여부
    
    # ============================================================
    # 워크플로우 제어
    # ============================================================
    retry_count: int = 0                     # 현재 재시도 횟수
    max_retries: int = 3                     # 최대 재시도 횟수
    
    # ============================================================
    # 최종 출력
    # ============================================================
    final_response: Optional[str] = None     # 최종 승인된 응답
    status: Literal["pending", "classifying", "generating", "validating", "completed", "escalated", "failed"] = "pending"
    error_message: Optional[str] = None      # 에러 발생 시 메시지
    
    class Config:
        """Pydantic 설정"""
        extra = "allow"  # 추가 필드 허용 (유연성)


def create_initial_state(
    ticket_id: str,
    user_id: str,
    content: str,
    metadata: Optional[dict] = None
) -> TicketState:
    """
    새 티켓의 초기 상태 생성
    
    Kafka 이벤트 수신 후 Orchestrator에서 호출
    """
    return TicketState(
        ticket_id=ticket_id,
        user_id=user_id,
        content=content,
        metadata=metadata or {},
        status="pending"
    )
