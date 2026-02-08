"""
공유 Pydantic 모델
Gateway와 Orchestrator에서 공통으로 사용하는 데이터 모델

주요 모델 분류:
1. Enums: 상태/카테고리 열거형
2. User Models: 사용자 관련
3. Auth Models: 인증/토큰 관련
4. Ticket Models: 티켓 관련
5. Kafka Event Models: 이벤트 메시지
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


# ============================================================
# 열거형 (Enums)
# ============================================================

class TicketCategory(str, Enum):
    """
    티켓 카테고리 종류
    
    Classifier Agent가 분류한 결과값
    """
    BILLING = "billing"       # 결제/청구 관련
    TECHNICAL = "technical"   # 기술적 문제
    GENERAL = "general"       # 일반 문의
    COMPLAINT = "complaint"   # 불만/컴플레인
    OTHER = "other"           # 기타


class TicketPriority(str, Enum):
    """
    티켓 우선순위
    
    Classifier Agent가 분류한 결과값
    urgent > high > medium > low
    """
    LOW = "low"          # 낮음: 일반 문의, 마이너 이슈
    MEDIUM = "medium"    # 중간: 기능 불편
    HIGH = "high"        # 높음: 주요 기능 장애
    URGENT = "urgent"    # 긴급: 시스템 다운, 보안 이슈


class TicketStatus(str, Enum):
    """
    티켓 처리 상태
    
    워크플로우 진행 상황 추적용
    Redis에 저장되어 클라이언트 폴링에 사용
    """
    PENDING = "pending"           # 대기 중 (0%)
    CLASSIFYING = "classifying"   # 분류 중 (25%)
    GENERATING = "generating"     # 응답 생성 중 (50%)
    VALIDATING = "validating"     # 검증 중 (75%)
    COMPLETED = "completed"       # 처리 완료 (100%)
    ESCALATED = "escalated"       # 상담사 에스컬레이션 (100%)
    FAILED = "failed"             # 처리 실패


class UserRole(str, Enum):
    """
    사용자 역할
    
    권한 관리에 사용:
    - customer: 일반 고객 (자신의 티켓만 조회)
    - agent: 상담사 (할당된 티켓 관리)
    - admin: 관리자 (전체 시스템 접근)
    """
    CUSTOMER = "customer"
    AGENT = "agent"
    ADMIN = "admin"


# ============================================================
# 사용자 모델 (User Models)
# ============================================================

class UserBase(BaseModel):
    """사용자 기본 모델"""
    email: str
    name: str


class UserCreate(UserBase):
    """사용자 생성 요청 모델 (회원가입)"""
    password: str


class UserResponse(UserBase):
    """
    사용자 응답 모델
    
    비밀번호 제외한 사용자 정보 반환
    """
    id: str
    role: UserRole
    created_at: datetime
    
    class Config:
        from_attributes = True  # ORM 모델에서 변환 허용


# ============================================================
# 인증 모델 (Auth Models)
# ============================================================

class LoginRequest(BaseModel):
    """로그인 요청 모델"""
    email: str
    password: str


class TokenResponse(BaseModel):
    """
    토큰 응답 모델 (로그인 성공 시)
    
    - access_token: API 요청에 사용 (15분)
    - refresh_token: Access Token 갱신에 사용 (7일)
    - token_type: Bearer
    - expires_in: 만료 시간 (초 단위)
    """
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenPayload(BaseModel):
    """
    JWT 토큰 페이로드 (디코딩 결과)
    
    JWT 내부에 포함된 정보:
    - sub: Subject (사용자 ID)
    - email: 이메일
    - role: 역할
    - jti: JWT ID (고유 식별자, 블랙리스트용)
    - exp: 만료 시간
    - iat: 발급 시간
    """
    sub: str        # user_id
    email: str
    role: UserRole
    jti: str        # JWT ID
    exp: datetime   # Expiration
    iat: datetime   # Issued At


# ============================================================
# 티켓 모델 (Ticket Models)
# ============================================================

class TicketCreate(BaseModel):
    """
    티켓 생성 요청 모델
    
    클라이언트가 보내는 문의 내용
    """
    content: str = Field(..., min_length=1, max_length=5000)
    metadata: Optional[dict] = None  # 추가 정보 (브라우저, OS 등)


class TicketResponse(BaseModel):
    """
    티켓 응답 모델
    
    티켓의 전체 정보 반환
    AI 처리 결과 포함
    """
    id: str
    user_id: str
    content: str
    category: Optional[TicketCategory] = None    # AI 분류 결과
    priority: Optional[TicketPriority] = None    # AI 분류 결과
    status: TicketStatus
    response: Optional[str] = None               # AI 생성 응답
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class TicketStatusResponse(BaseModel):
    """
    티켓 상태 응답 모델 (폴링용)
    
    클라이언트가 처리 진행 상황을 확인할 때 사용
    Redis에서 조회
    """
    ticket_id: str
    stage: TicketStatus
    progress: int = Field(ge=0, le=100)  # 진행률 (0~100%)
    updated_at: datetime


# ============================================================
# Kafka 이벤트 모델
# ============================================================

class TicketCreatedEvent(BaseModel):
    """
    티켓 생성 이벤트
    
    Gateway → Kafka → Orchestrator
    
    티켓 생성 시 발행되어 AI 처리 트리거
    """
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = "ticket_created"
    ticket_id: str
    user_id: str
    content: str
    metadata: Optional[dict] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentResultEvent(BaseModel):
    """
    에이전트 처리 결과 이벤트
    
    Orchestrator → Kafka → Gateway
    
    AI 처리 완료 시 발행되어 DB 업데이트 트리거
    """
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = "agent_result"
    ticket_id: str
    category: TicketCategory
    priority: TicketPriority
    response: str               # AI 생성 응답
    quality_score: float        # 품질 점수
    status: TicketStatus
    completed_at: datetime = Field(default_factory=datetime.utcnow)
