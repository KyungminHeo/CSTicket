# 전체 시스템 플로우: 회원가입 → 티켓 처리 완료

```mermaid
sequenceDiagram
    autonumber
    participant U as 사용자
    participant G as API Gateway
    participant DB as PostgreSQL
    participant R as Redis
    participant K as Kafka
    participant O as LangGraph Orchestrator
    participant V as Qdrant (Vector DB)

    rect rgb(230, 245, 255)
        Note over U,DB: 📝 Phase 1: 회원가입
        U->>G: POST /auth/register {email, password, name}
        G->>G: 비밀번호 해싱 (bcrypt)
        G->>DB: INSERT user (UUID 생성)
        DB-->>G: user_id
        G-->>U: 201 Created
    end

    rect rgb(255, 245, 230)
        Note over U,R: 🔐 Phase 2: 로그인
        U->>G: POST /auth/login {email, password}
        G->>DB: SELECT user WHERE email
        DB-->>G: user 정보
        G->>G: 비밀번호 검증
        G->>G: JWT Access Token 생성 (15분)
        G->>G: JWT Refresh Token 생성
        G->>R: SETEX refresh:{user_id}:{jti} (7일)
        G-->>U: {access_token, refresh_token}
    end

    rect rgb(245, 255, 230)
        Note over U,K: 🎫 Phase 3: 티켓 생성
        U->>G: POST /tickets {content} + Bearer Token
        G->>G: JWT 검증 (서명, 만료)
        G->>R: EXISTS blacklist:{jti}
        R-->>G: false (유효)
        G->>DB: INSERT ticket (ticket_id 생성)
        G->>K: PRODUCE ticket-events {ticket_id, content, user_id}
        G->>R: HSET status:{ticket_id} {stage: "pending"}
        G-->>U: 202 Accepted {ticket_id}
    end

    rect rgb(255, 230, 245)
        Note over K,V: 🤖 Phase 4: AI 에이전트 처리
        K->>O: CONSUME ticket-events
        O->>R: HSET status:{ticket_id} {stage: "classifying"}
        
        Note over O: 1️⃣ Classifier Agent
        O->>O: 카테고리/우선순위 분류
        O->>R: SET lg:state:{ticket_id} (체크포인트)
        O->>R: HSET status:{ticket_id} {stage: "generating"}
        
        Note over O,V: 2️⃣ Generator Agent (RAG)
        O->>V: 유사 문서 검색
        V-->>O: 관련 컨텍스트
        O->>O: LLM 응답 생성
        O->>R: SET lg:state:{ticket_id}
        O->>R: HSET status:{ticket_id} {stage: "validating"}
        
        Note over O: 3️⃣ Validator Agent
        O->>O: 품질 검증 (점수, 정책, 톤)
        
        alt 검증 통과
            O->>R: HSET status:{ticket_id} {stage: "completed"}
        else 검증 실패 (재시도 < 3)
            O->>O: Generator로 다시
        else 3회 실패
            O->>R: HSET status:{ticket_id} {stage: "escalated"}
        end
    end

    rect rgb(230, 255, 245)
        Note over U,DB: ✅ Phase 5: 결과 수신
        O->>K: PRODUCE agent-results {ticket_id, response}
        O->>DB: UPDATE ticket SET response, status
        O->>R: DEL lg:state:{ticket_id}
        U->>G: GET /tickets/{ticket_id}/status
        G->>R: HGETALL status:{ticket_id}
        R-->>G: {stage: "completed", progress: 100}
        G-->>U: 티켓 처리 완료 + 응답
    end
```

---

## 단계별 상세 설명

### Phase 1: 회원가입 📝

```
POST /auth/register
{
  "email": "user@example.com",
  "password": "securePassword123",
  "name": "홍길동"
}
```

| 단계 | 동작 | 저장소 |
|------|------|--------|
| 1 | 이메일 중복 확인 | PostgreSQL |
| 2 | 비밀번호 bcrypt 해싱 | - |
| 3 | UUID v4로 user_id 생성 | - |
| 4 | 사용자 정보 저장 | PostgreSQL |

---

### Phase 2: 로그인 🔐

```
POST /auth/login
{
  "email": "user@example.com",
  "password": "securePassword123"
}
```

| 단계 | 동작 | 저장소 |
|------|------|--------|
| 1 | 이메일로 사용자 조회 | PostgreSQL |
| 2 | 비밀번호 검증 | - |
| 3 | Access Token 생성 (15분) | - |
| 4 | Refresh Token 생성 | Redis |

**응답:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "expires_in": 900
}
```

---

### Phase 3: 티켓 생성 🎫

```
POST /tickets
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
{
  "content": "결제가 안 되고 있어요. 도와주세요!"
}
```

| 단계 | 동작 | 저장소 |
|------|------|--------|
| 1 | JWT 토큰 검증 | - |
| 2 | 블랙리스트 확인 | Redis |
| 3 | 티켓 DB 저장 | PostgreSQL |
| 4 | Kafka 이벤트 발행 | Kafka |
| 5 | 초기 상태 설정 | Redis |

**응답:**
```json
{
  "ticket_id": "t-abc123",
  "status": "pending",
  "message": "티켓이 접수되었습니다."
}
```

---

### Phase 4: AI 에이전트 처리 🤖

| 단계 | Agent | 입력 | 출력 | Redis 상태 |
|------|-------|------|------|------------|
| 1 | **Classifier** | 티켓 내용 | 카테고리: `billing`, 우선순위: `high` | `classifying` |
| 2 | **Generator** | 분류 결과 + Vector DB 컨텍스트 | 응답 초안 | `generating` |
| 3 | **Validator** | 초안 응답 | 승인/거부/에스컬레이션 | `validating` |

**LangGraph 워크플로우:**
```
classify → generate → validate
              ↑          ↓
              └── (retry) ←── 검증 실패
```

---

### Phase 5: 결과 수신 ✅

**폴링 방식:**
```
GET /tickets/t-abc123/status
→ {"stage": "completed", "progress": 100}

GET /tickets/t-abc123
→ {"response": "결제 문제 해결을 위해...", "status": "completed"}
```

---

## 데이터 흐름 요약표

| Phase | 주체 | 입력 | 출력 | 사용 저장소 |
|-------|------|------|------|-------------|
| 회원가입 | Gateway | 이메일, 비밀번호 | user_id | PostgreSQL |
| 로그인 | Gateway | 이메일, 비밀번호 | JWT 토큰들 | PostgreSQL, Redis |
| 티켓 생성 | Gateway | 티켓 내용 | ticket_id | PostgreSQL, Kafka, Redis |
| AI 처리 | Orchestrator | Kafka 이벤트 | 최종 응답 | Redis, Qdrant, PostgreSQL |
| 결과 조회 | Gateway | ticket_id | 응답 내용 | Redis, PostgreSQL |
