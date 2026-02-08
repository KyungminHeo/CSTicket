"""
Shared 패키지
Gateway와 Orchestrator에서 공통으로 사용하는 유틸리티 모음

주요 모듈:
- config: 환경 설정 관리
- models: Pydantic 데이터 모델
- redis_client: Redis 클라이언트
- kafka_client: Kafka 클라이언트
"""
from .config import get_settings, Settings
from .models import (
    # Enums (열거형)
    TicketCategory,
    TicketPriority,
    TicketStatus,
    UserRole,
    # User 모델
    UserBase,
    UserCreate,
    UserResponse,
    # Auth 모델
    LoginRequest,
    TokenResponse,
    TokenPayload,
    # Ticket 모델
    TicketCreate,
    TicketResponse,
    TicketStatusResponse,
    # Kafka 이벤트 모델
    TicketCreatedEvent,
    AgentResultEvent,
)
from .redis_client import RedisClient, get_redis_client
from .kafka_client import (
    KafkaProducerClient,
    KafkaConsumerClient,
    get_kafka_producer,
    TOPIC_TICKET_EVENTS,
    TOPIC_AGENT_RESULTS,
    TOPIC_DEAD_LETTER,
)

__all__ = [
    # 설정
    "get_settings",
    "Settings",
    # 열거형
    "TicketCategory",
    "TicketPriority", 
    "TicketStatus",
    "UserRole",
    # User 모델
    "UserBase",
    "UserCreate",
    "UserResponse",
    # Auth 모델
    "LoginRequest",
    "TokenResponse",
    "TokenPayload",
    # Ticket 모델
    "TicketCreate",
    "TicketResponse",
    "TicketStatusResponse",
    # Kafka 이벤트 모델
    "TicketCreatedEvent",
    "AgentResultEvent",
    # Redis
    "RedisClient",
    "get_redis_client",
    # Kafka
    "KafkaProducerClient",
    "KafkaConsumerClient",
    "get_kafka_producer",
    "TOPIC_TICKET_EVENTS",
    "TOPIC_AGENT_RESULTS",
    "TOPIC_DEAD_LETTER",
]
