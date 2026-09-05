# 教育题目层级分类系统

对输入的题目文本自动完成 **学科 → 题型 → 知识点** 三级分类，并输出各级置信度。
基于本地已训练的 `bert-base-chinese` 模型进行迁移微调，采用 **共享主干 + 多任务三头** 结构，
一次前向同时得到三级结果，CPU 动态量化推理 < 3 秒。

## 一、快速开始

```bash
# 1. 创建虚拟环境（已在 D:\edu_hierarchical_classifier\venv）
python -m venv venv

# 2. 安装依赖（清华镜像）
venv\Scripts\pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 下载数据（K-12EduBench，9 学科）
venv\Scripts\python src\download_data.py

# 4. 数据清洗与标签映射
venv\Scripts\python src\data_loader.py

# 5. 模型评估（生成 logs\evaluation_report.json）
venv\Scripts\python src\evaluate_models.py

# 6. 多任务微调训练（自动检测 GPU/CPU）
venv\Scripts\python src\train.py

# 7. 测试集端到端验收（生成 logs\test_evaluation.json）
venv\Scripts\python src\evaluate_test.py

# 8. 启动后端（"智慧教研平台"后台 + 原分类器）
venv\Scripts\python app.py
# 浏览器直接访问 http://127.0.0.1:7860       → 智慧教研平台后台（推荐入口）
# 原 Gradio 分类器独立访问 http://127.0.0.1:7860/classifier

# 9. （可选）直接以 file:// 打开 platform.html 亦可
#    左侧 iframe 自动指向 7860/classifier，右侧联动展示真实分类结果
```

## 二、系统架构

```
用户输入（题目文本）
        │
        ▼
共享编码器（BERT base 中文，冻结前 80% 层）
  输出：文本语义向量 [CLS] (768维)
        │
        ├──────────────┬──────────────┐
        ▼              ▼              ▼
  学科分类头       题型分类头      知识点分类头
  Linear+Softmax   Linear+Softmax   Linear+Softmax
  输出：学科        输出：题型        输出：知识点
  + 置信度          + 置信度          + 置信度
        │              │              │
        └──────────────┴──────────────┘
                     ▼
              返回三级分类结果 + 置信度
```

- **共享主干**：`bert-base-chinese`（12 层 / 768 维），冻结 embeddings 与前 80% 层，仅微调后 20% 层 + pooler。
- **三个独立头**：学科（9 类）、题型（按规则推断的细化题型）、知识点（一级知识点 + 学科前缀）。
- **多任务训练**：三头交叉熵加权求和（学科 0.4 / 题型 0.3 / 知识点 0.3）反向传播。
- **CPU 加速**：动态量化（int8）、`no_grad`、输入 `lru_cache` 缓存、batch_size=1。

## 三、模型选型说明

用户提供多份训练好的中文文本分类模型（均为 THUCNews 新闻 10 类任务），经逐一探测识别：

| 候选模型 | 架构 | 结论 |
| :--- | :--- | :--- |
| `003_bert/model/bert-base-chinese` | BERT 12 层 / 768 维（标准 HF） | ✅ **选中为主干** |
| `003_bert/bert_model.pt` | BERT 12 层 + 新闻分类头 | 主干同源，头须剥离，作备选 |
| `014_distill/stu_model.pt` | 蒸馏学生 BERT 4 层 / 240 维 | 非标准结构、容量不足，兜底 |
| `012_quantization / 013_pruning` | `nn.Linear(10000,10000)` 演示玩具 | 与文本分类无关，排除 |
| `001_randomforest / 002_FastText` | 传统 ML | 无法作共享主干，排除 |
| `004_LLM` | 无本地权重 | 依赖外部服务，排除 |

**选型理由**：bert-base-chinese 是全部候选深度模型的主干来源，标准格式加载最稳，
中文通用语义适合教育题目迁移，CPU 量化后单条推理约 0.3~0.8s，满足 < 1.5s 硬性门槛。

## 四、目录结构

```
D:\edu_hierarchical_classifier\
├── app.py                    # Gradio Web 界面（HEAD_JS 含 platform 桥接）
├── platform.html             # 智慧教研平台后台（iframe 嵌入分类器 + 右侧联动模块）
├── config.yaml               # 配置文件（硬件/路径/超参数）
├── requirements.txt          # 依赖清单
├── CLAUDE.md                 # 提示词文档（项目规范）
├── tools\
│   ├── platform_check.js     # platform.html 静态自检
│   ├── platform_bridge_test.js  # platform.html 端到端联动自测（CDP）
│   └── cdp_bridge_test.js    # Gradio 界面端到端自测（CDP）
├── user_models\models_path.txt   # 原始模型路径与选型记录
├── data\
│   ├── raw\k12edubench\      # K-12EduBench 原始数据（9 学科 JSON）
│   ├── processed\            # 清洗后 train/val/test.csv + labels.json
│   └── ...
├── models\
│   ├── pretrained_backbone\bert-base-chinese\         # 迁移来的主干
│   ├── pretrained_backbone\bert-base-chinese-finetuned\  # 微调后主干
│   └── heads\                # 三个分类头权重
├── src\
│   ├── utils.py              # 工具函数（项目根路径/环境重定向/日志）
│   ├── data_loader.py        # 数据加载、标签映射、划分
│   ├── download_data.py      # 数据下载（GitHub + 镜像兜底）
│   ├── model.py              # 多任务层级分类模型
│   ├── train.py              # 多任务微调训练
│   ├── predict.py            # 推理预测（动态量化）
│   ├── evaluate_models.py    # 候选模型评估脚本
│   └── evaluate_test.py      # 测试集端到端验收评估
├── logs\                     # 评估报告、训练日志、运行日志、反馈记录
└── venv\                     # Python 虚拟环境（D 盘隔离）
```

## 五、数据说明

- **K-12EduBench**（[GitHub](https://github.com/shida-edu4ai/K-12EduBench)）：3,259 道题，9 学科 × 三级知识点，含客观/主观题型与解析。
- 标签映射：
  - 学科 = 数据 `学科` 字段（数学/物理/化学/生物/语文/英语/历史/地理/政治）
  - 题型 = 规则推断（客观题含选项→选择题，无选项→判断题；主观题含"填空"→填空题、含"证明"→证明题、其余→解答题）
  - 知识点 = `一级知识点`，跨学科加前缀防冲突，小类并入"学科::其他"
- 划分：训练 70% / 验证 15% / 测试 15%（按学科分层抽样）。

## 六、实测验收结果

训练环境：NVIDIA GeForce RTX 3050 6GB（GPU 全流程训练，全程约 5 分钟）；推理自动适配 GPU/CPU。

| 指标 | 目标 | 实测（测试集） | 达标 |
| :--- | :--- | :--- | :--- |
| 学科分类 Accuracy | > 85% | **97.2%** | ✅ |
| 题型 F1-Macro | > 85% | **91.9%**（acc 96.6%） | ✅ |
| 知识点 F1-Macro | 视数据量 | 55.5%（50 类小样本，acc 73.9%） | ⚠️ |
| 级联准确率（三级全对） | 尽量高 | 71.3% | — |
| 单条推理耗时 | < 3 s | **16.2 ms（GPU）** | ✅ |

> 知识点 F1 偏低的主因：K-12EduBench 仅 3,259 条样本、知识点 50 类，多数类样本极少。
> 属小样本固有瓶颈，可引入更多数据或调低合并阈值改善（见 `data_loader.py`）。

### 硬件策略
- 代码自动检测：`device = torch.device("cuda" if torch.cuda.is_available() else "cpu")`，无需改业务逻辑。
- CPU 模式下自动启用**动态量化**（int8）；GPU 模式下禁用量化以保精度。
- 推理单条 batch=1 + `@lru_cache` 输入缓存 + `no_grad`。

## 七、常见问题

- **推理超时**：检查 `config.yaml` 中 `use_dynamic_quantization`，必要时导出 ONNX 或切换蒸馏学生模型。
- **数据集下载失败**：`download_data.py` 已内置国内镜像（ghproxy.net）兜底。
- **标签不达预期**：3,259 条为小样本，可在 `data_loader.py` 中调低合并阈值或引入更多数据。
