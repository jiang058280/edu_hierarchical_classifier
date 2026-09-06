# 数据与模型溯源记录（DATA PROVENANCE）

> 本文档记录项目关键资产（主干模型、数据集、增强产物）的来源与选型依据。
> 建立于 2026-09-06（改进计划 WP-A3 / WP-E1）；此前溯源信息散落在已废弃的 `config.yaml` 与旧版运行日志中。

## 一、主干模型（backbone）来源

| 项 | 记录 |
| --- | --- |
| 原始位置（只读，未移动） | `d:/pycharm/ccpython/toumanfen/003_bert/model/bert-base-chinese` |
| 候选模型扫描父目录 | `d:/pycharm/ccpython/toumanfen`（多候选评估择优） |
| 选型结论 | `bert-base-chinese` 胜出（宏平均 F1 与 CPU 推理耗时综合最优，通过 1500ms 硬性门槛） |
| 项目内副本 | `models/pretrained_backbone/bert-base-chinese/`（411MB safetensors + tokenizer，不入 git） |
| 微调产物 | `models/pretrained_backbone/bert-base-chinese-finetuned/`（pytorch_model.bin，被各版本 manifest 的 `backbone_ref` 引用） |
| 溯源旁证 | `legacy/user_models/models_path.txt`（候选路径清单）；旧评估报告归档于 `legacy/logs_snapshot/evaluation_report.json` |

## 二、数据集来源

| 数据集 | 来源 | 获取时间 | 规模 | 备注 |
| --- | --- | --- | --- | --- |
| K-12EduBench（主数据） | HuggingFace 公开数据集（下载缓存重定向至 `.huggingface_cache/`，已随 WP-A 清理） | 2026-08（v0.1 阶段） | 3,259 条原始 → 3,252 条去重后 | 9 学科 JSON，含学科/题型（客观/主观）/一级知识点/答案字段 |
| shijuan1（抓取样例） | 自建爬虫（`legacy/src/scraper.py`）抓取的试卷样例 | 2026-08 | 每学科约 10 条 | 仅用于验证爬取链路，未进入训练集 |

## 三、数据增强产物（当前训练数据）

| 项 | 记录 |
| --- | --- |
| 增强脚本 | `legacy/src/augment_data.py`（v0.1 阶段一次性脚本，已随旧版归档） |
| 增强后规模 | 6,901 条（train 4,829 / val 1,033 / test 1,039），见 `data/processed/labels.json` |
| 增强前基线 | 3,252 条，见 `data/processed/cleaning_report.json`（历史口径） |
| ⚠️ 参数留痕状态 | **增强超参数（倍率/方式/种子）未随脚本参数化保存，精确复现不可得**——改进计划 WP-E 通过 `data_manifest.json`（文件 SHA256 登记）+ 参数化脚本 `scripts/preprocess_all.py` 修复；后续增强一律走新管道并在本文件登记 |

## 四、训练运行证据（v0.1-base）

- 训练/评估运行日志：`legacy/logs_snapshot/training.log`、`train_gpu_run.log`（2026-09-06 从 `logs/` 归档）；
- 旧版一次性评估报告：`legacy/logs_snapshot/evaluation_report.json`、`test_evaluation.json`；
- 受治理评估报告：`reports/evaluation/`（golden 集口径，改进 WP-A4 起为全量 300 条）。

## 五、后续登记规则

自本文件建立起，以下事件必须在本文件追加登记：

1. 每次新增/更换数据集（来源、规模、获取方式、合规留痕）；
2. 每次数据增强/预处理参数变更（参数值、种子、影响行数、生成的 data_manifest 哈希）；
3. 每次主干模型更换（来源、选型评估报告位置）。
