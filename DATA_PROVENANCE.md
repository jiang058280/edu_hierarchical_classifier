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

## 五、2026-09-06 数据扩充（v0.3 训练轮）

| 数据集 | 来源与协议 | 学段 | 学科 | 题型 | 采样量 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| CMMLU（test split） | GitHub haonan-li/CMMLU（公开评测基准） | 高中 | 理化生地数政 6 科 | 选择题 | 836 | 带 A-D 答案 |
| M3KE（test split） | GitHub tjunlp-lab/M3KE（公开评测基准） | **初中 2,167** + 高中 953（分层采样） | 语数物化生史地政 8 科 | 选择题 | 3,120 | 带答案；初中唯一来源 |
| GAOKAO-Bench | GitHub OpenLMLab/GAOKAO-Bench（高考真题 2010-2022） | 高中 | 多科（含英语阅读） | 选择题为主 + 理科政史地解答 | 1,193 | 主观题无标准答案，仅用于分类训练 |
| NuminaMath-CoT cn_k12 | HuggingFace AI-MO/NuminaMath-CoT（CC BY-NC 4.0） | 初高中混合（未标注） | 仅数学 | 解答题 | 1,500 | 排除选项题/填空形态；学段未知→学段头掩码 |

- 合并规则固化在 `scripts/build_merged_dataset.py`（去重、85/15 分层并入 train/val、**旧 test 划分不动**）；
- 新增数据 knowledge_point 置空 → 知识点头训练仍只用原有 K-12EduBench 标注（掩码损失）；
- 原始快照：`data/raw/_cmmlu_tmp/`、`_m3ke_tmp/`、`_gaokao_tmp/`、`_numina_shard0.parquet`（均不入 git）。

**评估中的未来数据源**：好未来 CK12 评测集（41K 题、9 学科 × 单选/多选/填空/判断/排序 5 题型，带课标知识点树）——数据托管 Google Drive，当前网络不可达；如有 VPN 可手动下载放入 `data/raw/` 后扩展 build 脚本，是"全题型"覆盖的最优候选。

## 六、后续登记规则

自本文件建立起，以下事件必须在本文件追加登记：

1. 每次新增/更换数据集（来源、规模、获取方式、合规留痕）；
2. 每次数据增强/预处理参数变更（参数值、种子、影响行数、生成的 data_manifest 哈希）；
3. 每次主干模型更换（来源、选型评估报告位置）。

## 七、2026-09-08 训练集题型重采样（v0.4 训练轮，B1）

| 项 | 记录 |
| --- | --- |
| 动因 | v0.3 golden 300 中 4 条判断题错 → 题型宏 F1 0.8522；训练集判断题仅 256/9,893（2.6%） |
| 脚本 | `scripts/rebalance_train.py`（幂等护栏：备份已存在或已含 oversampled 列即拒绝执行） |
| 参数 | 判断题 ×4（每行总份数 4）、解答题 ×1.5（按行序偶数行 2 份/奇数行 1 份）、选择题不动；无随机种子（确定性复制） |
| 影响行数 | train 9,893 → 11,721（判断题 256→1,024、解答题 2,120→3,180、选择题 7,517 不变）；净增 1,828 份副本，副本行 `oversampled=1`，**文本与全部标签列零修改**（pandas 逐单元格比对验证） |
| 备份 | `data/processed/train.pre_rebalance.csv`（重采样前原版字节级副本，不入 git；git 历史亦含原版） |
| 复制报告 | `data/processed/rebalance_report.json`（前后分布、净增副本数、参数） |
| 数据指纹 | data_manifest 已由 `scripts/preprocess_all.py` 刷新；v0.4 manifest 的 data_ref 记录新 train.csv sha256（371d49eb…） |
| val/test | **未改动**（val 1,917 / test 1,039 保持固定基准） |
| 模型产出 | `models/versions/v0.4-20260906/`（golden 题型宏 F1 0.9406，温度 T=0.9984，详见 `reports/evaluation/v0.4-20260906_*.json`） |

