# 实验设计文档

## 目录

1. [实验一：延迟性能测试](#实验一延迟性能测试)
2. [实验二：Token 节省量测试](#实验二token-节省量测试)

---

## 实验一：延迟性能测试

### 目标

测量云边协同系统在不同路由模式下的响应延迟，验证本地 LLM 在低复杂度场景下的速度优势。

### 测试指标

| 指标 | 全称 | 定义 | 测量方式 |
|------|------|------|---------|
| **TTFT** | Time to First Token | 从发送请求到收到第一个 token 的时间 | SSE 流式响应，记录首个 `token` 事件到达时间 |
| **TPOT** | Time Per Output Token | 每个输出 token 的平均耗时 | 相邻 token 事件的时间差取平均 |
| **Total Latency** | 端到端延迟 | 从发送请求到收到 `done` 事件的总时间 | 请求开始到结束的挂钟时间 |
| **Chunks** | 输出分片数 | 流式响应中的 token 事件总数 | 计数 `token` 类型事件 |

### 测试用例设计

#### Mode A（本地直答）— 5 个用例

设计原则：简单知识问答，无敏感信息，低复杂度，预期路由到边缘 LLM。

| ID | 查询 | 场景 |
|----|------|------|
| a1 | 什么是马尔可夫链？请简要解释。 | 简单知识问答 |
| a2 | Python中list和tuple的区别是什么？ | 编程基础知识 |
| a3 | 今天是星期几？ | 时间查询（调用工具） |
| a4 | 帮我算一下 2 的 10 次方是多少。 | 简单计算（调用工具） |
| a5 | 用一句话解释什么是量子计算。 | 简短知识问答 |

#### Mode B/C（云端）— 5 个用例

设计原则：复杂推理任务或含敏感信息的复杂任务，预期路由到云端 LLM。

| ID | 查询 | 场景 | 预期模式 |
|----|------|------|---------|
| b1 | 请详细分析 Transformer 架构中 Self-Attention 机制的计算复杂度... | 复杂技术分析 | B |
| b2 | 请对比分析 Batch Normalization 和 Layer Normalization... | 复杂对比分析 | B |
| b3 | 帮我分析一下我手机号13912345678的用户在哪个运营商... | 含手机号的复杂查询 | C |
| b4 | 我的身份证号是110101199001011234，请帮我分析... | 含身份证的复杂查询 | C |
| b5 | 请帮我写一篇关于联邦学习在医疗数据隐私保护中应用的综述... | 长文本生成 | B |

### 预期结果

| 指标 | Mode A (本地) | Mode B/C (云端) |
|------|:---:|:---:|
| TTFT | 较低（本地推理无网络延迟） | 较高（含网络往返 + 云端排队） |
| TPOT | 中等（7B 模型推理速度适中） | 较低（云端大模型并行度高） |
| Total Latency | 对简单任务更低 | 对复杂任务更高但质量更好 |

### 运行方式

```bash
# 1. 确保后端运行
python -m uvicorn app.main:app --port 8000

# 2. 运行实验
python experiments/exp1_latency.py

# 3. 查看结果
cat experiments/results/latency_results.json
```

### 结果输出

结果保存到 `experiments/results/latency_results.json`，格式：

```json
{
  "experiment": "latency_benchmark",
  "timestamp": "2026-06-22 15:00:00",
  "mode_a_cases": [
    {
      "case_id": "a1",
      "mode": "direct_local",
      "ttft_ms": 320.5,
      "tpot_ms": 45.2,
      "total_ms": 2150.0,
      "num_chunks": 42
    }
  ],
  "summary": {
    "mode_a": {
      "avg_ttft_ms": 350.0,
      "avg_tpot_ms": 48.5,
      "avg_total_ms": 2300.0
    },
    "mode_bc": {
      "avg_ttft_ms": 800.0,
      "avg_tpot_ms": 30.0,
      "avg_total_ms": 5200.0
    }
  }
}
```

---

## 实验二：Token 节省量测试

### 目标

对比云边协同路由方案与纯云方案的 token 消耗和成本，验证本地 LLM 的成本优势。

### 核心思路

```text
纯云方案:  所有请求 → 云端 LLM → 全部消耗云端 token → 全部花钱
我们的方案: 简单请求 → 本地 LLM → 消耗本地 token → 免费
            复杂请求 → 云端 LLM → 消耗云端 token → 花钱
```

### 定价参数

基于 DeepSeek API 定价（2026 年）：

| 项目 | 价格 |
|------|------|
| 云端输入 token | ¥1 / 百万 tokens |
| 云端输出 token | ¥2 / 百万 tokens |
| 本地 Ollama | 免费 |

### 测试用例设计 — 15 个日常场景

设计原则：模拟真实用户的日常使用模式，混合简单/复杂、敏感/非敏感场景。

#### 简单问答（预期走本地）— 7 个

| ID | 查询 | 场景 |
|----|------|------|
| s01 | 你好，今天过得怎么样？ | 闲聊 |
| s02 | Python中for循环和while循环有什么区别？ | 简单知识 |
| s03 | 帮我算一下 15 * 23 + 47 等于多少。 | 简单计算 |
| s04 | 什么是机器学习？用一句话解释。 | 简单知识 |
| s05 | 现在几点了？ | 时间查询 |
| s06 | HTTP和HTTPS的区别是什么？ | 简单知识 |
| s07 | 用Python写一个Hello World程序。 | 简单编程 |

#### 复杂任务（预期走云端）— 3 个

| ID | 查询 | 场景 |
|----|------|------|
| s08 | 请详细解释 Raft 共识算法的工作原理... | 复杂技术分析 |
| s09 | 请分析2024年全球AI芯片市场的竞争格局... | 复杂分析 |
| s10 | 请设计一个分布式限流系统... | 系统设计 |

#### 含敏感信息的复杂任务（预期脱敏上云）— 2 个

| ID | 查询 | 场景 |
|----|------|------|
| s11 | 我的邮箱是zhangsan@company.com，请帮我分析... | 含邮箱的复杂任务 |
| s12 | 我手机号是13800138000，请帮我查一下... | 含手机号的复杂任务 |

#### 含敏感信息的简单任务（预期走本地）— 2 个

| ID | 查询 | 场景 |
|----|------|------|
| s13 | 我的银行卡号是6222021234567890123，请问这是哪个银行的卡？ | 含银行卡的简单任务 |
| s14 | 请帮我存一下我老板的电话：13912345678... | 含联系方式的存储任务 |

#### 中等复杂度 — 1 个

| ID | 查询 | 场景 |
|----|------|------|
| s15 | 请解释什么是Docker容器，它和虚拟机有什么区别？ | 中等知识问答 |

### 成本计算方法

```python
# 纯云方案成本（所有请求都走云端）
pure_cloud_cost = Σ (prompt_tokens × ¥1/M + completion_tokens × ¥2/M)

# 我们的方案成本（只有走云端的才花钱）
our_cost = Σ (云端请求的 prompt_tokens × ¥1/M + completion_tokens × ¥2/M)
# 本地请求成本 = 0

# 节省金额
saved = pure_cloud_cost - our_cost
savings_pct = saved / pure_cloud_cost × 100%
```

### 预期结果

| 指标 | 预期值 |
|------|--------|
| 本地路由比例 | 约 60%（15 个场景中 9 个走本地） |
| 节省比例 | 约 40-60%（取决于实际路由决策） |
| 成本差异 | 纯云方案成本 > 我们的方案成本 |

### 运行方式

```bash
# 1. 确保后端运行
python -m uvicorn app.main:app --port 8000

# 2. 运行实验
python experiments/exp2_token_savings.py

# 3. 查看结果
cat experiments/results/token_savings_results.json
```

### 结果输出

结果保存到 `experiments/results/token_savings_results.json`，格式：

```json
{
  "experiment": "token_savings",
  "scenarios": [
    {
      "id": "s01",
      "category": "闲聊",
      "mode": "direct_local",
      "actual_route": "local",
      "est_prompt_tokens": 15,
      "est_completion_tokens": 30,
      "pure_cloud_cost_yuan": 0.000075,
      "our_cost_yuan": 0.0,
      "saved_yuan": 0.000075
    }
  ],
  "summary": {
    "total_scenarios": 15,
    "local_routed": 9,
    "cloud_routed": 6,
    "total_pure_cloud_cost_yuan": 0.005,
    "total_our_cost_yuan": 0.002,
    "total_saved_yuan": 0.003,
    "savings_percentage": 60.0
  }
}
```

---

## 实验环境

| 项目 | 配置 |
|------|------|
| 边缘 LLM | Ollama + Qwen2.5-7B（本地） |
| 云端 LLM | DeepSeek API |
| SLM 裁判 | Qwen2.5-1.5B（本地） |
| 向量库 | Qdrant (Docker) |
| 后端 | FastAPI (localhost:8000) |
| 测试脚本 | Python + httpx |

## 文件结构

```text
experiments/
├── experiments.md              # 本文档
├── exp1_latency.py             # 实验一：延迟测试脚本
├── exp2_token_savings.py       # 实验二：Token 节省量测试脚本
└── results/                    # 实验结果输出目录
    ├── latency_results.json
    └── token_savings_results.json
```
