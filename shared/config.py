"""
공통 설정 모듈 (Shared Config)
환경변수를 로드하고 애플리케이션 설정 제공

Pydantic Settings를 사용하여:
- .env 파일에서 설정 로드
- 기본값 제공
- 타입 검증 자동 수행
"""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    애플리케이션 설정 클래스
    
    환경변수에서 값을 로드 (없으면 기본값 사용)
    환경변수 이름은 필드명과 동일 (대소문자 구분 없음)
    
    예: POSTGRES_HOST 환경변수 → postgres_host 필드
    """
    
    # ============================================================
    # 환경 설정
    # ============================================================
    environment: str = "development"  # development, staging, production
    
    # ============================================================
    # PostgreSQL (데이터베이스)
    # ============================================================
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "customer_support"
    postgres_user: str = "postgres"
    postgres_password: str = "postgres123"
    
    # ============================================================
    # Redis (캐싱, 세션, Rate Limit)
    # ============================================================
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    
    # ============================================================
    # Kafka (이벤트 메시징)
    # ============================================================
    kafka_bootstrap_servers: str = "localhost:9092"
    
    # ============================================================
    # Qdrant (Vector DB)
    # ============================================================
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    
    # ============================================================
    # JWT 인증
    # ============================================================
    jwt_secret_key: str = "your-super-secret-key-change-in-production"  # 프로덕션에서 변경 필수!
    jwt_algorithm: str = "HS256"                # 서명 알고리즘
    jwt_access_token_expire_minutes: int = 15   # Access Token 만료: 15분
    jwt_refresh_token_expire_days: int = 7      # Refresh Token 만료: 7일
    
    # ============================================================
    # OpenAI (LLM API)
    # ============================================================
    openai_api_key: str = ""           # OpenAI API 키 (필수)
    openai_model: str = "gpt-4"        # 사용할 모델
    
    # ============================================================
    # Rate Limiting
    # ============================================================
    rate_limit_per_minute: int = 60    # 분당 최대 요청 수
    
    # ============================================================
    # 계산된 속성 (Property)
    # ============================================================
    
    @property
    def postgres_url(self) -> str:
        """
        PostgreSQL 비동기 연결 URL
        
        asyncpg 드라이버 사용 (비동기 지원)
        형식: postgresql+asyncpg://user:pass@host:port/db
        """
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
    
    @property
    def postgres_url_sync(self) -> str:
        """
        PostgreSQL 동기 연결 URL
        
        마이그레이션 등 동기 작업용
        형식: postgresql://user:pass@host:port/db
        """
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
    
    @property
    def redis_url(self) -> str:
        """
        Redis 연결 URL
        
        형식: redis://[:password@]host:port/db_number
        """
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/0"
        return f"redis://{self.redis_host}:{self.redis_port}/0"
    
    class Config:
        """Pydantic 설정"""
        env_file = ".env"           # .env 파일에서 로드
        env_file_encoding = "utf-8"


# ============================================================
# 싱글톤 설정 인스턴스
# ============================================================

@lru_cache()
def get_settings() -> Settings:
    """
    설정 인스턴스 반환 (캐시됨)
    
    lru_cache로 한 번만 로드하고 재사용
    매번 .env 파일을 읽지 않아 성능 향상
    """
    return Settings()
