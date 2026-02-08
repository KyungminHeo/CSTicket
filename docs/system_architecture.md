# Multi-AI Agent 시스템 아키텍처

## 🔄 전체 시스템 흐름

```
┌──────────────────────────────────────────────────────────────────────┐
│                           전체 아키텍처                               │
└──────────────────────────────────────────────────────────────────────┘

[사용자/클라이언트]
        │
        │ 1️⃣ POST /api/v1/tickets (문의 내용)
        ▼
┌─────────────────┐
│  API Gateway    │  ← FastAPI 서버
│  (gateway/)     │
└────────┬────────┘
         │ 2️⃣ 티켓 저장 (DB) + 상태 저장 (Redis)
         │ 3️⃣ Kafka에 이벤트 발행
         ▼
    ╔═══════════╗
    ║   Kafka   ║  topic: "ticket-events"
    ╚═════╤═════╝
          │ 4️⃣ 메시지 소비 (Consumer)
          ▼
┌─────────────────┐
│   Orchestrator  │  ← LangGraph 워크플로우
│  (orchestrator/)│
│                 │
│  classify       │  5️⃣ AI 분류
│      ↓          │
│  generate       │  6️⃣ AI 응답 생성 (RAG)
│      ↓          │
│  validate       │  7️⃣ AI 품질 검증
│      ↓          │
│  complete       │
└────────┬────────┘
         │ 8️⃣ 결과를 Kafka에 발행
         ▼
    ╔═══════════╗
    ║   Kafka   ║  topic: "agent-results"
    ╚═════╤═════╝
          │ 9️⃣ Gateway가 소비 (TODO: 미구현)
          ▼
┌─────────────────┐
│  API Gateway    │  → DB 업데이트
└────────┬────────┘
         │
         │ 🔟 사용자가 폴링으로 결과 확인
         │    GET /api/v1/tickets/{id}/status
         ▼
[사용자/클라이언트]
```

---

## 📋 단계별 설명

| 단계 | 컴포넌트 | 동작 |
|------|----------|------|
| 1️⃣ | Client → Gateway | 사용자가 문의 작성 (API 호출) |
| 2️⃣ | Gateway | 티켓 저장 + Redis 상태="pending" |
| 3️⃣ | Gateway → Kafka | `ticket-events` 토픽에 이벤트 발행 |
| 4️⃣ | Kafka → Orchestrator | Consumer가 메시지 수신 |
| 5️⃣ | Orchestrator | ClassifierAgent 실행 |
| 6️⃣ | Orchestrator | GeneratorAgent 실행 (RAG) |
| 7️⃣ | Orchestrator | ValidatorAgent 실행 |
| 8️⃣ | Orchestrator → Kafka | `agent-results` 토픽에 결과 발행 |
| 9️⃣ | Kafka → Gateway | (TODO) DB 업데이트 |
| 🔟 | Client ← Gateway | 사용자가 상태/결과 조회 |

---

## 🏗️ 컴포넌트 역할

### API Gateway (FastAPI)
- REST API 엔드포인트 제공
- JWT 인증 처리
- Rate Limiting
- Kafka 이벤트 발행

### Orchestrator (LangGraph)
- 멀티-AI 에이전트 워크플로우 실행
- 3개 에이전트 순차/조건부 실행
- 체크포인트 저장 (Redis)

### Kafka
- 비동기 메시지 큐
- Gateway ↔ Orchestrator 느슨한 결합
- 토픽: `ticket-events`, `agent-results`

### Redis
- 실시간 상태 추적 (폴링 응답)
- JWT 토큰 관리 (refresh, blacklist)
- Rate Limiting 카운터
- LangGraph 체크포인트

### Qdrant (Vector DB)
- RAG용 지식 베이스 저장
- 문서 임베딩 벡터 검색

---

## 🤖 AI 에이전트 파이프라인 (LangGraph)

```
┌─────────────┐
│   classify  │ ← 진입점 (티켓 분류)
└──────┬──────┘
       │ 카테고리, 우선순위, 태그, 감정 분석
       ▼
┌─────────────┐
│   generate  │ ◄────────────┐
└──────┬──────┘              │ (재시도 루프, 최대 3회)
       │ RAG 검색 + LLM 응답 생성
       ▼                     │
┌─────────────┐              │
│   validate  │ ─────────────┘
└──────┬──────┘
       │ 품질 점수, 정책 준수 검증
       │
       ├── 승인 (score >= 0.7) → complete → END
       │
       └── 에스컬레이션 → escalate → END (상담사 검토)
```

---

## ⏱️ 사용자 경험 흐름

```
1. 문의 작성 → 즉시 "티켓이 접수되었습니다" (202 Accepted)
2. 화면에서 진행률 표시 (폴링)
   - pending: 0%
   - classifying: 25%
   - generating: 50%
   - validating: 75%
   - completed: 100%
3. 완료되면 AI 응답 확인
```

---

## 🔑 핵심 설계 원칙

| 원칙 | 설명 |
|------|------|
| **비동기 처리** | Gateway는 즉시 응답, AI 처리는 백그라운드 |
| **느슨한 결합** | Kafka로 컴포넌트 간 의존성 최소화 |
| **실시간 상태** | Redis로 클라이언트 폴링 지원 |
| **장애 복구** | LangGraph 체크포인트로 중단된 워크플로우 복구 |
| **품질 보장** | Validator가 응답 품질 검증, 재시도/에스컬레이션 |
