"""
LangGraph 워크플로우 정의
멀티-AI 에이전트 파이프라인의 상태 머신

워크플로우 구조:
┌───────────┐
│  classify │ ← 진입점
└─────┬─────┘
      ▼
┌───────────┐
│  generate │ ◄─────────┐
└─────┬─────┘           │ (재시도)
      ▼                 │
┌───────────┐           │
│  validate │ ──────────┘
└─────┬─────┘
      │
      ├── 승인 → complete → END
      │
      └── 에스컬레이션 → escalate → END
"""
from typing import Literal
from langgraph.graph import StateGraph, END

from app.graph.state import TicketState
from app.agents import classify_node, generate_node, validate_node


# ============================================================
# 조건부 라우터 함수
# ============================================================

def should_retry_or_complete(state: dict) -> Literal["generate", "complete", "escalate"]:
    """
    검증 후 다음 단계 결정 (조건부 라우팅)
    
    Validator Agent가 상태를 업데이트하면
    이 함수가 다음 노드를 결정함
    
    Returns:
        - "complete": 응답 승인됨 → 완료 노드로
        - "generate": 재생성 필요 → Generator로 돌아감
        - "escalate": 상담사 에스컬레이션 → 에스컬레이션 노드로
    """
    status = state.get("status", "validating")
    
    # 완료 상태면 완료 노드로
    if status == "completed":
        return "complete"
    
    # 에스컬레이션 상태면 에스컬레이션 노드로
    elif status == "escalated":
        return "escalate"
    
    # 그 외: 재시도 또는 에스컬레이션
    else:
        retry_count = state.get("retry_count", 0)
        max_retries = state.get("max_retries", 3)
        
        # 최대 재시도 초과 → 에스컬레이션
        if retry_count >= max_retries:
            return "escalate"
        
        # 재시도 가능 → Generator로 돌아감
        return "generate"


# ============================================================
# 터미널 노드 (종료 상태)
# ============================================================

async def complete_node(state: dict) -> dict:
    """
    완료 노드 - 티켓 처리 성공
    
    Validator가 응답을 승인했을 때 실행됨
    상태를 "completed"로 표시하고 종료
    """
    state["status"] = "completed"
    return state


async def escalate_node(state: dict) -> dict:
    """
    에스컬레이션 노드 - 상담사 검토 필요
    
    다음 경우에 실행됨:
    - Validator가 응답을 3회 연속 거부
    - 정책 위반 감지됨
    - 복잡한 문의로 AI 처리 부적합
    
    상태를 "escalated"로 표시하고 종료
    실제 운영에서는 상담사 큐에 추가
    """
    state["status"] = "escalated"
    return state


# ============================================================
# 워크플로우 생성
# ============================================================

def create_workflow() -> StateGraph:
    """
    LangGraph 워크플로우 생성
    
    워크플로우 단계:
    1. classify: 티켓 분석 및 카테고리 분류
       - 카테고리, 우선순위, 태그, 감정 분석
    
    2. generate: RAG 기반 응답 생성
       - Vector DB에서 관련 문서 검색
       - LLM으로 응답 생성
    
    3. validate: 응답 품질 검증
       - 품질 점수, 정책 준수, 톤 적절성 체크
    
    4. 조건부 라우팅:
       - 승인 → complete (종료)
       - 재생성 필요 → generate (루프)
       - 품질 미달/정책 위반 → escalate (종료)
    """
    # StateGraph 초기화 (상태 타입: dict)
    workflow = StateGraph(dict)
    
    # ========== 노드 추가 ==========
    # 각 노드는 상태(dict)를 받아 수정된 상태를 반환
    workflow.add_node("classify", classify_node)   # AI 분류
    workflow.add_node("generate", generate_node)   # AI 응답 생성
    workflow.add_node("validate", validate_node)   # AI 품질 검증
    workflow.add_node("complete", complete_node)   # 처리 완료
    workflow.add_node("escalate", escalate_node)   # 에스컬레이션
    
    # ========== 진입점 설정 ==========
    workflow.set_entry_point("classify")
    
    # ========== 엣지(전이) 추가 ==========
    # 순차 실행: classify → generate → validate
    workflow.add_edge("classify", "generate")
    workflow.add_edge("generate", "validate")
    
    # ========== 조건부 엣지 ==========
    # validate 노드 실행 후 should_retry_or_complete 함수로 분기
    workflow.add_conditional_edges(
        "validate",                  # 소스 노드
        should_retry_or_complete,    # 라우터 함수
        {
            "complete": "complete",  # 반환값 → 대상 노드
            "generate": "generate",
            "escalate": "escalate"
        }
    )
    
    # ========== 종료 엣지 ==========
    # complete, escalate 노드 실행 후 END로 이동
    workflow.add_edge("complete", END)
    workflow.add_edge("escalate", END)
    
    return workflow


def compile_workflow():
    """
    워크플로우 컴파일
    
    실행 가능한 형태로 변환
    .invoke() 또는 .astream()으로 실행
    """
    workflow = create_workflow()
    return workflow.compile()


# ============================================================
# 싱글톤 워크플로우 인스턴스
# ============================================================

# 앱 시작 시 한 번만 컴파일
app = compile_workflow()
