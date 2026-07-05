# Privacy Masking Benchmark

评估 `ThreeLayerPrivacyDetector` + `RegexSanitizer` 隐私屏蔽管道在 Text Anonymization Benchmark (TAB) 数据集上的表现。

## 目录结构

```
benchmark/
├── __init__.py                  # 包初始化
├── download_tab_dataset.py      # TAB 数据集下载脚本
├── privacy_benchmark.py         # 隐私屏蔽评测主脚本
├── README.md                    # 本文件
└── results/                     # 评测结果输出目录

datasets/                        # 数据集存放目录（项目根目录下）
└── tab/
```

## 快速开始

### 1. 安装依赖

```bash
pip install datasets  # HuggingFace datasets (用于下载 TAB)
```

### 2. 下载 TAB 数据集

```bash
# 下载全部训练集 → datasets/tab/
python benchmark/download_tab_dataset.py

# 快速测试（只下载 100 条）
python benchmark/download_tab_dataset.py --max-samples 100

# 多数投票模式（至少 2 个标注者一致才采纳）
python benchmark/download_tab_dataset.py --annotator majority

# 只保留 DIRECT 类型标识符
python benchmark/download_tab_dataset.py --identifier-type direct

# 导出为 JSONL 格式
python benchmark/download_tab_dataset.py --format jsonl
```

### 3. 运行评测

```bash
# 默认两层检测（Regex + Presidio NER，100 条样本）
python benchmark/privacy_benchmark.py --max-samples 100

#  Regex + ner（Layer 2）
python benchmark/privacy_benchmark.py --layers 2 --max-samples 100

# 完整三层（Regex + Keywords + SLM，需要 Ollama）
python benchmark/privacy_benchmark.py --layers 3 --max-samples 200

# 使用预下载的数据集文件
python benchmark/privacy_benchmark.py --data datasets/tab/tab_dataset.json

# 指定标注者策略
python benchmark/privacy_benchmark.py --annotator majority --max-samples 100

# 只评测 DIRECT 标识符（必须脱敏的实体）
python benchmark/privacy_benchmark.py --identifier-filter direct --max-samples 100

# 指定输出文件
python benchmark/privacy_benchmark.py --max-samples 100 --output results/my_eval.json
```

## 评测指标

### 实体检测 (Entity Detection)

| 指标 | 说明 |
|------|------|
| Precision | 检测出的实体中真正是实体的比例 |
| Recall | 真实实体中被检测出的比例 |
| F1 Score | Precision 和 Recall 的调和平均 |
| Exact Span Match | 字符区间与标注完全一致的比例 |
| GT Coverage | 标注实体中被精确命中的比例 |

按实体类型分别统计：`PHONE`, `ID_CARD`, `EMAIL`, `BANK_CARD`, `IP_ADDRESS`, `NAME`, `ADDRESS`, `FINANCIAL`, `MEDICAL`, `MISC`

### 标识符类型召回 (Identifier-Type Recall)

TAB 数据集将每个实体标注为三种标识符类型：

| 类型 | 含义 | 脱敏要求 |
|------|------|----------|
| DIRECT | 直接标识符 (姓名、证件号) | **必须**脱敏 |
| QUASI | 准标识符 (日期、组织) | **应该**脱敏 |
| NO_MASK | 非标识符 (无关实体) | 无需脱敏 |

评测分别统计三种类型的召回率。

### 隐私级别分类 (Privacy Level Classification)

| 指标 | 说明 |
|------|------|
| Accuracy | 整体分类正确率 |
| False-Safe Rate | 有标注实体的文本被误判为 S1（危险 — 遗漏 PII） |
| False-Alarm Rate | 无标注实体的文本被误判为 S2/S3（过度保守） |

### 检测层分析 (Layer Breakdown)

统计 Regex Layer、Keyword Layer 各自的命中率和贡献度。

### 脱敏质量 (Sanitization Quality)

- 平均替换实体数
- 文本长度变化比例

## TAB 数据集

Text Anonymization Benchmark (TAB) 包含 1,014 份欧洲人权法院 (ECHR) 英文判例（train split），每条由 2-4 位标注者独立标注。标注结构为 span-level entity mentions：

```json
{
  "id": "001-90194",
  "text": "PROCEDURE\nThe case originated in an application...",
  "applicant": "Henrik Hasslund",
  "entities": [
    {"text": "36244/06", "type": "CODE", "start": 54, "end": 62, "identifier_type": "DIRECT"},
    {"text": "Kingdom of Denmark", "type": "ORG", "start": 76, "end": 94, "identifier_type": "NO_MASK"},
    {"text": "Mr Henrik Hasslund", "type": "PERSON", "start": 253, "end": 271, "identifier_type": "DIRECT"}
  ]
}
```

### TAB 实体类型 → 系统类型映射

| TAB 标签 | 含义 | 映射到我们的类型 |
|----------|------|------------------|
| PERSON | 人名 | NAME |
| CODE | 编号/标识符 | ID_CARD |
| LOC | 地点 | ADDRESS |
| ORG | 组织 | NAME |
| DATETIME | 日期时间 | MISC |
| DEM | 国籍/族群 | MISC |
| QUANTITY | 数量 | MISC |
| MISC | 其他 | MISC |

### 多标注者支持

| 模式 | 说明 |
|------|------|
| `annotator1` (默认) | 使用标注者 1 的标注作为 ground truth |
| `majority` | 至少 2 位标注者一致 (IoU >= 0.5) 才采纳 |
| `union` | 合并所有标注者的标注（去重） |

## 已知局限性

- 当前隐私引擎的 Regex 和 Keywords 层主要面向**中文** PII（手机号、身份证、中文地址关键词），对英文 ECHR 判例的召回率会很有限
- 英文场景下的检测能力主要依赖 SLM 层（`--use-slm`）
- TAB 的标注目标是「匿名化特定申请人」，而非通用 NER —— 同一实体类型是否脱敏取决于与申请人的关联

## 依赖关系

```
benchmark
  ├── benchmark/download_tab_dataset.py  (实体提取逻辑复用)
  ├── app/services/privacy_engine.py     (ThreeLayerPrivacyDetector, RegexSanitizer)
  ├── app/domain/privacy/                (PrivacyDetection, SensitiveEntity, etc.)
  └── datasets (HuggingFace)             (下载 TAB 数据集)
```
