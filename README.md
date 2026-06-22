# CloudEdgeAgent — 云边协同隐私保护 AI Agent 系统

Privacy-First Cloud-Edge Collaborative AI Agent System

## Architecture

```text
┌─────────────────────────────────────────────────────────────────────┐
│  Frontend (Vue 3 + Naive UI)                                        │
│  ChatView · DocumentsView · StatusView · HistoryView                │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP / SSE
┌──────────────────────────────▼──────────────────────────────────────┐
│  API Layer (FastAPI)                                                │
│  POST /api/v1/chat · POST /api/v1/chat/stream                      │
│  POST /api/v1/documents · GET /api/v1/documents/search              │
│  GET /api/v1/chat/sessions · GET /health                            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│  Service Layer                                                      │
│  ChatService → CollaborativeOrchestrator                            │
│                    ├── PrivacyDetector (3-layer: Regex → NER → SLM) │
│                    ├── ComplexityAnalyzer (edge SLM)                │
│                    ├── PolicyEngine (privacy × complexity matrix)   │
│                    └── Execute Mode:                                │
│                        A: Direct Local    (S1 + low complexity)     │
│                        B: Direct Cloud    (S1 + high complexity)    │
│                        C: Sanitize→Cloud  (S2/S3 + high complexity) │
│                        D: Sketch→Refine   (S2/S3 + extreme complex) │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│  Domain Layer (ABC interfaces)                                      │
│  LLMClient · BaseAgent · MemoryStore · ConversationStore            │
│  PrivacyDetector · Sanitizer · Chunker · Embedder · Retriever       │
│  Reranker · BaseTool · ToolRegistry                                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│  Infrastructure Layer                                               │
│  OpenAICompatibleClient (Ollama/DeepSeek)                           │
│  ReActAgent (LangGraph StateGraph)                                  │
│  QdrantMemoryStore + MiniLMEmbedder + LLMReranker                  │
│  SQLiteConversationStore + SQLiteSanitizationMappingStore           │
│  ThreeLayerPrivacyDetector + RegexSanitizer                         │
│  InMemoryCache + SessionCacheManager                                │
└─────────────────────────────────────────────────────────────────────┘
```

## Project Structure

```text
final_project/
├── app/                          # Backend application
│   ├── api/                      # FastAPI HTTP layer
│   │   ├── routers/              #   chat.py, documents.py, health.py
│   │   ├── schemas/              #   Pydantic request/response models
│   │   └── dependencies/         #   Dependency injection wiring
│   ├── core/                     # Cross-cutting concerns
│   │   ├── config/               #   Pydantic Settings
│   │   ├── logger/               #   structlog setup
│   │   ├── exceptions/           #   Unified exception hierarchy
│   │   └── security/             #   API key validation
│   ├── domain/                   # Business abstractions (no external deps)
│   │   ├── agent/                #   BaseAgent, ReActAgent (LangGraph)
│   │   ├── llm/                  #   LLMClient interface
│   │   ├── memory/               #   MemoryStore, ConversationStore ABC
│   │   ├── privacy/              #   PrivacyDetector, Sanitizer, Policy
│   │   ├── rag/                  #   Chunker, Embedder, Retriever, Reranker
│   │   └── tool/                 #   BaseTool, ToolRegistry
│   ├── infrastructure/           # Concrete implementations
│   │   ├── llm/                  #   OpenAICompatibleClient, ClientFactory
│   │   ├── vectorstore/          #   QdrantMemoryStore
│   │   ├── database/             #   SQLiteConversationStore, MappingStore
│   │   ├── rag/                  #   MiniLMEmbedder, LLMReranker, RAGPipeline
│   │   └── cache/                #   InMemoryCache, SessionCacheManager
│   ├── services/                 # Business orchestration
│   │   ├── privacy_engine.py     #   3-layer detector + regex sanitizer
│   │   ├── agent_orchestrator.py #   CollaborativeOrchestrator (4 modes)
│   │   └── chat_service.py       #   ChatService (end-to-end pipeline)
│   └── main.py                   #   FastAPI app factory + lifespan
├── frontend/                     # Frontend application (Vue 3 + Vite)
│   ├── src/
│   │   ├── api/                  #   Axios HTTP client + API functions
│   │   ├── types/                #   TypeScript type definitions
│   │   ├── router/               #   Vue Router (4 routes)
│   │   ├── stores/               #   Pinia state management
│   │   ├── views/                #   ChatView, DocumentsView, StatusView, HistoryView
│   │   └── components/           #   Sidebar, ChatMessage, ChatInput, PrivacyBadge, ModeTag
│   ├── vite.config.ts            #   Vite config with API proxy
│   └── package.json
├── tools/                        # Built-in agent tools
│   ├── search_tool.py            #   Web search (DuckDuckGo API)
│   ├── calculator_tool.py        #   Safe math evaluation (AST)
│   └── time_tool.py              #   Current time (UTC)
├── tests/
│   ├── unit/                     #   10 unit test files
│   ├── integration/              #   Integration tests
│   └── e2e/                      #   End-to-end tests (placeholder)
├── scripts/
│   ├── cli.py                    #   Interactive CLI (reuses ChatService)
│   ├── run.py                    #   Dev server runner
│   └── start_local_llm.bat       #   Ollama startup script
├── configs/
│   └── .env.example              #   Environment variable template
├── docs/
│   └── code-review-report.md     #   Code review report
├── data/
│   └── local_memory.db           #   SQLite database
├── pyproject.toml                #   Project metadata + dependencies
├── CLAUDE.md                     #   Engineering guidelines
└── README.md
```

## Features

### Core Features

- **四级隐私路由** — 隐私等级(S1/S2/S3) × 复杂度(L1-L5) 自动决策路由模式
- **Mode A — 本地直答** — 无敏感数据 + 低复杂度，全部在边缘完成
- **Mode B — 云端直答** — 无敏感数据 + 高复杂度，直接调用云端 LLM
- **Mode C — 脱敏上云** — 含敏感数据 + 高复杂度，脱敏后发云端，还原答案
- **Mode D — 草稿精修** — 含敏感数据 + 极高复杂度，本地草稿 + 云端精修
- **ReAct Agent** — LangGraph 实现 Think → Act → Observe 循环，支持工具调用
- **RAG 知识库** — Qdrant 向量库 + MiniLM 嵌入 + LLM 重排序
- **跨会话记忆** — SQLite 双内容存储（原始 + 脱敏），支持历史检索
- **SSE 流式输出** — 打字机效果实时返回

### Privacy Pipeline

```text
Layer 1: Regex    — 手机号、身份证、邮箱、银行卡 (延迟 < 1ms)
Layer 2: NER      — Presidio 识别人名、地址、公司名
Layer 3: SLM Judge — Qwen2.5-1.5B 判断语义敏感度
```

### Built-in Tools

| Tool | Description |
|------|-------------|
| SearchTool | DuckDuckGo 联网搜索 |
| CalculatorTool | AST 安全数学计算 |
| TimeTool | UTC 时间查询 |

## Quick Start

### 1. Install dependencies

```bash
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp configs/.env.example .env
# Edit .env with your API keys
```

### 3. Start local LLM (Ollama)

```bash
ollama pull qwen2.5:7b
ollama serve
```

### 4. Start Qdrant (Docker)

```bash
docker run -d -p 6333:6333 qdrant/qdrant
```

### 5. Run the backend

```bash
python scripts/run.py
# or
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 6. Run the frontend (development mode)

```bash
cd frontend
npm install
npm run dev
# Open http://localhost:5173
```

### 7. Production mode (single server)

```bash
cd frontend
npm run build           # Build to frontend/dist/
uvicorn app.main:app    # FastAPI serves frontend at :8000
```

### 8. CLI mode

```bash
python scripts/cli.py
```

### 9. Run tests

```bash
pytest tests/ -v
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/api/v1/chat` | Send message (sync) |
| POST | `/api/v1/chat/stream` | Send message (SSE streaming) |
| GET | `/api/v1/chat/sessions` | List all sessions |
| GET | `/api/v1/chat/sessions/{id}/messages` | Get session messages |
| POST | `/api/v1/documents` | Upload document to RAG |
| GET | `/api/v1/documents/search` | Search documents |

## Tech Stack

| Component | Choice | Purpose |
|-----------|--------|---------|
| Backend | FastAPI | Async API framework |
| Frontend | Vue 3 + Vite + Naive UI | Chat UI with routing visualization |
| Edge LLM | Ollama + Qwen2.5-7B | Local inference (privacy-sensitive) |
| Cloud LLM | DeepSeek API | Cloud inference (complex tasks) |
| SLM Judge | Qwen2.5-1.5B | Privacy detection judge |
| Agent | LangGraph ReAct | Think → Act → Observe loop |
| Vector DB | Qdrant | RAG knowledge base |
| Embedding | MiniLM (sentence-transformers) | Local document embedding |
| Privacy | 3-layer pipeline | Regex → NER → SLM |
| Database | SQLite | Conversation history + sanitization mappings |
| Logging | structlog | Structured JSON logging |

## Collaborate Modes

| Mode | Name | Privacy | Complexity | Flow |
|------|------|---------|------------|------|
| A | 本地直答 | S1 | L1-L2 | User → Edge → Answer |
| B | 云端直答 | S1 | L3-L5 | User → Cloud → Answer |
| C | 脱敏上云 | S2/S3 | L3-L5 | User → Sanitize → Cloud → Restore → Answer |
| D | 草稿精修 | S2/S3 | L5 | Edge sketch → Cloud refine → Edge restore |
