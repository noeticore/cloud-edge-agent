# 云边协同隐私保护 Agent 系统 — Architecture & Plan

**Version:** v2.0 | **Updated:** 2026-06-22

---

## 1. 项目背景与核心思路

随着 DeepSeek、Qwen、GPT 等大模型能力提升，AI Agent 被广泛用于个人助理、代码开发、知识管理等场景。但现有系统存在两个核心痛点：

- **隐私风险**：用户的身份证号、手机号、银行流水、企业代码、私有文档等敏感数据可能直接上传至第三方云端。
- **本地模型能力不足**：本地部署的模型虽安全，但推理能力弱、长上下文有限、Tool Calling 效果差，无法独立完成复杂任务。

为此，本项目构建一个 **Privacy-First Cloud-Edge Collaborative Agent**，遵循 Local First、Privacy First、Cost Aware、Graceful Degradation 四项原则。核心思路是：简单任务本地执行，复杂任务云端执行；当面对敏感又复杂的数据时，先本地脱敏，再送云端推理，最后本地恢复结果。

---

## 2. 系统目标

**功能目标**：支持对话问答、Agent 推理、Tool 调用、RAG 检索、云边协同推理、隐私保护、Web UI 可视化。

**非功能目标**：
- **隐私保护** — 敏感数据默认不出本地。
- **透明可控** — 用户可查看路由结果、脱敏内容及 Agent 执行全过程。
- **可扩展性** — 新模型、新工具、新知识库可快速接入。

---

## 3. 总体架构

系统采用四层架构，数据流向如下：

```text
┌─────────────────────────────────────────────────────────────────┐
│  Frontend (Vue 3 + Naive UI)                                    │
│  ChatView · DocumentsView · StatusView · HistoryView            │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP / SSE
┌──────────────────────────────▼──────────────────────────────────┐
│  API Layer (FastAPI)                                            │
│  /api/v1/chat · /api/v1/chat/stream · /api/v1/documents        │
│  /api/v1/chat/sessions · /health                                │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  Service Layer                                                  │
│  ChatService → CollaborativeOrchestrator                        │
│    ├── PrivacyDetector (Regex → NER → SLM)                     │
│    ├── ComplexityAnalyzer                                       │
│    ├── PolicyEngine (privacy × complexity → mode A/B/C/D)      │
│    └── ReActAgent (LangGraph) + ToolRegistry                   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  Domain Layer (ABC interfaces)                                  │
│  LLMClient · BaseAgent · MemoryStore · ConversationStore        │
│  PrivacyDetector · Sanitizer · RAG components · BaseTool        │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  Infrastructure Layer                                           │
│  OpenAICompatibleClient · ReActAgent · QdrantMemoryStore        │
│  MiniLMEmbedder · LLMReranker · SQLiteConversationStore        │
│  ThreeLayerPrivacyDetector · RegexSanitizer                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. 核心模块（已实现）

### 4.1 LLM Client — 统一模型接口

定义统一的 `LLMClient` 抽象（`invoke`、`stream_invoke`、`think`、`embedding`），屏蔽底层差异。

- **OpenAICompatibleClient** — 通过 OpenAI SDK 兼容协议接入 Ollama（本地）和 DeepSeek（云端）
- **ClientFactory** — 根据配置创建 edge/cloud 客户端
- **Edge fallback** — Ollama 不可用时自动降级到云端

### 4.2 Privacy Engine — 隐私分析（三级流水线）

- **Layer 1 — Regex**：正则匹配手机号、身份证、邮箱、银行卡号，延迟 < 1ms
- **Layer 2 — NER**：基于 Presidio 识别人名、地址、公司名等命名实体
- **Layer 3 — SLM Judge**：使用 Qwen2.5-1.5B 判断文本是否敏感及其等级

**RegexSanitizer** — 将敏感实体替换为占位符（如 `[PHONE_001]`），支持跨会话映射持久化（SQLite）。

### 4.3 Task Analyzer — 任务复杂度评估

通过 LLM 分析将任务分为五个等级：

| 等级 | 描述 |
|------|------|
| L1 | FAQ 类应答 |
| L2 | 单步推理 |
| L3 | 多步推理 |
| L4 | Agent 任务 |
| L5 | 长链复杂任务 |

### 4.4 Policy Engine — 路由决策

综合隐私等级和复杂度等级做决策：

| Privacy | Complexity | Mode | Description |
|---------|------------|------|-------------|
| S1 | L1-L2 | A | 本地直答 |
| S1 | L3-L5 | B | 云端直答 |
| S2/S3 | L1-L2 | A | 本地直答 |
| S2/S3 | L3-L5 | C | 脱敏上云 |
| S2/S3 | L5 (extreme) | D | 草稿精修 |

### 4.5 Collaborative Orchestrator — 协同编排

- **Mode A — Direct Local**：用户 → Edge Agent → 回答
- **Mode B — Direct Cloud**：用户 → Cloud Agent → 回答
- **Mode C — Sanitize-Cloud-Restore**：用户 → RegexSanitizer → Cloud Agent → 还原 → 回答
- **Mode D — Sketch-Refine**：Edge Agent 生成草稿 → Cloud Agent 精修 → Edge 还原

### 4.6 ReAct Agent 框架

基于 LangGraph StateGraph 实现 Think → Action → Observation → Final Answer 循环：
- 支持多工具调用
- 工具名称验证（防止幻觉工具名）
- 重复调用检测
- 空 Action / 多行 Final Answer 容错解析

### 4.7 Tool Layer

| Tool | Description |
|------|-------------|
| SearchTool | DuckDuckGo 联网搜索 |
| CalculatorTool | AST 安全数学计算 |
| TimeTool | UTC 时间查询 |

工具通过 `ToolRegistry` 注册，Agent 自动决定是否调用。

### 4.8 Memory Layer — 多层记忆

- **短期记忆** — 当前会话上下文（SQLite get_context_messages）
- **长期记忆** — Qdrant 向量存储 + MiniLM 嵌入
- **跨会话记忆** — SQLite 关键词搜索 + 最近对话检索
- **双内容存储** — 原始内容（本地）+ 脱敏内容（云端上下文）

### 4.9 RAG Pipeline

```text
Document Upload → FixedSizeChunker → MiniLMEmbedder → QdrantMemoryStore
Query → MiniLMEmbedder → QdrantMemoryStore.search → LLMReranker → Results
```

- **FixedSizeChunker** — 可配置大小和重叠的文本分块
- **MiniLMEmbedder** — 本地 sentence-transformers 嵌入
- **QdrantMemoryStore** — 向量存储和检索
- **LLMReranker** — LLM 批量评分重排序

---

## 5. 前端（Vue 3 + Naive UI）

### 5.1 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 框架 | Vue 3 + Vite | 响应式 SPA |
| UI 库 | Naive UI | 企业级组件库 |
| 类型 | TypeScript | 类型安全 |
| 状态管理 | Pinia | 轻量状态管理 |
| 路由 | Vue Router 4 | 页面路由 |
| HTTP | Axios | 请求封装 |

### 5.2 页面

| 页面 | 功能 |
|------|------|
| ChatView | 对话界面，SSE 流式输出，显示隐私等级、路由模式、耗时 |
| DocumentsView | 文档上传（RAG 摄入）+ 语义搜索 |
| StatusView | 后端健康检查 + 最近路由决策详情 + 模式说明 |
| HistoryView | 浏览历史会话 + 对话记录 |

### 5.3 开发/生产模式

- **开发模式**：`npm run dev`（Vite on :5173）+ 后端 on :8000，Vite 代理 API
- **生产模式**：`npm run build` → `frontend/dist/`，FastAPI 通过 StaticFiles 托管

---

## 6. 技术栈总览

| 层面 | 选型 | 说明 |
|------|------|------|
| Backend | FastAPI | 轻量高性能异步框架 |
| Frontend | Vue 3 + Vite + Naive UI | 响应式 Web UI |
| Agent 框架 | LangGraph | 状态机 + 条件路由 |
| Local LLM | Ollama + Qwen2.5-7B | 本地推理 |
| Cloud LLM | DeepSeek API | 云端推理 |
| SLM Judge | Qwen2.5-1.5B | 隐私检测裁判 |
| Vector DB | Qdrant | RAG 知识库 |
| Embedding | MiniLM (sentence-transformers) | 本地嵌入 |
| Database | SQLite | 对话历史 + 脱敏映射 |
| Privacy | Presidio + Regex + SLM | 三级检测 |
| Logging | structlog | 结构化日志 |

---

## 7. 已实现功能清单

| 功能 | 状态 | 说明 |
|------|:---:|------|
| LLM 接入 (Ollama + DeepSeek) | ✅ | OpenAI 兼容客户端 |
| ReAct Agent (LangGraph) | ✅ | Think-Act-Observe 循环 |
| 三级隐私检测 | ✅ | Regex + NER + SLM |
| 脱敏与还原 | ✅ | RegexSanitizer + SQLite 映射 |
| 路由策略 (Mode A/B/C/D) | ✅ | 隐私 × 复杂度矩阵 |
| 工具调用 | ✅ | Calculator, Search, Time |
| RAG 管道 | ✅ | Qdrant + MiniLM + LLMReranker |
| 文档上传 API | ✅ | POST /api/v1/documents |
| 对话存储 (SQLite) | ✅ | 双内容存储 |
| 跨会话记忆 | ✅ | 关键词搜索 + 最近对话 |
| SSE 流式输出 | ✅ | POST /api/v1/chat/stream |
| CLI 交互 | ✅ | 复用 ChatService |
| Web UI (Vue 3) | ✅ | 4 个页面 |
| 单元测试 | ✅ | 10 个测试文件 |
| 集成测试 | ✅ | 4 个测试类 |

---

## 8. 评估方案

**对比基线**：Pure Cloud（全云端）、Pure Local（全本地）、Cloud-Edge Router（无脱敏路由）。

**评估指标**：
- **隐私** — 敏感信息泄露率
- **效率** — 端到端响应时间
- **成本** — Token 消耗、云端调用次数
- **质量** — GPT-as-Judge 评分、任务成功率

---

## 9. 预期创新点

1. **三级隐私路由**：规则引擎 + NER + SLM 组合判决，精度与延迟兼顾。
2. **云边协同 Agent 架构**：Mode A/B/C/D 四模式协同，按需在本地与云端间迁移。
3. **双内容存储**：本地保留原始数据，云端仅使用脱敏版本，兼顾隐私与智能。
4. **Sketch-Refine 协同推理**：边端生成摘要 → 云端补全 → 边端恢复。

---

## 10. 最终交付物

- **代码**：Backend (FastAPI) + Frontend (Vue 3) + CLI
- **文档**：README.md、Architecture & Plan、Code Review Report
- **演示**：Web UI 交互演示 + CLI 演示
- **测试**：单元测试 + 集成测试
