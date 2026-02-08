"""
응답 생성 에이전트 (Generator Agent)
RAG (Retrieval Augmented Generation) 기반 고객 응답 생성

워크플로우 두 번째 노드로 실행됨:
1. Vector DB(Qdrant)에서 관련 문서 검색
2. 검색된 문서를 컨텍스트로 LLM 응답 생성

Classifier Agent의 분류 결과를 활용하여
카테고리/우선순위/감정에 맞는 응답 생성
"""
from typing import Optional
import json

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from shared import get_settings
from app.graph.state import TicketState


# ============================================================
# 응답 생성 프롬프트 템플릿
# ============================================================

GENERATOR_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """You are a professional customer support agent. Generate a helpful, 
empathetic, and accurate response to the customer's inquiry.

Guidelines:
1. Be polite and professional
2. Address all points in the customer's message
3. Provide clear and actionable information
4. Use the provided context documents when relevant
5. If you don't have enough information, acknowledge it honestly
6. Keep responses concise but complete
7. Match the tone to the severity (more formal for complaints/urgent issues)

Category: {category}
Priority: {priority}
Customer Sentiment: {sentiment}

Relevant Knowledge Base Documents:
{context_docs}"""),
    ("human", """Customer Message:
{content}

Generate a professional support response:""")
])


# ============================================================
# Generator Agent 클래스
# ============================================================

class GeneratorAgent:
    """
    RAG 기반 응답 생성 에이전트
    
    처리 흐름:
    1. 고객 문의를 임베딩 벡터로 변환
    2. Qdrant에서 유사 문서 검색
    3. 검색된 문서 + 문의 내용으로 LLM 응답 생성
    
    사용되는 컴포넌트:
    - ChatOpenAI: 응답 생성 LLM
    - OpenAIEmbeddings: 텍스트 → 벡터 변환
    - QdrantClient: 벡터 유사도 검색
    """
    
    def __init__(self):
        settings = get_settings()
        
        # 응답 생성용 LLM (temperature 0.7: 자연스러운 응답)
        self.llm = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=0.7  # 적당한 창의성
        )
        
        # 임베딩 모델 (텍스트 → 벡터)
        self.embeddings = OpenAIEmbeddings(api_key=settings.openai_api_key)
        
        # Qdrant 벡터 DB 클라이언트
        self.qdrant = QdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port
        )
        self.collection_name = "knowledge_base"  # 지식 베이스 컬렉션
        
        # LangChain 체인
        self.chain = GENERATOR_PROMPT | self.llm
    
    async def _retrieve_context(self, query: str, category: str, limit: int = 3) -> list[str]:
        """
        Vector DB에서 관련 문서 검색 (RAG의 Retrieval 단계)
        
        Args:
            query: 검색 쿼리 (고객 문의 내용)
            category: 카테고리 필터 (선택적)
            limit: 반환할 문서 수
            
        Returns:
            관련 문서 내용 목록
        
        동작:
        1. query를 임베딩 벡터로 변환
        2. Qdrant에서 유사도 검색
        3. 결과에서 content 추출
        """
        try:
            # 쿼리 임베딩 생성
            query_embedding = await self.embeddings.aembed_query(query)
            
            # Qdrant 검색 (카테고리 필터 적용)
            results = self.qdrant.search(
                collection_name=self.collection_name,
                query_vector=query_embedding,
                limit=limit,
                query_filter={
                    "must": [
                        {"key": "category", "match": {"value": category}}
                    ]
                } if category else None
            )
            
            # 검색 결과에서 문서 내용 추출
            return [hit.payload.get("content", "") for hit in results]
            
        except Exception as e:
            # Vector DB 연결 실패 시 빈 목록 반환 (graceful degradation)
            print(f"Context retrieval warning: {e}")
            return []
    
    async def generate(self, state: TicketState) -> TicketState:
        """
        응답 생성 실행
        
        Args:
            state: 현재 티켓 상태 (분류 결과 포함)
            
        Returns:
            draft_response가 추가된 상태
        """
        try:
            # 1. 관련 문서 검색 (RAG)
            context_docs = await self._retrieve_context(
                query=state.content,
                category=state.category
            )
            state.context_docs = context_docs
            
            # 2. 컨텍스트 문서 포맷팅
            context_text = "\n\n".join(context_docs) if context_docs else "No relevant documents found."
            
            # 3. LLM으로 응답 생성
            result = await self.chain.ainvoke({
                "category": state.category or "general",
                "priority": state.priority or "medium",
                "sentiment": state.sentiment or "neutral",
                "context_docs": context_text,
                "content": state.content
            })
            
            # 4. 상태 업데이트
            state.draft_response = result.content
            state.status = "generating"
            
            return state
            
        except Exception as e:
            state.error_message = f"Generation error: {str(e)}"
            state.status = "failed"
            return state


# ============================================================
# LangGraph 노드 함수
# ============================================================

async def generate_node(state: dict) -> dict:
    """
    LangGraph 노드 함수 (응답 생성)
    
    Args:
        state: LangGraph 상태 (dict)
        
    Returns:
        업데이트된 상태 (dict)
    """
    ticket_state = TicketState(**state)
    agent = GeneratorAgent()
    result = await agent.generate(ticket_state)
    return result.model_dump()
