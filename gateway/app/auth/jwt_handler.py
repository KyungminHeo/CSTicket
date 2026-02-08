"""
JWT 인증 유틸리티
- 비밀번호 해싱 (bcrypt)
- Access Token / Refresh Token 생성
- 토큰 디코딩 및 검증
"""
from datetime import datetime, timedelta
from typing import Optional
import uuid

import jwt
import bcrypt
from fastapi import HTTPException, status

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from shared import get_settings, TokenPayload, UserRole


# 전역 설정 로드
settings = get_settings()


# ============================================================
# 비밀번호 해싱 (bcrypt)
# ============================================================

def hash_password(password: str) -> str:
    """
    비밀번호를 bcrypt로 해싱
    
    bcrypt 특징:
    - 솔트(salt) 자동 생성 → Rainbow Table 공격 방어
    - 느린 해싱 → 무차별 대입 공격 방어
    - 출력 형식: $2b$12$솔트+해시 (60자)
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    입력 비밀번호가 저장된 해시와 일치하는지 검증
    
    로그인 시 사용 - 일치하면 True
    """
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


# ============================================================
# Access Token (단기 토큰)
# ============================================================

def create_access_token(user_id: str, email: str, role: UserRole) -> tuple[str, str, datetime]:
    """
    JWT Access Token 생성
    
    특징:
    - 짧은 수명: 15분 (설정 가능)
    - 매 API 요청마다 헤더에 포함
    - Stateless: 서버에 저장 안 함
    
    Payload 구조:
    {
        "sub": "user-uuid",      # Subject: 사용자 ID
        "email": "user@test.com",
        "role": "customer",
        "jti": "uuid",           # JWT ID: 고유 식별자 (블랙리스트용)
        "iat": 1707200000,       # Issued At: 발급 시간
        "exp": 1707200900,       # Expiration: 만료 시간
        "type": "access"
    }
    
    Returns:
        (token, jti, expiration): 토큰 문자열, JWT ID, 만료 시간
    """
    jti = str(uuid.uuid4())  # JWT ID: 나중에 블랙리스트 등록 시 사용
    now = datetime.utcnow()
    exp = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    
    payload = {
        "sub": user_id,       # Subject: 누구의 토큰인가
        "email": email,
        "role": role.value,   # Enum → 문자열
        "jti": jti,           # JWT ID: 토큰 고유 식별자
        "iat": now,           # Issued At: 언제 발급했나
        "exp": exp,           # Expiration: 언제 만료되나
        "type": "access"      # 토큰 종류 구분
    }
    
    # HS256 알고리즘으로 서명
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, exp


# ============================================================
# Refresh Token (장기 토큰)
# ============================================================

def create_refresh_token(user_id: str) -> tuple[str, str, datetime]:
    """
    JWT Refresh Token 생성
    
    특징:
    - 긴 수명: 7일 (설정 가능)
    - Access Token 갱신 용도
    - Redis에 저장됨 (로그아웃 시 삭제 가능)
    
    Payload 구조:
    {
        "sub": "user-uuid",
        "jti": "uuid",
        "iat": 1707200000,
        "exp": 1707804800,
        "type": "refresh"
    }
    
    Returns:
        (token, jti, expiration): 토큰 문자열, JWT ID, 만료 시간
    """
    jti = str(uuid.uuid4())
    now = datetime.utcnow()
    exp = now + timedelta(days=settings.jwt_refresh_token_expire_days)
    
    payload = {
        "sub": user_id,
        "jti": jti,
        "iat": now,
        "exp": exp,
        "type": "refresh"
    }
    
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, exp


# ============================================================
# 토큰 디코딩 및 검증
# ============================================================

def decode_token(token: str) -> dict:
    """
    JWT 토큰 디코딩 및 검증
    
    검증 항목:
    1. 서명 유효성 (jwt_secret_key로 검증)
    2. 만료 시간 (exp)
    
    Raises:
        HTTPException 401: 토큰 만료 또는 유효하지 않음
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm]
        )
        return payload
    except jwt.ExpiredSignatureError:
        # 토큰 만료됨
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        # 서명 불일치, 형식 오류 등
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_token_payload(token: str) -> TokenPayload:
    """
    JWT에서 타입이 지정된 페이로드 추출
    
    일반 dict 대신 TokenPayload Pydantic 모델로 반환
    → 타입 안전성 보장
    """
    payload = decode_token(token)
    return TokenPayload(
        sub=payload["sub"],
        email=payload["email"],
        role=UserRole(payload["role"]),
        jti=payload["jti"],
        exp=datetime.fromtimestamp(payload["exp"]),
        iat=datetime.fromtimestamp(payload["iat"]),
    )
