"""
API Gateway 메인 애플리케이션
FastAPI 기반 REST API 서버

주요 기능:
- 인증 API (/api/v1/auth/*)
- 티켓 API (/api/v1/tickets/*)
- Rate Limiting (Redis 기반)
- CORS 설정

실행 방법:
    cd gateway
    uvicorn app.main:app --reload
"""
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# shared 패키지 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from shared import get_redis_client, get_kafka_producer, get_settings
from app.routes import auth, tickets
from app.middleware.rate_limit import RateLimitMiddleware


# ============================================================
# 애플리케이션 수명주기 관리
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    애플리케이션 수명주기 핸들러
    
    시작 시 (Startup):
    - Redis 연결
    - Kafka Producer 시작
    
    종료 시 (Shutdown):
    - Redis 연결 해제
    - Kafka Producer 정지
    """
    # ========== Startup ==========
    print("🚀 Starting API Gateway...")
    
    # Redis 연결 (세션, Rate Limit, 상태 저장)
    redis_client = await get_redis_client()
    app.state.redis = redis_client
    print("✅ Redis connected")
    
    # Kafka Producer 시작 (이벤트 발행)
    kafka_producer = await get_kafka_producer()
    app.state.kafka = kafka_producer
    print("✅ Kafka producer started")
    
    # yield 이후는 Shutdown 시 실행됨
    yield
    
    # ========== Shutdown ==========
    print("🛑 Shutting down API Gateway...")
    await redis_client.disconnect()
    await kafka_producer.stop()
    print("✅ Cleanup complete")


# ============================================================
# 애플리케이션 팩토리
# ============================================================

def create_app() -> FastAPI:
    """
    FastAPI 애플리케이션 생성 및 설정
    
    설정 항목:
    1. 메타데이터 (제목, 설명, 버전)
    2. CORS 미들웨어
    3. Rate Limit 미들웨어
    4. 라우터 등록
    """
    settings = get_settings()
    
    # FastAPI 인스턴스 생성
    app = FastAPI(
        title="Customer Support API Gateway",
        description="Multi-AI Agent Customer Support System",
        version="1.0.0",
        lifespan=lifespan,
    )
    
    # ========== 미들웨어 설정 ==========
    
    # CORS (Cross-Origin Resource Sharing)
    # 프로덕션에서는 allow_origins 제한 필요
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: 프로덕션에서 도메인 지정
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Rate Limiting (분당 요청 제한)
    app.add_middleware(RateLimitMiddleware)
    
    # ========== 라우터 등록 ==========
    
    # 인증 라우터: /api/v1/auth/*
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
    
    # 티켓 라우터: /api/v1/tickets/*
    app.include_router(tickets.router, prefix="/api/v1/tickets", tags=["Tickets"])
    
    # ========== 헬스체크 ==========
    
    @app.get("/health")
    async def health_check():
        """
        헬스체크 엔드포인트
        
        로드밸런서/쿠버네티스가 서버 상태 확인용으로 사용
        """
        return {"status": "healthy", "service": "gateway"}
    
    return app


# ============================================================
# 애플리케이션 인스턴스
# ============================================================

app = create_app()


if __name__ == "__main__":
    import uvicorn
    # 개발 서버 실행 (reload 모드)
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
