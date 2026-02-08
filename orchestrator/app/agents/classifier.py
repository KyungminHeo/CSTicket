"""
티켓 분류 에이전트 (Classifier Agent)
LLM을 사용하여 고객 문의를 분석하고 카테고리, 우선순위, 태그, 감정을 분류

워크플로우 첫 번째 노드로 실행됨
결과는 Generator Agent로 전달되어 응답 생성에 활용됨
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
# 분류 결과 스키마
# ============================================================

class ClassificationResult(BaseModel):
    """
    LLM 분류 결과 스키마
    
    JsonOutputParser가 이 스키마를 사용하여
    LLM 응답을 구조화된 데이터로 파싱
    """
    category: str = Field(description="티켓 카테고리: billing, technical, general, complaint, other")
    priority: str = Field(description="우선순위: low, medium, high, urgent")
    tags: list[str] = Field(description="관련 태그 목록")
    sentiment: str = Field(description="고객 감정: positive, neutral, negative")
    reasoning: str = Field(description="분류 이유 설명")


# ============================================================
# 분류 프롬프트 템플릿
# ============================================================

CLASSIFIER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are an expert customer support ticket classifier. 
Analyze the customer's message and classify it accurately.

Categories:
- billing: Payment issues, invoices, refunds, subscription problems
- technical: Software bugs, errors, technical difficulties, feature issues
- general: General inquiries, information requests, how-to questions
- complaint: Dissatisfaction, complaints, negative feedback
- other: Anything that doesn't fit above categories

Priority Guidelines:
- urgent: System down, security issues, complete service unavailable
- high: Major functionality broken, significant business impact
- medium: Feature not working as expected, moderate inconvenience
- low: Minor issues, cosmetic problems, general questions

Respond ONLY with valid JSON matching the required schema."""),
    ("human", """Classify this support ticket:

Ticket ID: {ticket_id}
Content: {content}
Metadata: {metadata}

Provide classification in JSON format with: category, priority, tags, sentiment, reasoning""")
])


# ============================================================
# Classifier Agent 클래스
# ============================================================

class ClassifierAgent:
    """
    티켓 분류 에이전트
    
    LangChain을 사용하여 LLM 체인 구성:
    프롬프트 → ChatOpenAI → JsonOutputParser
    
    사용되는 LLM 설정:
    - 모델: gpt-4 (설정 가능)
    - temperature: 0.1 (일관된 분류를 위해 낮게 설정)
    """
    
    def __init__(self):
        settings = get_settings()
        
        # ChatOpenAI 모델 초기화
        self.llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=0.1  # 낮은 온도 = 일관된 결과
        )
        
        # JSON 출력 파서 (Pydantic 스키마 기반 파싱)
        self.parser = JsonOutputParser(pydantic_object=ClassificationResult)
        
        # LangChain 체인: 프롬프트 | LLM | 파서
        self.chain = CLASSIFIER_PROMPT | self.llm | self.parser
    
    async def classify(self, state: TicketState) -> TicketState:
        """
        티켓 분류 실행
        
        Args:
            state: 현재 티켓 상태 (content 필수)
            
        Returns:
            분류 결과가 추가된 상태:
            - category: 분류된 카테고리
            - priority: 분류된 우선순위
            - tags: 추출된 태그 목록
            - sentiment: 감정 분석 결과
        """
        try:
            # LLM 체인 실행 (비동기)
            result = await self.chain.ainvoke({
                "ticket_id": state.ticket_id,
                "content": state.content,
                "metadata": json.dumps(state.metadata)
            })
            
            # 상태 업데이트
            state.category = result.get("category", "other")
            state.priority = result.get("priority", "medium")
            state.tags = result.get("tags", [])
            state.sentiment = result.get("sentiment", "neutral")
            state.status = "classifying"
            
            return state
            
        except Exception as e:
            # 에러 발생 시 실패 상태로 전환
            state.error_message = f"Classification error: {str(e)}"
            state.status = "failed"
            return state


# ============================================================
# LangGraph 노드 함수
# ============================================================

async def classify_node(state: dict) -> dict:
    """
    LangGraph 노드 함수 (분류)
    
    LangGraph는 dict 형태로 상태를 전달하므로
    dict ↔ TicketState 변환 필요
    
    Args:
        state: LangGraph 상태 (dict)
        
    Returns:
        업데이트된 상태 (dict)
    """
    # dict → TicketState 변환
    ticket_state = TicketState(**state)
    
    # 에이전트 실행
    agent = ClassifierAgent()
    result = await agent.classify(ticket_state)
    
    # TicketState → dict 변환
    return result.model_dump()
