# CloudEdgeAgent 代码审查报告

> 审查日期：2026-06-18 | 审查范围：全项目 | 版本：v0.1.0
>
> **项目定位**：大学生课程项目 — 目标为功能完整、可演示、PPT 有料。性能优化和生产级稳定性不在本期范围内。

---

## 目录

1. [总体评价](#1-总体评价)
2. [当前各模块完成度](#2-当前各模块完成度)
3. [需要修复的架构问题](#3-需要修复的架构问题)
4. [汇报演示场景分析](#4-汇报演示场景分析)
5. [实现规划（分两阶段）](#5-实现规划分两阶段)
6. [附录：代码改进清单（顺手修）](#6-附录代码改进清单顺手修)

---

## 1. 总体评价

### 1.1 做得好的地方

| 维度 | 评价 |
|------|------|
| **架构分层** | API → Service → Domain → Infrastructure 四层清晰，接口抽象（ABC）规范 |
| **隐私创新** | PBCR 三层检测 + ε 预算 + 四种协作模式，差异化明确，汇报有故事可讲 |
| **技术栈** | LangGraph ReAct Agent、Qdrant 向量库、Presidio NER、FastAPI — 紧跟业界潮流 |
| **代码规范** | 类型注解完整、命名清晰、Docstring 规范、ruff+mypy+pytest 工具链齐全 |
| **测试基础** | 11 个单元测试文件 + 1 个集成测试，核心组件均有覆盖 |

### 1.2 当前状态一句话总结

> **骨骼搭好了，但神经没接上。** 各个模块独立都能跑，但串联起来的主链路是断的。修好之后就是一个功能完整、能上台演示的项目。

---

## 2. 当前各模块完成度

按"功能是否可用"而非"代码是否写了"来评估：

| 模块 | 完成度 | 说明 |
|------|--------|------|
| **LLM 接入** | ✅ 90% | OpenAI 兼容客户端，DeepSeek 和 Ollama 都能调通 |
| **ReAct Agent** | ✅ 85% | LangGraph 实现，Think-Act-Observe 循环正常，工具调用正常 |
| **隐私检测** | ✅ 85% | 三层 pipeline（Regex → NER → SLM）可用，Presidio 可降级 |
| **脱敏与还原** | ✅ 90% | RegexSanitizer 替换和还原可用 |
| **ε 预算控制** | ✅ 80% | 消耗和耗尽检查可用（内存模式，重启丢失） |
| **路由策略** | ✅ 90% | 6 条规则矩阵可用，预算耗尽强制本地 |
| **记忆（短时）** | ✅ 80% | 关键词搜索可用，但无混合检索 |
| **记忆（长时）** | ⚠️ 60% | Qdrant 存储和搜索写好了，但没接入主流程 |
| **RAG 管道** | ⚠️ 40% | 四个组件（Chunker/Embedder/Retriever/Reranker）都写好了，但没串联 |
| **Orchestrator** | ⚠️ 50% | 四种模式逻辑正确，但**绕过 Agent 直接调 LLM** |
| **Chat API** | ✅ 80% | POST /api/v1/chat 可用，但无 streaming |
| **CLI** | ✅ 90% | 交互式 ReAct 可用，但**与 Web API 是两套独立系统** |

### 核心断裂点

```
       CLI 路径                         Web API 路径
    ┌──────────┐                   ┌──────────────────┐
    │ ReActAgent│ ← 工具可用       │ Orchestrator     │
    │ + Tool    │                   │ ↓                │
    │ + Memory  │                   │ LLMClient.invoke │ ← 没用 Agent，工具不可用
    └──────────┘                   └──────────────────┘
         ↑                                ↑
    两条独立路径，逻辑重复
```

---

## 3. 需要修复的架构问题

### 3.1 🔴 核心问题：Orchestrator 绕过了 Agent

**现状**：`agent_orchestrator.py` 四种模式全部直接调 `LLMClient.invoke()`。

```python
# app/services/agent_orchestrator.py:179 — Mode A
async def _mode_direct_local(self, query: str) -> str:
    messages = [LLMMessage(role="user", content=query)]
    response = await self._edge.invoke(messages)  # ← 绕过 Agent
    return response.content
```

**后果**：工具（CalculatorTool / SearchTool / TimeTool）在 Web API 路径中完全不可用。你对着网页问"帮我算 123*456"，它只能猜，调不了计算器。

**修复思路**：Orchestrator 持有 EdgeAgent 和 CloudAgent 两个 `BaseAgent` 实例，Mode A/B/C/D 通过 Agent 执行。Agent 内部自动决定是否调用工具。

### 3.2 🔴 CLI 和 Web API 是两套系统

`scripts/cli.py` 自己创建 `ReActAgent` + `ToolRegistry` + `InMemoryShortTermStore`，自己管理记忆；`chat_service.py` 也做同样的事。改一处要改两处。

**修复思路**：CLI 复用 `ChatService.chat()`，变成一个薄壳。

### 3.3 🟡 RAG 管道组件齐全但没串联

Chunker、Embedder、Retriever、Reranker 四个组件都实现了，但没有一个 "RAGPipeline" 把它们连起来。缺少：
- 文档摄入流程（上传文档 → 分块 → Embedding → 存入 Qdrant）
- 检索流程（查询 → Embedding → 向量搜索 → Rerank → 返回结果）
- 文档上传 API

### 3.4 🟡 隐私检测未注入 Agent

ReActAgent 拿到 query 直接发给 LLM，不经过隐私检测。虽然当前 Web API 路径的 Orchestrator 做了检测，但如果 Agent 被直接调用（如 CLI），隐私保护就失效了。

---

## 4. 汇报演示场景分析

站在"PPT 答辩 + 现场演示"的角度，梳理一下你最想展示什么，以及当前能不能展示。

### 4.1 核心演示场景

| 场景 | 演示目的 | 当前能否演示？ |
|------|---------|:---:|
| **场景 1**：普通问答走本地 | 无敏感信息 → Edge LLM 直接回答 | ✅ 可以 |
| **场景 2**：复杂推理走云端 | L3-L5 任务 → Cloud LLM | ✅ 可以 |
| **场景 3**：含手机号的查询 → 自动脱敏 → 云端回答 → 还原 | S2 隐私 + 复杂任务 → Mode C | ✅ 可以 |
| **场景 4**：含机密信息 + 复杂任务 → Sketch-Refine | S3 隐私 + 复杂任务 → Mode D (PBCR) | ⚠️ 逻辑有但没验证效果 |
| **场景 5**：Agent 调用工具回答 | "帮我算 123*456" → ReAct → CalculatorTool | ❌ Web API 不可用 |
| **场景 6**：上传文档后基于文档问答 | 文档 → RAG 管道 → 检索增强回答 | ❌ 完全不可用 |
| **场景 7**：多轮对话记忆 | 上一轮说了名字，下一轮能记住 | ✅ 短时记忆可用 |
| **场景 8**：ε 预算耗尽 → 强制本地 | 多次敏感请求后 budget=0 → 降级 Edge | ✅ 可以 |
| **场景 9**：Streaming 输出 | 打字机效果实时返回 | ❌ 未实现 |
| **场景 10**：跨会话长时记忆 | 昨天的对话今天还能想起来 | ⚠️ Qdrant 存了但没接入检索 |

### 4.2 汇报故事线

一个好的项目汇报通常有一条清晰的故事线，比如：

> 1. **问题**：隐私数据上云的风险 → 引出 PBCR 创新
> 2. **方案**：三层检测 → ε 预算 → 四种协作模式 → 技术架构
> 3. **演示**：场景 3（脱敏上云）+ 场景 4（Sketch-Refine）+ 场景 5（工具调用）+ 场景 6（RAG）
> 4. **亮点**：LangGraph ReAct Agent、PBCR 差异化对比

当前**无法完成完整演示**，因为场景 5（工具调用）和场景 6（RAG）完全不可用。修好这两点 + 打通 Agent 接入，就足够支撑一场完整汇报。

---

## 5. 实现规划（分两阶段）

> **原则**：
> - 只做对"演示 + PPT"有直接贡献的事
> - 性能、稳定性、生产级特性一律不做
> - 代码能跑就行，不追求优雅（但顺手修的 bug 还是要修）

### 阶段一：补全核心功能（预计 2-3 周）

这是**最重要**的阶段。完成后，汇报所需的全部演示场景都能跑通。

#### Task 1: Agent 接入 Orchestrator 🔴 最关键

**做什么**：让 Orchestrator 的四种模式通过 Agent 执行，而不是直接调 LLM。

```
Before: Orchestrator._mode_direct_local(query)
            → self._edge.invoke(messages)
            → 返回纯文本，无工具调用能力

After:  Orchestrator._mode_direct_local(query)
            → self._edge_agent.run(query, context)
            → ReAct 循环：思考 → 调工具 → 观察 → 回答
            → 返回答案 + 推理步骤（汇报可以展示 trace！）
```

**涉及文件**：
| 文件 | 改动 |
|------|------|
| `app/domain/agent/agent.py` | `BaseAgent` 增加 `tool_registry` 和 `memory` 属性声明 |
| `app/services/agent_orchestrator.py` | 构造函数接收 `edge_agent` + `cloud_agent`；四种模式改为 `agent.run()` |
| `app/api/dependencies/deps.py` | 创建 Agent 实例并注入 Orchestrator |

**验收标准**：
- curl 问"用计算器算 123*456"，Web API 能返回正确答案 + trace 显示调用了 CalculatorTool
- 敏感查询仍然走正确的隐私路由（Mode A/B/C/D 逻辑不变）

**预计工期**：3-4 天

---

#### Task 2: RAG 管道串联 + 文档 API

**做什么**：把 Chunker → Embedder → Qdrant → Retriever → Reranker 串起来，暴露一个文档上传接口。

**涉及文件**：
| 文件 | 改动 |
|------|------|
| `app/infrastructure/rag/pipeline.py` | 🆕 新增：`RAGPipeline` 类，封装 `ingest()` 和 `retrieve()` |
| `app/api/routers/documents.py` | 🆕 新增：`POST /api/v1/documents` 上传 + `GET /api/v1/documents/search` 搜索 |
| `app/services/chat_service.py` | `_retrieve_context()` 中调用 RAG 检索，把检索结果加入上下文 |
| `app/api/dependencies/deps.py` | 创建 RAGPipeline 实例并注入 |

**验收标准**：
- curl 上传一段文本 → 分块 → Embedding → 存入 Qdrant
- 发一条相关问题 → 回答中包含检索到的文档片段
- 这个特色在 PPT 里可以单独开一页讲"RAG 增强记忆"

**预计工期**：3-4 天

---

#### Task 3: CLI 复用 ChatService

**做什么**：CLI 不再自己创建 Agent/工具/记忆，改为调用 `ChatService.chat()`。

**涉及文件**：
| 文件 | 改动 |
|------|------|
| `scripts/cli.py` | 精简为：加载配置 → 创建 ChatService → 循环调用 `chat_service.chat()` |

**验收标准**：
- CLI 和 Web API 走同一套逻辑
- CLI 也能享受隐私检测 + ε 预算 + 四种路由模式

**预计工期**：1 天

---

#### Task 4: SSE Streaming 端点

**做什么**：新增 `POST /api/v1/chat/stream`，SSE 实时返回 token。这是演示加分项——打字机效果比干等好看。

**涉及文件**：
| 文件 | 改动 |
|------|------|
| `app/api/routers/chat.py` | 新增 streaming 端点，使用 `StreamingResponse` |
| `app/services/chat_service.py` | 新增 `chat_stream()` 方法 |

**验收标准**：
- 浏览器里能看到 token 逐字出现

**预计工期**：1 天

---

#### Task 5: 全局异常处理 + 接口打磨

**做什么**：让 API 报错时返回统一格式的 JSON 而非裸 500，让演示时不尴尬。

**涉及文件**：
| 文件 | 改动 |
|------|------|
| `app/main.py` | 注册 `exception_handler` 捕获 `BaseAppException` |
| `app/api/routers/chat.py` | 补充响应示例（OpenAPI docs 更好看） |

**预计工期**：0.5 天

---

### 阶段一完成后的效果

| 演示场景 | 状态 |
|---------|:---:|
| 场景 5：工具调用（算数/搜索/时间） | ✅ |
| 场景 6：文档上传 + RAG 问答 | ✅ |
| 场景 9：Streaming 输出 | ✅ |
| 所有已有场景保持可用 | ✅ |

**汇报可以有一条完整的故事线了。**

---

### 阶段二：为汇报加分（预计 1-2 周）

这些都是"锦上添花"，让演示效果更好、PPT 内容更丰富。如果时间不够，可以挑着做。

#### Task 6: 演示数据准备

**做什么**：准备一批能展示项目特色的 query，录制演示流程。

- 准备 10 条覆盖四种模式的测试 query
- 准备 2-3 份文档用于 RAG 演示
- 准备一个演示脚本（按 PPT 章节对应）

**预计工期**：1 天

---

#### Task 7: Sketch-Refine 效果增强

**做什么**：当前 Mode D 只是简单的"边摘要 → 云细化 → 边还原"，效果不够明显。增强后能直观展示"边云协作"的价值。

思路：
1. Edge 先做初步推理给出 sketch
2. Cloud 基于 sketch 深度推理
3. Edge 对云端结果做隐私审核（检查是否有敏感信息泄露）
4. 还原后展示三步对比（原始 → sketch → refined）

**涉及文件**：`app/services/agent_orchestrator.py` 的 `_mode_sketch_refine()`

**预计工期**：1-2 天

---

#### Task 8: 长时记忆接入 ChatService

**做什么**：Qdrant 长时记忆存储已经写好了，但 `chat_service.py` 中作为可选组件传入了却没有真正用于"跨会话记忆"。让它在检索上下文时也搜一下长时记忆。

**涉及文件**：`app/services/chat_service.py` 的 `_retrieve_context()`

**预计工期**：0.5 天

---

#### Task 9: 多轮对话上下文优化

**做什么**：当前 `_enrich_query()` 只是把历史消息文本拼接到 query 前面，token 多了会超出窗口。改为只注入最近 N 条（滑动窗口），或让 LLM 自己总结一下历史。

**涉及文件**：`app/services/chat_service.py`

**预计工期**：0.5 天

---

#### Task 10: API 文档页面美化

**做什么**：FastAPI 自带的 `/docs` 页面已经不错，但可以补充更详细的 description、example、tag 分组，让评委打开 Swagger 时眼前一亮。

**涉及文件**：各 router 的装饰器参数

**预计工期**：0.5 天

---

## 6. 附录：代码改进清单（顺手修）

以下是审查中发现的小问题，修起来很快（每个 5-15 分钟），可以在改核心逻辑时顺手修掉。

### 必修（影响功能正确性）

| # | 位置 | 问题 | 修复 |
|---|------|------|------|
| 1 | `react_agent.py:346` | `total_tokens=0` 写死了 | 累加每次 `LLMResponse.usage.total_tokens` |
| 2 | `agent_orchestrator.py:55` | `import json` 在函数体内部 | 移到文件顶部 |
| 3 | `privacy_engine.py:117` | 同上 | 同上 |
| 4 | `deps.py:68` | `import structlog` 在 except 块内 | 移到文件顶部 |
| 5 | `chat_service.py:43` | `budget_tracker` 参数无类型注解 | 加上 `PrivacyBudgetTracker` 类型 |

### 建议修（减少重复代码）

| # | 位置 | 问题 | 修复 |
|---|------|------|------|
| 6 | `client_factory.py` | `create_edge_llm_client` 和 `create_cloud_llm_client` 代码完全相同 | 合并为 `_create_openai_client(settings)` |
| 7 | `agent_orchestrator.py:179-190` | Mode A 和 Mode B 逻辑完全相同 | 提取 `_mode_direct(client, query)` 复用 |
| 8 | `deps.py:44-72, 87-95` | 反复创建 `OpenAICompatibleClient` | 复用 client_factory |

### 可选修（不改也不影响演示）

| # | 位置 | 问题 | 修复 |
|---|------|------|------|
| 9 | `privacy_engine.py:74` | AnalyzerEngine 每次请求新建 | 提到模块级延迟初始化 |
| 10 | `react_agent.py:35-55` | System Prompt 硬编码 | 提取为类属性，方便后续定制 |

---

## 总结

```
当前状态：                    阶段一完成后：              阶段二完成后：
┌──────────┐                ┌──────────────┐          ┌────────────────┐
│ CLI ✅   │                │ CLI ✅ (复用)│          │ 全部场景可演示 │
│ Web API✅│                │ Web API ✅   │          │ PPT 内容充实   │
│ Agent ⚠️ │                │ Agent 接入✅ │          │ 演示脚本就绪   │
│ RAG  ⚠️ │                │ RAG 管道 ✅  │          │ 效果打磨完成   │
│ 工具 ❌  │                │ Streaming✅  │          └────────────────┘
│ Stream❌ │                │ 异常处理✅   │              △ 汇报就绪
└──────────┘                └──────────────┘
   △ 能演示                      △ 功能完整
   但链路断                      够汇报了
```

**一句话建议**：优先打通 Task 1（Agent 接入）和 Task 2（RAG 管道），这两个修完，项目就从"框架不错但跑不通"变成"功能完整、能上台演示"。其他的看着做，时间够就多打磨，不够也不影响核心效果。
