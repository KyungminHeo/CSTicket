# Multi-AI Agent Customer Support System

LangGraph + Kafka + Redis 기반 Multi-AI Agent 고객 지원 자동화 시스템

Antigravity로 개발

## 기술 스택

- **Language**: Python 3.11+
- **API Framework**: FastAPI
- **Agent Framework**: LangGraph
- **Message Broker**: Apache Kafka
- **Cache/State**: Redis
- **Vector DB**: Qdrant
- **Database**: PostgreSQL

## 시작하기

### 1. 환경 설정

```bash
cp .env.example .env
# .env 파일에서 필요한 값들을 설정하세요
```

### 2. 인프라 실행

```bash
docker-compose up -d
```

### 3. Gateway 실행

```bash
cd gateway
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 4. Orchestrator 실행

```bash
cd orchestrator
pip install -r requirements.txt
python -m app.main
```

## 프로젝트 구조

```
multi-agent/
├── docker-compose.yml
├── .env.example
├── gateway/              # API Gateway (FastAPI)
├── orchestrator/         # LangGraph Orchestrator
├── shared/               # 공유 라이브러리
└── docs/                 # 문서
```

## API 엔드포인트

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/api/v1/auth/register` | POST | 회원가입 |
| `/api/v1/auth/login` | POST | 로그인 |
| `/api/v1/tickets` | POST | 티켓 생성 |
| `/api/v1/tickets/{id}` | GET | 티켓 조회 |
| `/api/v1/tickets/{id}/status` | GET | 처리 상태 조회 |
