"""
품질 검증 에이전트 (Validator Agent)
생성된 응답의 품질, 정책 준수, 톤 적절성을 검증

워크플로우 세 번째 노드로 실행됨:
- 승인(approve): 응답 최종 확정 → complete 노드로
- 수정(revise): 재생성 필요 → generate 노드로 돌아감
- 에스컬레이션(escalate): 상담사 검토 필요 → escalate 노드로
"""
from typing import Optional
import json

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from shared import get_settings
from app.graph.state import TicketState


# ============================================================
# 검증 결과 스키마
# ============================================================

class ValidationResult(BaseModel):
    """
    LLM 검증 결과 스키마
    
    품질 평가 기준을 구조화하여
    일관된 검증 결과 생성
    """
    quality_score: float = Field(description="전체 품질 점수 (0.0 ~ 1.0)")
    is_relevant: bool = Field(description="고객 문제를 정확히 다루는가")
    is_complete: bool = Field(description="필요한 정보를 모두 제공하는가")
    is_professional: bool = Field(description="톤이 전문적이고 적절한가")
    policy_compliant: bool = Field(description="회사 정책을 준수하는가")
    issues: list[str] = Field(description="발견된 문제점 목록")
    suggestions: list[str] = Field(description="개선 제안 목록")
    verdict: str = Field(description="최종 판정: approve, revise, escalate")


# ============================================================
# 검증 프롬프트 템플릿
# ============================================================

VALIDATOR_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a quality assurance specialist for customer support responses.
Evaluate the generated response against the original customer message.

Evaluation Criteria:
1. Relevance: Does it address the customer's specific issue?
2. Completeness: Does it provide all necessary information?
3. Professionalism: Is the tone appropriate for the priority/sentiment?
4. Policy Compliance: Does it follow standard support policies?
5. Accuracy: Is the information correct based on context documents?

Scoring Guidelines:
- 0.9-1.0: Excellent, ready to send
- 0.7-0.89: Good, minor improvements possible
- 0.5-0.69: Acceptable, some issues to address
- Below 0.5: Poor, needs significant revision

Verdicts:
- approve: Score >= 0.7 and no policy violations
- revise: Score >= 0.5 and < 0.7, or minor issues
- escalate: Score < 0.5, policy violations, or complex issues

Respond ONLY with valid JSON matching the required schema."""),
    ("human", """Evaluate this response:

Original Customer Message:
{customer_message}

Ticket Classification:
- Category: {category}
- Priority: {priority}
- Sentiment: {sentiment}

Generated Response:
{draft_response}

Context Documents Used:
{context_docs}

Retry Count: {retry_count}/{max_retries}

Provide evaluation in JSON format.""")
])


# ============================================================
# Validator Agent 클래스
# ============================================================

class ValidatorAgent:
    """
    품질 검증 에이전트
    
    생성된 응답을 다음 기준으로 평가:
    1. 관련성: 고객 문제를 정확히 다루는가
    2. 완전성: 필요한 정보를 모두 제공하는가
    3. 전문성: 톤이 적절한가
    4. 정책 준수: 회사 정책을 위반하지 않는가
    
    평가 결과에 따라 다음 워크플로우 결정:
    - 점수 >= 0.7: 승인 (complete)
    - 점수 < 0.7 but >= 0.5: 재생성 (generate로 돌아감)
    - 점수 < 0.5 또는 정책 위반: 에스컬레이션 (escalate)
    """
    
    def __init__(self):
        settings = get_settings()
        
        # 검증용 LLM (temperature 0.1: 일관된 평가)
        self.llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=0.1  # 낮은 온도 = 일관된 결과
        )
        
        # JSON 출력 파서
        self.parser = JsonOutputParser(pydantic_object=ValidationResult)
        
        # LangChain 체인
        self.chain = VALIDATOR_PROMPT | self.llm | self.parser
    
    async def validate(self, state: TicketState) -> TicketState:
        """
        응답 품질 검증 실행
        
        Args:
            state: 현재 티켓 상태 (draft_response 포함)
            
        Returns:
            검증 결과가 추가된 상태:
            - quality_score: 품질 점수
            - quality_feedback: 개선 피드백
            - status: 다음 단계 (completed/escalated/validating)
        """
        try:
            # LLM 검증 실행
            result = await self.chain.ainvoke({
                "customer_message": state.content,
                "category": state.category or "general",
                "priority": state.priority or "medium",
                "sentiment": state.sentiment or "neutral",
                "draft_response": state.draft_response,
                "context_docs": "\n".join(state.context_docs) if state.context_docs else "None",
                "retry_count": state.retry_count,
                "max_retries": state.max_retries
            })
            
            # 검증 결과 상태 업데이트
            state.quality_score = result.get("quality_score", 0.0)
            state.policy_compliant = result.get("policy_compliant", True)
            state.tone_appropriate = result.get("is_professional", True)
            
            # 피드백 메시지 생성
            issues = result.get("issues", [])
            suggestions = result.get("suggestions", [])
            feedback_parts = []
            if issues:
                feedback_parts.append(f"Issues: {', '.join(issues)}")
            if suggestions:
                feedback_parts.append(f"Suggestions: {', '.join(suggestions)}")
            state.quality_feedback = " | ".join(feedback_parts) if feedback_parts else None
            
            # 판정에 따른 다음 단계 결정
            verdict = result.get("verdict", "revise")
            
            if verdict == "approve":
                # 승인: 응답 최종 확정
                state.final_response = state.draft_response
                state.status = "completed"
            elif verdict == "escalate" or state.retry_count >= state.max_retries:
                # 에스컬레이션: 상담사 검토 필요
                state.status = "escalated"
            else:
                # 재시도: generate 노드로 돌아감
                state.status = "validating"
                state.retry_count += 1
            
            return state
            
        except Exception as e:
            state.error_message = f"Validation error: {str(e)}"
            state.status = "failed"
            return state


# ============================================================
# LangGraph 노드 함수
# ============================================================

async def validate_node(state: dict) -> dict:
    """
    LangGraph 노드 함수 (품질 검증)
    
    Args:
        state: LangGraph 상태 (dict)
        
    Returns:
        업데이트된 상태 (dict)
    """
    ticket_state = TicketState(**state)
    agent = ValidatorAgent()
    result = await agent.validate(ticket_state)
    return result.model_dump()
