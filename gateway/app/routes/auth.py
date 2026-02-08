"""
인증 라우터 (Authentication Routes)
- 회원가입, 로그인, 로그아웃, 토큰 갱신 처리
"""
from datetime import datetime
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from shared import (
    get_redis_client,
    get_settings,
    LoginRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
    UserRole,
)
from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_token_payload,
)
from shared import TokenPayload


router = APIRouter()

# ============== 임시 인메모리 DB (TODO: PostgreSQL로 교체) ==============
fake_users_db: dict[str, dict] = {}


class RegisterRequest(BaseModel):
    """회원가입 요청 스키마"""
    email: EmailStr
    password: str
    name: str


class RefreshRequest(BaseModel):
    """토큰 갱신 요청 스키마"""
    refresh_token: str


# ============== 회원가입 ==============
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest):
    """
    새 사용자 등록
    
    1. 이메일 중복 확인
    2. 비밀번호 해싱
    3. 사용자 DB 저장
    """
    # 이메일 중복 확인
    if request.email in fake_users_db:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered"
        )
    
    # 새 사용자 생성
    user_id = str(uuid.uuid4())  # UUID v4로 고유 ID 생성
    now = datetime.utcnow()
    
    user = {
        "id": user_id,
        "email": request.email,
        "name": request.name,
        "password_hash": hash_password(request.password),  # bcrypt로 암호화
        "role": UserRole.CUSTOMER,  # 기본 역할: 고객
        "created_at": now,
    }
    
    # DB 저장 (현재는 인메모리)
    fake_users_db[request.email] = user
    
    return UserResponse(
        id=user_id,
        email=request.email,
        name=request.name,
        role=UserRole.CUSTOMER,
        created_at=now,
    )


# ============== 로그인 ==============
@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """
    로그인 및 JWT 토큰 발급
    
    1. 이메일로 사용자 조회
    2. 비밀번호 검증
    3. Access Token 생성 (15분)
    4. Refresh Token 생성 (7일) → Redis 저장
    """
    settings = get_settings()
    redis = await get_redis_client()
    
    # 사용자 조회
    user = fake_users_db.get(request.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    # 비밀번호 검증 (bcrypt)
    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    # Access Token 생성 (짧은 수명, 요청마다 사용)
    access_token, access_jti, access_exp = create_access_token(
        user_id=user["id"],
        email=user["email"],
        role=user["role"]
    )
    
    # Refresh Token 생성 (긴 수명, Access Token 갱신용)
    refresh_token, refresh_jti, refresh_exp = create_refresh_token(user["id"])
    
    # Refresh Token을 Redis에 저장 (로그아웃 시 삭제용)
    # Redis Key: refresh:{user_id}:{jti} → Token 값 (TTL: 7일)
    await redis.set_refresh_token(
        user_id=user["id"],
        jti=refresh_jti,
        token=refresh_token,
        expires_days=settings.jwt_refresh_token_expire_days
    )
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60  # 초 단위
    )


# ============== 토큰 갱신 ==============
@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: RefreshRequest):
    """
    Refresh Token으로 새 Access Token 발급
    
    1. Refresh Token 디코딩/검증
    2. 새 Access Token 생성
    3. 새 Refresh Token 생성 (Token Rotation)
    4. 기존 Refresh Token 삭제 (Redis)
    """
    settings = get_settings()
    redis = await get_redis_client()
    
    # Refresh Token 디코딩
    try:
        payload = get_token_payload(request.refresh_token)
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    
    # 사용자 조회
    user = None
    for u in fake_users_db.values():
        if u["id"] == payload.sub:
            user = u
            break
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    # 새 Access Token 생성
    access_token, access_jti, access_exp = create_access_token(
        user_id=user["id"],
        email=user["email"],
        role=user["role"]
    )
    
    # 새 Refresh Token 생성 (Token Rotation: 보안 강화)
    refresh_token, refresh_jti, refresh_exp = create_refresh_token(user["id"])
    
    # 새 Refresh Token Redis 저장
    await redis.set_refresh_token(
        user_id=user["id"],
        jti=refresh_jti,
        token=refresh_token,
        expires_days=settings.jwt_refresh_token_expire_days
    )
    
    # 기존 Refresh Token 삭제 (재사용 방지)
    await redis.delete_refresh_token(user["id"], payload.jti)
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60
    )


# ============== 로그아웃 ==============
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: Annotated[TokenPayload, Depends(get_current_user)]):
    """
    로그아웃 - Access Token 무효화
    
    JWT는 stateless라서 서버에서 직접 무효화 불가
    → Redis 블랙리스트에 추가하여 무효화
    
    Redis Key: blacklist:{jti} → "1" (TTL: 토큰 남은 시간)
    """
    redis = await get_redis_client()
    
    # Access Token 남은 시간 계산
    remaining_ttl = int((user.exp - datetime.utcnow()).total_seconds())
    
    # 블랙리스트에 추가 (남은 시간만큼만 저장)
    if remaining_ttl > 0:
        await redis.add_to_blacklist(user.jti, remaining_ttl)
    
    return None


# ============== 현재 사용자 정보 조회 ==============
@router.get("/me", response_model=UserResponse)
async def get_me(user: Annotated[TokenPayload, Depends(get_current_user)]):
    """
    현재 로그인한 사용자 정보 반환
    
    JWT 토큰에서 user_id 추출 → DB에서 사용자 조회
    """
    # 사용자 조회
    for u in fake_users_db.values():
        if u["id"] == user.sub:
            return UserResponse(
                id=u["id"],
                email=u["email"],
                name=u["name"],
                role=u["role"],
                created_at=u["created_at"],
            )
    
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="User not found"
    )
