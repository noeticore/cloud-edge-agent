# TAB 全量隐私检测评测结果（Layer 2）

评测日期：2026-07-05

## 运行配置

- 数据集：Text Anonymization Benchmark（TAB）train split
- 样本数：1,014
- 全部人工标注实体：87,466
- DIRECT（必须脱敏）实体：3,420
- 检测链：Regex + Presidio NER（Layer 2）
- 标注策略：annotator1
- 完整 JSON：`outputs/benchmark_results/`（本地生成，不纳入 Git）

```powershell
python benchmark/privacy_benchmark.py `
  --data outputs/datasets/tab-full/tab_dataset.json `
  --layers 2 `
  --output outputs/benchmark_results/privacy_benchmark_full_l2.json

python benchmark/privacy_benchmark.py `
  --data outputs/datasets/tab-full/tab_dataset.json `
  --identifier-filter direct `
  --layers 2 `
  --output outputs/benchmark_results/privacy_benchmark_full_l2_direct.json
```

## 全部标注实体结果

| 指标 | 结果 |
|---|---:|
| Precision | 85.65% |
| Recall | 57.56% |
| F1 | 68.85% |
| 预测实体数 | 58,779 |
| Exact span match | 69.64% |
| GT coverage | 47.22% |
| DIRECT recall | 60.67%（2,075 / 3,420） |
| QUASI recall | 69.99%（38,710 / 55,307） |
| NO_MASK recall | 34.15%（9,814 / 28,739） |
| 平均替换数 | 58.0 / sample |
| 评测耗时 | 177.8 秒（175.3 ms / sample） |

| TAB 类型 | F1 |
|---|---:|
| DATETIME | 94.01% |
| PERSON | 80.58% |
| LOC | 75.37% |
| DEM | 57.90% |
| ORG | 18.20% |
| MISC | 6.31% |
| CODE | 3.29% |
| QUANTITY | 0.00% |

## DIRECT 必须脱敏实体结果

| 指标 | 结果 |
|---|---:|
| Precision | 3.53% |
| Recall | 60.64% |
| F1 | 6.67% |
| DIRECT 匹配 | 2,075 / 3,420 |
| 预测实体数 | 58,779 |
| PERSON F1 | 25.72% |
| ORG F1 | 18.18% |
| CODE F1 | 3.06% |
| 评测耗时 | 180.2 秒（177.7 ms / sample） |

DIRECT recall 说明系统找到了约六成必须脱敏的实体；但 Precision 只有 3.53%，因为 Presidio 将大量 QUASI 和 NO_MASK 实体也全部作为敏感信息替换，存在非常明显的过度脱敏。当前方案偏保守，但仍漏掉约 39.3% 的 DIRECT 实体。

## 结论

1. Presidio 对日期、人名和地点识别较好，但对组织、案件编号、杂项和数量的覆盖明显不足。
2. Regex 几乎没有贡献，因为项目正则主要针对中国手机号、身份证、邮箱和银行卡，而 TAB 是英文欧洲法院判例。
3. 从真正的隐私保护目标看，应重点报告 DIRECT recall 60.67%，同时诚实说明过度脱敏和漏检并存。
4. “全部实体 F1=68.85%”衡量的是通用实体重合度，不等于必须脱敏实体的实际质量。
5. 当前 Layer 2 适合做课程项目基线，但尚不能声称达到生产级隐私保护。

## 结果边界

- 全量 TAB 的 1,014 个样本全部含标注实体；DIRECT 过滤后也只有 1 个无 DIRECT 实体样本，因此 privacy-level accuracy 和 false-alarm 指标区分度很弱。
- TAB 的匿名化目标针对特定申请人，与通用 PII 检测并不完全相同。
- Layer 1 在英文 TAB 上为 0% 属于预期现象，不代表中文结构化 PII 正则失效。
- Layer 3 依赖本地 Ollama；本次环境未启动 Ollama，因此没有运行 SLM 全量评测。

## 本轮修复

- Layer 1 的分层统计不再错误调用 NER。
- 本地 JSON 输入现在会正确应用 `--identifier-filter`。
- Presidio AnalyzerEngine 改为进程内复用。
- Benchmark 在评分和分层统计之间复用 NER 结果。
- 增加 `benchmark` 可选依赖组及对应回归测试。
