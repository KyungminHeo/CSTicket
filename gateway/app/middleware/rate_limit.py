"""
Rate Limiting 미들웨어
Redis 슬라이딩 윈도우 기반 요청 제한

동작 방식:
1. 요청 식별자 결정 (인증된 사용자 ID 또는 IP)
2. Redis에서 현재 카운터 확인
3. 제한 초과 시 429 응답
4. 응답 헤더에 Rate Limit 정보 포함
"""
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from shared import get_redis_client, get_settings


# ============================================================
# Rate Limiting 제외 경로
# ============================================================

# 이 경로들은 Rate Limiting 적용 안 함
EXCLUDED_PATHS = {
    "/health",          # 헬스체크
    "/docs",            # Swagger UI
    "/openapi.json",    # OpenAPI 스키마
    "/redoc"            # ReDoc
}


# ============================================================
# Rate Limit 미들웨어
# ============================================================

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Redis 슬라이딩 윈도우 기반 Rate Limiting
    
    설정:
    - 분당 60회 요청 제한 (기본값, 환경변수로 변경 가능)
    - 인증된 사용자: user:{user_id} 기준
    - 비인증 사용자: ip:{ip_address} 기준
    
    응답 헤더:
    - X-RateLimit-Limit: 분당 최대 요청 수
    - X-RateLimit-Remaining: 남은 요청 수
    - Retry-After: 제한 초과 시 재시도 대기 시간 (초)
    """
    
    async def dispatch(self, request: Request, call_next) -> Response:
        """
        미들웨어 핸들러
        
        모든 요청이 이 함수를 통과
        """
        # 제외 경로는 Rate Limiting 건너뜀
        if request.url.path in EXCLUDED_PATHS:
            return await call_next(request)
        
        settings = get_settings()
        redis = await get_redis_client()
        
        # 요청 식별자 결정 (user_id 또는 IP)
        identifier = self._get_identifier(request)
        
        # Redis에서 Rate Limit 확인
        is_allowed, remaining = await redis.check_rate_limit(
            identifier,
            limit=settings.rate_limit_per_minute,  # 분당 제한
            window=60  # 1분 윈도우
        )
        
        # 제한 초과 시 429 응답
        if not is_allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please try again later.",
                headers={
                    "X-RateLimit-Limit": str(settings.rate_limit_per_minute),
                    "X-RateLimit-Remaining": "0",
                    "Retry-After": "60"  # 60초 후 재시도
                }
            )
        
        # 정상 요청 처리
        response = await call_next(request)
        
        # Rate Limit 헤더 추가 (클라이언트가 남은 요청 수 확인 가능)
        response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        
        return response
    
    def _get_identifier(self, request: Request) -> str:
        """
        Rate Limiting 식별자 결정
        
        우선순위:
        1. 인증된 사용자: user:{user_id}
        2. 비인증: ip:{ip_address}
        
        user_id 기준이면 여러 기기에서 같은 제한 적용
        IP 기준이면 NAT 환경에서 여러 사용자가 같은 제한 공유
        """
        # 인증된 사용자 확인 (get_current_user 의존성이 설정한 값)
        if hasattr(request.state, "user"):
            return f"user:{request.state.user.sub}"
        
        # IP 주소 추출 (프록시 뒤에 있을 경우 X-Forwarded-For 사용)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # X-Forwarded-For: client, proxy1, proxy2
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"
        
        return f"ip:{ip}"
