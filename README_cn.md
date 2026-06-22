# CloudEdgeAgent — 云边协同隐私保护 AI Agent 系统

## 系统架构

```text
┌─────────────────────────────────────────────────────────────────────┐
│  前端 (Vue 3 + Naive UI)                                            │
│  对话页面 · 文档管理 · 系统状态 · 历史会话                              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP / SSE
┌──────────────────────────────▼──────────────────────────────────────┐
│  API 层 (FastAPI)                                                   │
│  POST /api/v1/chat · POST /api/v1/chat/stream                      │
│  POST /api/v1/documents · GET /api/v1/documents/search              │
│  GET /api/v1/chat/sessions · GET /health                            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│  服务层                                                             │
│  ChatService → CollaborativeOrchestrator                            │
│                    ├── 隐私检测器 (三级: 正则 → NER → SLM)           │
│                    ├── 复杂度分析器 (边缘 SLM)                      │
│                    ├── 路由策略引擎 (隐私 × 复杂度矩阵)              │
│                    └── 执行模式:                                    │
│                        A: 本地直答    (S1 + 低复杂度)               │
│                        B: 云端直答    (S1 + 高复杂度)               │
│                        C: 脱敏上云    (S2/S3 + 高复杂度)            │
│                        D: 草稿精修    (S2/S3 + 极高复杂度)          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│  领域层 (抽象接口)                                                   │
│  LLMClient · BaseAgent · MemoryStore · ConversationStore            │
│  PrivacyDetector · Sanitizer · Chunker · Embedder · Retriever       │
│  Reranker · BaseTool · ToolRegistry                                 │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│  基础设施层                                                         │
│  OpenAI兼容客户端 (Ollama/DeepSeek)                                 │
│  ReAct智能体 (LangGraph StateGraph)                                 │
│  Qdrant向量库 + MiniLM嵌入器 + LLM重排序器                          │
│  SQLite对话存储 + SQLite脱敏映射存储                                 │
│  三级隐私检测器 + 正则脱敏器                                         │
│  内存缓存 + 会话缓存管理器                                           │
└─────────────────────────────────────────────────────────────────────┘
```

## 项目结构

```text
final_project/
├── app/                          # 后端应用
│   ├── api/                      # FastAPI HTTP 层
│   │   ├── routers/              #   路由: chat.py, documents.py, health.py
│   │   ├── schemas/              #   Pydantic 请求/响应模型
│   │   └── dependencies/         #   依赖注入
│   ├── core/                     # 横切关注点
│   │   ├── config/               #   Pydantic Settings 配置管理
│   │   ├── logger/               #   structlog 结构化日志
│   │   ├── exceptions/           #   统一异常层级
│   │   └── security/             #   API Key 验证
│   ├── domain/                   # 领域层 (纯业务抽象，无外部依赖)
│   │   ├── agent/                #   BaseAgent, ReActAgent (LangGraph)
│   │   ├── llm/                  #   LLMClient 统一接口
│   │   ├── memory/               #   MemoryStore, ConversationStore 抽象
│   │   ├── privacy/              #   PrivacyDetector, Sanitizer, 路由策略
│   │   ├── rag/                  #   Chunker, Embedder, Retriever, Reranker
│   │   └── tool/                 #   BaseTool, ToolRegistry
│   ├── infrastructure/           # 基础设施层 (具体实现)
│   │   ├── llm/                  #   OpenAI兼容客户端, 客户端工厂
│   │   ├── vectorstore/          #   QdrantMemoryStore 向量存储
│   │   ├── database/             #   SQLite对话存储, 脱敏映射存储
│   │   ├── rag/                  #   MiniLM嵌入器, LLM重排序器, RAG管道
│   │   └── cache/                #   内存缓存, 会话缓存管理
│   ├── services/                 # 服务层 (业务编排)
│   │   ├── privacy_engine.py     #   三级隐私检测器 + 正则脱敏器
│   │   ├── agent_orchestrator.py #   协同编排器 (4种模式)
│   │   └── chat_service.py       #   ChatService (端到端管道)
│   └── main.py                   #   FastAPI 应用工厂 + 生命周期管理
├── frontend/                     # 前端应用 (Vue 3 + Vite)
│   ├── src/
│   │   ├── api/                  #   Axios HTTP 客户端 + API 函数
│   │   ├── types/                #   TypeScript 类型定义
│   │   ├── router/               #   Vue Router (4个路由)
│   │   ├── stores/               #   Pinia 状态管理
│   │   ├── views/                #   对话、文档管理、系统状态、历史会话
│   │   └── components/           #   侧边栏、消息气泡、输入框、隐私标签
│   ├── vite.config.ts            #   Vite 配置 (含 API 代理)
│   └── package.json
├── tools/                        # 内置 Agent 工具
│   ├── search_tool.py            #   DuckDuckGo 联网搜索
│   ├── calculator_tool.py        #   AST 安全数学计算
│   └── time_tool.py              #   UTC 时间查询
├── tests/
│   ├── unit/                     #   10 个单元测试文件
│   ├── integration/              #   集成测试
│   └── e2e/                      #   端到端测试 (占位)
├── scripts/
│   ├── cli.py                    #   交互式命令行 (复用 ChatService)
│   ├── run.py                    #   开发服务器启动脚本
│   └── start_local_llm.bat       #   Ollama 启动脚本
├── configs/
│   └── .env.example              #   环境变量模板
├── docs/
│   └── code-review-report.md     #   代码审查报告
├── data/
│   └── local_memory.db           #   SQLite 数据库
├── pyproject.toml                #   项目元数据 + 依赖
├── CLAUDE.md                     #   工程规范指南
└── README.md
```

## 核心特性

### 四级隐私路由

系统根据**隐私等级**(S1/S2/S3)和**任务复杂度**(L1-L5)自动选择最优路由模式：

| 模式 | 名称 | 隐私等级 | 复杂度 | 流程 |
|------|------|---------|--------|------|
| A | 本地直答 | S1 | L1-L2 | 用户 → 边缘 → 回答 |
| B | 云端直答 | S1 | L3-L5 | 用户 → 云端 → 回答 |
| C | 脱敏上云 | S2/S3 | L3-L5 | 用户 → 脱敏 → 云端 → 还原 → 回答 |
| D | 草稿精修 | S2/S3 | L5 | 边缘草稿 → 云端精修 → 边缘还原 |

### 三级隐私检测流水线

```text
第一层: 正则匹配  — 手机号、身份证、邮箱、银行卡 (延迟 < 1ms)
第二层: NER识别   — Presidio 识别人名、地址、公司名
第三层: SLM裁判   — Qwen2.5-1.5B 判断语义敏感度
```

设计思路：快速路径优先（正则命中即短路），逐层兜底，兼顾速度和覆盖度。

### ReAct 智能体

基于 LangGraph StateGraph 实现 Think → Action → Observe → Final Answer 循环：
- 支持多工具调用
- 工具名称验证（防止幻觉工具名）
- 重复调用检测
- 容错解析（空 Action、多行 Final Answer）

### RAG 知识库

```text
文档上传 → 文本分块 → MiniLM嵌入 → Qdrant向量存储
用户查询 → MiniLM嵌入 → Qdrant语义搜索 → LLM重排序 → 结果
```

### 跨会话记忆

- **短期记忆** — 当前会话上下文（SQLite 滑动窗口）
- **长期记忆** — Qdrant 向量存储 + MiniLM 嵌入
- **跨会话检索** — SQLite 关键词搜索 + 最近对话检索
- **双内容存储** — 原始内容（本地）+ 脱敏内容（云端上下文）

### 内置工具

| 工具 | 说明 |
|------|------|
| SearchTool | DuckDuckGo 联网搜索 |
| CalculatorTool | AST 安全数学计算 |
| TimeTool | UTC 时间查询 |

## 快速启动

### 1. 安装依赖

```bash
pip install -e ".[dev]"
```

### 2. 配置环境

```bash
cp configs/.env.example .env
# 编辑 .env 填入 API Key
```

### 3. 启动本地 LLM (Ollama)

```bash
ollama pull qwen2.5:7b
ollama serve
```

### 4. 启动 Qdrant 向量库 (Docker)

```bash
docker run -d -p 6333:6333 qdrant/qdrant
```

### 5. 启动后端

```bash
python scripts/run.py
# 或
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 6. 启动前端（开发模式）

```bash
cd frontend
npm install
npm run dev
# 打开 http://localhost:5173
```

### 7. 生产模式（单服务器）

```bash
cd frontend
npm run build           # 构建到 frontend/dist/
uvicorn app.main:app    # FastAPI 统一托管前端和后端，访问 :8000
```

### 8. 命令行模式

```bash
python scripts/cli.py
```

### 9. 运行测试

```bash
pytest tests/ -v
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/api/v1/chat` | 发送消息（同步） |
| POST | `/api/v1/chat/stream` | 发送消息（SSE 流式输出） |
| GET | `/api/v1/chat/sessions` | 列出所有会话 |
| GET | `/api/v1/chat/sessions/{id}/messages` | 获取会话消息记录 |
| POST | `/api/v1/documents` | 上传文档到 RAG 知识库 |
| GET | `/api/v1/documents/search` | 语义搜索文档 |

## 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 后端框架 | FastAPI | 异步 API 框架 |
| 前端框架 | Vue 3 + Vite + Naive UI | 响应式 Web UI + 路由可视化 |
| 边缘 LLM | Ollama + Qwen2.5-7B | 本地推理（隐私敏感任务） |
| 云端 LLM | DeepSeek API | 云端推理（复杂任务） |
| SLM 裁判 | Qwen2.5-1.5B | 隐私检测语义判断 |
| Agent 框架 | LangGraph ReAct | Think → Act → Observe 循环 |
| 向量数据库 | Qdrant | RAG 知识库 |
| 嵌入模型 | MiniLM (sentence-transformers) | 本地文档嵌入 |
| 隐私检测 | 三级流水线 | 正则 → NER → SLM |
| 数据库 | SQLite | 对话历史 + 脱敏映射持久化 |
| 日志 | structlog | 结构化 JSON 日志 |

## 前端页面

| 页面 | 功能 |
|------|------|
| 对话页面 | 聊天界面，SSE 流式输出，显示隐私等级、路由模式、耗时 |
| 文档管理 | 上传文档到 RAG 知识库，语义搜索 |
| 系统状态 | 后端健康检查，最近路由决策详情，模式说明 |
| 历史会话 | 浏览所有历史会话及对话记录 |

## 交付物

- **后端** — FastAPI 四层架构，完整的 Agent + 隐私检测 + RAG + 工具链
- **前端** — Vue 3 Web UI，4 个功能页面
- **CLI** — 交互式命令行，复用 ChatService
- **测试** — 10 个单元测试 + 集成测试
- **文档** — README.md、架构设计文档、代码审查报告
