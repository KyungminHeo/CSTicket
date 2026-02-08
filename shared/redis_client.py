"""
Redis 클라이언트 유틸리티
멀티-AI 에이전트 시스템에서 사용하는 Redis 기능들

주요 기능:
1. JWT 토큰 관리 (Refresh Token 저장, Access Token 블랙리스트)
2. Rate Limiting (분당 요청 제한)
3. 티켓 처리 상태 (실시간 폴링용)
4. LangGraph 상태 저장 (체크포인트)
5. 응답 캐싱 (유사 질문 재사용)
"""
import json
from datetime import timedelta
from typing import Any, Optional
import redis.asyncio as redis
from .config import get_settings


class RedisClient:
    """
    비동기 Redis 클라이언트 래퍼
    
    싱글톤 패턴으로 전역에서 하나의 연결만 유지
    """
    
    def __init__(self):
        self.settings = get_settings()
        self._client: Optional[redis.Redis] = None
    
    async def connect(self) -> None:
        """Redis 서버에 연결"""
        self._client = redis.from_url(
            self.settings.redis_url,  # redis://localhost:6379/0
            encoding="utf-8",
            decode_responses=True  # 바이트 대신 문자열 반환
        )
    
    async def disconnect(self) -> None:
        """Redis 연결 해제"""
        if self._client:
            await self._client.close()
    
    @property
    def client(self) -> redis.Redis:
        """Redis 클라이언트 인스턴스 반환"""
        if not self._client:
            raise RuntimeError("Redis client not connected. Call connect() first.")
        return self._client
    
    # ============================================================
    # 1. JWT 토큰 관리
    # ============================================================
    
    async def set_refresh_token(self, user_id: str, jti: str, token: str, expires_days: int = 7) -> None:
        """
        Refresh Token을 Redis에 저장
        
        로그인 시 호출됨
        - Key: refresh:{user_id}:{jti}
        - Value: 토큰 문자열
        - TTL: 7일 (기본값)
        
        사용 목적: 로그아웃 시 토큰 삭제하여 무효화
        """
        key = f"refresh:{user_id}:{jti}"
        await self.client.setex(key, timedelta(days=expires_days), token)
    
    async def get_refresh_token(self, user_id: str, jti: str) -> Optional[str]:
        """
        Refresh Token 조회
        
        토큰 갱신 시 유효성 검증용
        """
        key = f"refresh:{user_id}:{jti}"
        return await self.client.get(key)
    
    async def delete_refresh_token(self, user_id: str, jti: str) -> None:
        """
        Refresh Token 삭제 (로그아웃)
        
        Token Rotation 시에도 기존 토큰 삭제
        """
        key = f"refresh:{user_id}:{jti}"
        await self.client.delete(key)
    
    async def add_to_blacklist(self, jti: str, expires_seconds: int) -> None:
        """
        Access Token을 블랙리스트에 추가
        
        로그아웃 시 호출됨
        - Key: blacklist:{jti}
        - Value: "1"
        - TTL: 토큰의 남은 유효시간
        
        JWT는 stateless라서 서버에서 직접 무효화 불가
        → 블랙리스트로 "논리적 무효화" 구현
        """
        key = f"blacklist:{jti}"
        await self.client.setex(key, expires_seconds, "1")
    
    async def is_blacklisted(self, jti: str) -> bool:
        """
        Access Token이 블랙리스트에 있는지 확인
        
        모든 인증된 요청에서 호출됨
        True면 요청 거부 (로그아웃된 토큰)
        """
        key = f"blacklist:{jti}"
        return await self.client.exists(key) > 0
    
    # ============================================================
    # 2. Rate Limiting (요청 제한)
    # ============================================================
    
    async def check_rate_limit(self, identifier: str, limit: int = 60, window: int = 60) -> tuple[bool, int]:
        """
        슬라이딩 윈도우 기반 Rate Limiting
        
        Args:
            identifier: 제한 대상 (user:{user_id} 또는 ip:{ip_address})
            limit: 윈도우 내 최대 요청 수 (기본: 60회)
            window: 윈도우 크기 (초, 기본: 60초)
        
        Returns:
            (is_allowed, remaining_requests): (허용 여부, 남은 요청 수)
        
        동작 방식:
        1. INCR rate:{identifier} → 카운터 증가
        2. 첫 요청이면 EXPIRE 설정 (1분 후 자동 삭제)
        3. 카운터 > limit 이면 요청 거부
        
        Redis Key: rate:{identifier} → 카운터 (TTL: 1분)
        """
        key = f"rate:{identifier}"
        current = await self.client.incr(key)
        
        # 첫 번째 요청이면 만료 시간 설정
        if current == 1:
            await self.client.expire(key, window)
        
        remaining = max(0, limit - current)
        is_allowed = current <= limit
        
        return is_allowed, remaining
    
    # ============================================================
    # 3. 티켓 처리 상태 (실시간 추적)
    # ============================================================
    
    async def set_ticket_status(self, ticket_id: str, stage: str, progress: int = 0) -> None:
        """
        티켓 처리 상태 업데이트
        
        Orchestrator에서 각 단계 완료 시 호출
        - Key: status:{ticket_id}
        - Value: Hash {stage, progress, updated_at}
        - TTL: 1시간
        
        stage 값:
        - pending: 대기 중
        - classifying: 분류 중 (25%)
        - generating: 응답 생성 중 (50%)
        - validating: 검증 중 (75%)
        - completed: 완료 (100%)
        - escalated: 에스컬레이션 (100%)
        """
        key = f"status:{ticket_id}"
        await self.client.hset(key, mapping={
            "stage": stage,
            "progress": str(progress),
            "updated_at": str(int(__import__("time").time()))
        })
        await self.client.expire(key, 3600)  # 1시간 TTL
    
    async def get_ticket_status(self, ticket_id: str) -> Optional[dict]:
        """
        티켓 처리 상태 조회
        
        Gateway의 /tickets/{id}/status 엔드포인트에서 호출
        클라이언트가 폴링하여 진행 상황 표시
        """
        key = f"status:{ticket_id}"
        data = await self.client.hgetall(key)
        if data:
            return {
                "stage": data.get("stage"),
                "progress": int(data.get("progress", 0)),
                "updated_at": int(data.get("updated_at", 0))
            }
        return None
    
    # ============================================================
    # 4. LangGraph 상태 저장 (체크포인트)
    # ============================================================
    
    async def save_agent_state(self, ticket_id: str, state: dict, ttl: int = 3600) -> None:
        """
        LangGraph 워크플로우 상태 저장
        
        각 노드 실행 후 호출 → 장애 복구용 체크포인트
        - Key: lg:state:{ticket_id}
        - Value: JSON 문자열 (TicketState 전체)
        - TTL: 1시간
        
        사용 목적:
        - 서버 재시작 시 진행 중이던 워크플로우 복구
        - 디버깅 시 중간 상태 확인
        """
        key = f"lg:state:{ticket_id}"
        await self.client.setex(key, ttl, json.dumps(state))
    
    async def get_agent_state(self, ticket_id: str) -> Optional[dict]:
        """
        LangGraph 워크플로우 상태 조회
        
        서버 재시작 후 미완료 티켓 복구 시 사용
        """
        key = f"lg:state:{ticket_id}"
        data = await self.client.get(key)
        if data:
            return json.loads(data)
        return None
    
    async def delete_agent_state(self, ticket_id: str) -> None:
        """
        LangGraph 워크플로우 상태 삭제
        
        처리 완료 후 호출하여 정리
        """
        key = f"lg:state:{ticket_id}"
        await self.client.delete(key)
    
    # ============================================================
    # 5. 응답 캐싱 (선택적 최적화)
    # ============================================================
    
    async def get_cached_response(self, content_hash: str) -> Optional[str]:
        """
        유사 문의에 대한 캐시된 응답 조회
        
        content_hash: 문의 내용의 해시값
        동일/유사한 질문이 들어오면 LLM 재호출 없이 캐시 반환
        """
        key = f"cache:resp:{content_hash}"
        return await self.client.get(key)
    
    async def set_cached_response(self, content_hash: str, response: str, ttl: int = 1800) -> None:
        """
        응답 캐시 저장
        
        - Key: cache:resp:{content_hash}
        - Value: AI 생성 응답
        - TTL: 30분 (기본값)
        
        사용 목적: LLM API 비용 절감, 응답 시간 단축
        """
        key = f"cache:resp:{content_hash}"
        await self.client.setex(key, ttl, response)


# ============================================================
# 싱글톤 인스턴스 관리
# ============================================================

_redis_client: Optional[RedisClient] = None


async def get_redis_client() -> RedisClient:
    """
    Redis 클라이언트 싱글톤 반환
    
    전역에서 하나의 연결만 유지하여 리소스 절약
    Gateway/Orchestrator 시작 시 한 번 연결
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient()
        await _redis_client.connect()
    return _redis_client
