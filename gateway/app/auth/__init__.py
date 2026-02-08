"""
인증 모듈 (Auth Module)

주요 컴포넌트:
- jwt_handler: JWT 토큰 생성/검증, 비밀번호 해싱
- dependencies: FastAPI 인증 의존성 (라우트 보호용)
"""
from .jwt_handler import (
    hash_password,         # 비밀번호 해싱
    verify_password,       # 비밀번호 검증
    create_access_token,   # Access Token 생성
    create_refresh_token,  # Refresh Token 생성
    decode_token,          # JWT 디코딩
    get_token_payload,     # 페이로드 추출
)
from .dependencies import (
    get_current_user,      # 인증 필수 의존성
    get_current_customer,  # 모든 역할 허용
    get_current_agent,     # 상담사/관리자만
    get_current_admin,     # 관리자만
    require_role,          # 커스텀 역할 검사
)

__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_token_payload",
    "get_current_user",
    "get_current_customer",
    "get_current_agent",
    "get_current_admin",
    "require_role",
]
