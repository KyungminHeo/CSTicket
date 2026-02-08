"""
라우터 패키지 (Routes Package)

API 엔드포인트:
- auth: 인증 관련 (/api/v1/auth/*)
- tickets: 티켓 관련 (/api/v1/tickets/*)
"""
from . import auth, tickets

__all__ = ["auth", "tickets"]
