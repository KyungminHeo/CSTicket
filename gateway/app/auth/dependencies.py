"""
인증 의존성 (Auth Dependencies)
라우트 보호를 위한 FastAPI 의존성 함수들

주요 기능:
- JWT 토큰 검증
- 블랙리스트 확인
- 역할 기반 접근 제어 (RBAC)
"""
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from shared import get_redis_client, TokenPayload, UserRole
from app.auth.jwt_handler import get_token_payload


# ============================================================
# HTTP Bearer 스키마
# ============================================================

# Swagger UI에서 "Authorize" 버튼 표시
# 요청 헤더: Authorization: Bearer <token>
security = HTTPBearer()


# ============================================================
# 사용자 인증 의존성
# ============================================================

async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]
) -> TokenPayload:
    """
    현재 인증된 사용자 가져오기 (JWT 검증)
    
    검증 단계:
    1. Authorization 헤더에서 토큰 추출
    2. JWT 서명/만료 검증
    3. Redis 블랙리스트 확인 (로그아웃된 토큰)
    
    사용법:
        @router.get("/protected")
        async def protected_route(user: Annotated[TokenPayload, Depends(get_current_user)]):
            return {"user_id": user.sub}
    
    Raises:
        HTTPException 401: 토큰 무효, 만료, 또는 블랙리스트
    """
    token = credentials.credentials
    
    # JWT 토큰 디코딩 및 검증
    payload = get_token_payload(token)
    
    # Redis 블랙리스트 확인 (로그아웃된 토큰 차단)
    redis = await get_redis_client()
    if await redis.is_blacklisted(payload.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # 다른 미들웨어/의존성에서 접근할 수 있도록 저장
    request.state.user = payload
    
    return payload


# ============================================================
# 역할 기반 접근 제어 (RBAC)
# ============================================================

async def require_role(required_roles: list[UserRole]):
    """
    특정 역할 필수 의존성 팩토리
    
    사용법:
        @router.get("/admin", dependencies=[Depends(require_role([UserRole.ADMIN]))])
        async def admin_only():
            return {"message": "Admin only"}
    """
    async def role_checker(user: Annotated[TokenPayload, Depends(get_current_user)]):
        if user.role not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return user
    return role_checker


# ============================================================
# 편의 의존성 (역할별)
# ============================================================

async def get_current_customer(
    user: Annotated[TokenPayload, Depends(get_current_user)]
) -> TokenPayload:
    """
    인증된 사용자 반환 (모든 역할 허용)
    
    customer, agent, admin 모두 접근 가능
    """
    return user


async def get_current_agent(
    user: Annotated[TokenPayload, Depends(get_current_user)]
) -> TokenPayload:
    """
    상담사 또는 관리자만 접근 허용
    
    Raises:
        HTTPException 403: customer 역할인 경우
    """
    if user.role not in [UserRole.AGENT, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent or admin access required"
        )
    return user


async def get_current_admin(
    user: Annotated[TokenPayload, Depends(get_current_user)]
) -> TokenPayload:
    """
    관리자만 접근 허용
    
    Raises:
        HTTPException 403: admin이 아닌 경우
    """
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user
