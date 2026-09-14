# ADR-01：干净测试基准（bench_clean）与双基准并行策略

| 项 | 内容 |
| --- | --- |
| 编号 | ADR-01 |
| 状态 | **Accepted（2026-09-13 执行授权）** |
| 提出日期 | 2026-09-07 |
| 提出来源 | 《剩余工作总计划》第二阶段 B2 |
| 关联文档 | `DATA_PROVENANCE.md`（第三节增强产物）、`docs/AI交接文档.md`、`reports/evaluation/` |

---

## 1. 背景与问题

### 1.1 现行测试集的构成

当前评估基准链条：

```
data/raw/k12edubench（9 学科 JSON）
   │  load_raw_samples → 3,259 条有效
   │  dedup → 3,252 条
   │  分层划分 70/15/15（seed=42）
   ▼
原始划分：train 2,274 / val 483 / test 495        ← v0.1 之前（已不可复现文件）
   │  legacy/src/augment_data.py 一次性增强（超参数未留存）
   ▼
增强后：train 4,829 / val 1,033 / test 1,039      ← 当前 data/processed/*.csv 的基底
   │  v0.3 合并 CMMLU/M3KE/GAOKAO/Numina（仅进 train/val，test 不动）
   ▼
现状：train 9,893 / val 1,917 / test 1,039
```

### 1.2 问题

1. **test.csv 含增强副本**：1,039 条 test 由 495 条原始 test 扩充而来，同一题存在多个近重复变体。近重复样本使评估有效样本量缩水、指标偏乐观（模型对见过的题型模式记忆性得分）；
2. **golden set 同源**：`eval_sets/golden_test_set.json`（300 条）从 test.csv 抽样，继承了同样的乐观偏差，"回归红线"测量的是一个虚高的水位；
3. **历史可比性约束**：golden set 一旦换源，v0.1→v0.3 的全部历史报告失去可比性，且当前 v0.4 重训（B1）正处于验收关键期，不宜同时变更基准。

## 2. 决策

### 决策一：新建 bench_clean（真实水位基准）

- 来源：`data/raw/k12edubench` 走原始管道（`edu_core.data.dataset` 的 load → dedup → 小类合并）取回**未增强的 3,252 条全量**，重新按学科分层抽取约 **500 条**（seed 固化并留痕）；
- 排除规则：与 `train.csv`（现 11,721 条，含增强副本）做文本相似比对——规范化（去空白/全半角/大小写）后：
  - 完全一致 → 排除；
  - 前 64 字符前缀一致或归一化文本相似度 ≥0.92（difflib SequenceMatcher）→ 排除；
  - 与 val.csv 同样排除（防验证集泄漏到测试口径）；
- 产出：`eval_sets/bench_clean.json`（入库 git，字段与 golden_test_set.json 对齐 + `dataset: "bench_clean"` 标识与排除统计）；
- 评估脚本：`evaluate_core_model.py` / rebuild 流水线增加 `--dataset bench_clean` 参数，产出独立报告 `reports/evaluation/<version>_bench_clean_evaluation.json`。

### 决策二：golden set 保留作回归基线，双轨并行

- **golden set（300 条）**：继续作为门禁/回滚的"回归红线"（相对指标：不得低于当前 ACTIVE 版本），保证 v0.1→v0.4+ 历史可比性不被打断；
- **bench_clean（~500 条）**：作为"真实水位"报告，每版本激活后顺带评估落盘，用于观察跨版本**绝对水平**与对外汇报；
- 所有评估报告同时给出两个口径（存在时）并在表头注明用途：`golden=回归红线（同源历史可比）` / `bench_clean=真实水位（绝对水平）`。

### 决策三：门禁基准切换时机

- v0.4（B1）：门禁仍用 golden set；bench_clean 仅产出首份真实水位报告（v0.3 也补测一次，作为双轨起点）；
- 待 bench_clean 连续积累 ≥2 个版本的报告后（预计 v0.5/v0.6），若其方差稳定，再出 ADR 修订把门禁绝对阈值（如 subject_acc ≥0.85 等）切到 bench_clean 口径；
- 切换前本 ADR 不做任何 golden set 的改动（不重导、不重抽）。

## 3. 数据流（目标态）

```
data/raw/k12edubench ──原始管道──► 3,252 条未增强
                                      │ 分层抽样 ~500（seed 留痕）
                                      │ 归一化相似去重（vs train/val）
                                      ▼
                          eval_sets/bench_clean.json ──┐
                                                       ├─► reports/evaluation/
golden_test_set.json（不动）──────────────────────────┘     <version>_evaluation.json（golden 口径，门禁）
                                                           <version>_bench_clean_evaluation.json（真实水位）
```

## 4. 备选方案（否决理由）

| 方案 | 否决理由 |
| --- | --- |
| 直接重导 golden set 换源到干净数据 | 历史报告全部失去可比性；且 v0.4 验收期同时动基准违反"每一步可回滚" |
| 用学习曲线/交叉验证替代 | 改造成本高，无法解释历史报告 |
| 不做干净基准，维持现状 | 增强副本导致的指标虚高会误导后续 ADR-03~05 的模型演进决策（风险登记册 #6） |

## 5. 后果与风险

1. **双基准解读混乱**（风险登记册 #6 对策）：所有报告双口径并列 + 用途注释；README 增补"两个基准是什么、看哪个"说明；
2. **bench_clean 覆盖面局限**：仅 K-12EduBench 分布（9 学科），不含 CMMLU/M3KE 等扩充源——真实水位以该分布为口径，报告中注明；
3. **排除规则可能过滤过多**：预计 500 条抽样中与 train 相似被排除比例较低（原始 test 本与 train 不同源划分），若排除后不足 400 条则提高抽样量至 600 重抽一次（种子 +1 递推，留痕）；
4. **实现顺序**：已实现 `scripts/build_clean_bench.py` 与 `--dataset bench_clean` 参数；`bench_clean` 一经生成即拒绝脚本覆盖，保证后续版本可比。

## 6. 验收清单（实现完成后）

- [x] `eval_sets/bench_clean.json` 落盘（432 条，含 seed、排除统计）；
- [x] v0.3 与 v0.4 各产出一份 `_bench_clean_evaluation.json`；
- [x] 评测脚本支持显式 `--dataset` 并在输出中注明口径；
- [x] 历史 golden 报告文件零改动。
