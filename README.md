# 教育题目层级分类系统（企业版）

对输入的题目文本自动完成 **学科 → 题型 → 知识点** 三级分类。基于本地微调的 `bert-base-chinese`（共享主干 + 多任务三头），一次前向同时输出三级结果与置信度。

本仓库是 **v0.1-demo（Gradio 单体，见 `legacy/`）的企业级改造版**，工程模式对齐 `knowforge-rag-platform`：薄入口 + 分层核心包 + **模型版本治理** + **评测质量门禁** + **反馈再训练闭环** + MySQL 持久化 + Milvus 语义查重 + Docker 交付。

> 改造范围与决策记录见 `docs/企业级改造计划.md`，改进执行计划见 `docs/改进落地计划书.md`；本文档描述当前（改造后）架构。

## 一、核心能力

| 能力 | 当前实现 |
| --- | --- |
| 三级分类 | 共享 BERT 主干 + 学科/题型 Linear 头 + 知识点 ResidualMLP 头，GPU 自动适配，CPU int8 动态量化 |
| 模型版本治理 | `models/versions/<ver>/` + MySQL 注册表 + active 指针，STAGED → 门禁 → ACTIVE → 可回滚 |
| 启动硬校验 | preflight 校验主干/标签/版本目录；active 版本文件缺失**拒绝启动**（消灭旧版"随机头静默服务"） |
| 评测回归 | golden 测试集（`eval_sets/golden_test_set.json`，300 条固化）+ 指标报告 + 质量门禁脚本 |
| 反馈闭环 | 反馈落库并关联推理留痕（含模型版本）→ Bad Case 导出 → 人工复核 → 再训练 |
| 数据持久化 | 题库/分类留痕/反馈/按日统计全部入 MySQL（替代旧版内存变量与 JSON 文件） |
| 语义查重 | 题目向量（BERT [CLS] 768 维）入 Milvus，录入时 top-K 相似提示（增强组件，可降级） |
| API | FastAPI 正式 REST `/api/v1/*` + 旧契约 `/api/*` 别名 + OpenAPI 文档 + 限流 + 统一错误处理 |
| 前端 | 原生静态页（无构建链）：分类页 `/` + 治理工作台 `/admin` |
| 工程守护 | `check_project_guardrails.py` 阻止 Gradio 回归 / 硬编码路径 / 依赖未锁定 |
| Docker 交付 | `docker compose up` 一键起 mysql + milvus + api |

## 二、技术栈

| 层 | 选型 |
| --- | --- |
| API | FastAPI + Uvicorn + Pydantic v2（pydantic-settings 配置，`EDU_` 前缀环境变量） |
| 模型 | torch 2.11 + transformers 5.x，bert-base-chinese（GPU cu128 / CPU 量化自适应） |
| 业务库 | MySQL 8.4（SQLAlchemy 2.0 Core + PyMySQL，DDL 集中在 `runtime_schema.sql`，不引入 Alembic） |
| 查重 | Milvus 2.5 standalone（etcd + MinIO），COSINE 相似度 |
| 前端 | 原生 HTML/JS/CSS（`static/index.html`、`static/admin.html`） |
| 测试 | pytest（纯逻辑测试，不依赖权重与数据库） |
| 交付 | Dockerfile + docker-compose（mysql/etcd/minio/milvus/api） |

## 三、目录结构

```
edu_hierarchical_classifier/
├── app.py                  # 薄入口：lifespan 预热（preflight→建库→active版本→模型预热）→ 路由注册
├── edu_core/               # 核心包（对齐 qa_core 分层模式）
│   ├── config/             #   settings（pydantic-settings）/ preflight / logging
│   ├── inference/          #   model.py（三头结构）/ predictor.py（版本化加载+量化+embed）
│   ├── data/               #   dataset.py（raw 清洗/标签映射/分层划分）
│   ├── training/           #   train.py（多任务损失 + 早停 + 版本产物）
│   ├── application/        #   service.py（分类编排）/ confidence.py（置信度分级）/ factory.py（单例）
│   ├── storage/            #   migrations/V*__*.sql（版本化 DDL）/ bootstrap.py / stores.py（7 个 Store）
│   ├── security/           #   auth.py（JWT / RBAC 依赖，改进计划 WP-D）
│   ├── governance/         #   model_versions.py（注册/激活/回滚）
│   ├── quality/            #   evaluation.py（golden set 回归）/ gate.py（门禁）
│   ├── dedup/              #   milvus_client.py（语义查重，可降级）
│   ├── observability/      #   阶段耗时
│   └── api/                #   auth/classify/questions/stats/models/pages 路由 + 限流 + 错误处理
├── scripts/                # 运维脚本（见第六节命令表）
├── tests/                  # pytest 纯逻辑测试（含迁移/鉴权/mask 集成测试）
├── static/                 # index.html 分类页 / admin.html 治理工作台 / katex
├── docs/                   # 设计与历史文档（改进计划/介绍与流程；history/ 为 v0.1 需求原文）
├── eval_sets/              # golden_test_set.json、bad_cases.json
├── reports/                # evaluation/（评估报告）verification/（发布验收）
├── models/versions/<ver>/  # 三头权重 + manifest.json + backbone/（权重不入 git，manifest 入 git）
├── legacy/                 # v0.1-demo 旧版归档（Gradio 单体 + 旧 src + 旧前端，可运行）
├── data/processed/         # train/val/test.csv + labels.json + data_manifest.json
├── Dockerfile / docker-compose.yml / .env.example / requirements.txt（锁定版本）
└── VERSIONING.md / DATA_PROVENANCE.md / CLAUDE.md   # 版本流程 / 溯源规则 / AI 协作指南
```

## 四、架构与主链路

```
浏览器 static/index.html
    │  POST /api/v1/classify（限流 + Pydantic 校验 ≤4000 字）
    ▼
edu_core.api.classify ──► ClassificationService（application 层编排）
    ├─ HierarchicalPredictor.predict()      # active 版本权重，GPU/量化自适应
    ├─ confidence 分级                       # ≥0.80 high / 0.60~0.80 medium / <0.60 low
    ├─ ClassificationStore.insert()          # 留痕：三级预测 + 置信度 + model_version + 耗时
    └─ StatsStore.bump_processed()           # 按日累计（原子 upsert）
    ▼
响应：三级标签 + 置信度 + band + 复核建议（+ 录题时的 Milvus 查重提示）
```

### 模型版本治理（对齐 knowforge 知识库版本状态机）

```
scripts/train_model.py --version vX
    └─► models/versions/vX/{三头.pt, manifest.json}
scripts/rebuild_model_version.py --version vX --gate --activate
    ├─ 注册 STAGED（preflight 校验版本目录 + backbone_ref）
    ├─ golden set 评估 → reports/evaluation/vX_evaluation.json
    ├─ 质量门禁：subject_acc≥0.85 / type_f1≥0.85 / knowledge_f1≥0.40
    │             cascade_acc≥0.65 / CPU 延迟≤1500ms
    └─ 通过 → ACTIVE（MySQL active 指针）；旧 ACTIVE 自动 ARCHIVED，可回滚
```

## 五、快速开始

### 5.1 本机运行（复用本机 MySQL）

```powershell
cd D:\edu_hierarchical_classifier

# 1) 配置（可复用 knowforge 的 MySQL 容器：host 127.0.0.1 / port 3306，会自动新建 edu_classifier 库）
copy .env.example .env

# 2) 初始化数据库（建库 + 6 张表）
venv\Scripts\python scripts\init_db.py

# 3) 迁移旧权重为首个受治理版本并激活（首次部署；含 50 条冒烟评估 + 门禁）
venv\Scripts\python scripts\migrate_model_weights.py
venv\Scripts\python scripts\rebuild_model_version.py --version v0.1-base --limit 50 --gate --activate

# 4) 固化 golden 回归集（300 条）
venv\Scripts\python scripts\export_golden_set.py

# 5) 启动 API（preflight + 模型预热完成后才接收流量）
venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 7860
```

访问：

- 分类页：http://127.0.0.1:7860/
- 治理工作台：http://127.0.0.1:7860/admin
- API 文档：http://127.0.0.1:7860/api/docs

### 5.2 Docker Compose（独立一套基础设施）

```powershell
copy .env.example .env
docker compose --env-file .env up -d mysql etcd minio milvus
docker compose --env-file .env build api
docker compose --env-file .env run --rm api python scripts/init_db.py
docker compose --env-file .env run --rm api python scripts/migrate_model_weights.py
docker compose --env-file .env run --rm api python scripts/rebuild_model_version.py --version v0.1-base --limit 50 --gate --activate
docker compose --env-file .env up -d api
```

> 注意：MySQL 宿主机端口默认映射 **3307**（避免与 knowforge 的 3306 冲突），Milvus 映射 19531。本机直跑 API 时用默认 3306/19530 即可。

## 六、常用命令

| 场景 | 命令 |
| --- | --- |
| 初始化数据库 | `venv\Scripts\python scripts\init_db.py` |
| 训练新版本 | `venv\Scripts\python scripts\train_model.py --version v0.2-xxx [--epochs 12]` |
| 注册+评估+门禁+激活 | `venv\Scripts\python scripts\rebuild_model_version.py --version v0.2-xxx --gate --activate` |
| 单独评估 | `venv\Scripts\python scripts\evaluate_core_model.py --version v0.1-base` |
| 门禁校验（CI 用） | `venv\Scripts\python scripts\quality\check_evaluation_gate.py --report reports\evaluation\v0.1-base_evaluation.json` |
| 导出 Bad Case | `venv\Scripts\python scripts\export_bad_cases.py` |
| 迁移旧反馈 | `venv\Scripts\python scripts\migrate_legacy_data.py` |
| 工程守护检查 | `venv\Scripts\python scripts\check_project_guardrails.py` |
| API 端到端冒烟 | `venv\Scripts\python scripts\api_smoke.py`（需 MySQL；Milvus 可选） |
| 发布验收 | `venv\Scripts\python scripts\verify_release.py` |
| 数据预处理 | `venv\Scripts\python -m edu_core.data.dataset` |
| 单元测试 | `venv\Scripts\python -m pytest tests -q` |

## 七、实测基线（v0.1-base，RTX 3050 6GB）

| 指标 | 目标/门禁 | 实测（测试集） |
| --- | --- | --- |
| 学科 Accuracy | ≥ 0.85 | **97.2%** |
| 题型 F1-Macro | ≥ 0.85 | **91.9%**（acc 96.6%） |
| 知识点 F1-Macro | ≥ 0.40 | 55.5%（50 类小样本瓶颈，acc 73.9%） |
| 级联准确率（三级全对） | ≥ 0.65 | 71.3% |
| 单条推理耗时 | ≤ 1500ms | 16.2ms（GPU）/ ~125ms（CPU int8） |

> 知识点 F1 偏低主因：K-12EduBench 仅 3,259 条、50 个知识点类。提升路径：复核修正数据回流（反馈闭环）→ 再训练新版本 → 门禁对比后激活。

## 八、与旧版（legacy/）的差异

| 维度 | 旧版 v0.1-demo | 本版 |
| --- | --- | --- |
| 入口 | Gradio 单体 1369 行 + 隐藏组件 REST hack | FastAPI 薄入口 + 分层包 |
| 题库/反馈/统计 | 内存变量 + JSON 文件（重启丢、单日覆盖） | MySQL 持久化（按日累计） |
| 模型权重 | models/heads 直接覆盖，版本不可追溯 | 版本目录 + manifest + 注册表 + active 指针 + 回滚 |
| 权重缺失 | 静默用随机头服务（仅 WARN） | preflight fail-fast 拒绝启动 |
| 评测 | 一次性脚本，结果不可比 | golden set 固化 + 报告落盘 + 门禁阻断 |
| 反馈 | 无关联、无法再训练 | 关联 classification_id + model_version，可导出再训练 |
| 接口 | CORS "*"、无限流、无文档 | 白名单 CORS、限流、OpenAPI、统一错误处理 |
| 测试 | 7 个 CDP 散装脚本 | pytest 纯逻辑套件 + guardrails + verify_release |

旧版运行方式见 `legacy/README.md`；两者不要同时启动（同端口 7860）。

## 九、安全说明

- **认证与权限（WP-D）**：除 `/health` 与页面路由外，全部接口需 JWT 登录；版本激活/回滚/删题为 admin 专属；治理动作写 `audit_logs` 审计。登录：`POST /api/v1/auth/login`（Swagger Authorize 可调试）；建号：`venv\Scripts\python scripts\create_user.py --username admin --password xxx --role admin`（首次启动 users 表为空时也会按 `EDU_ADMIN_BOOTSTRAP_PASSWORD` 自动创建 admin）；
- `EDU_JWT_SECRET` 鉴权开启时必须为 ≥16 字符非示例值（preflight 与 verify_release 强校验）；`EDU_AUTH_DISABLED=true` 仅限本机调试；
- `.env` 不提交（只提交 `.env.example` 占位模板）；
- CORS 白名单可配置，默认拒绝通配符（guardrails 强制）；
- 接口限流默认 120 次/分钟/客户端；
- 模型权重、venv、缓存、原始数据集均不入 git（见 `.gitignore`）；
- 表结构变更走 `edu_core/storage/migrations/V*__*.sql` 版本化迁移（`schema_migrations` 登记，幂等重放），`runtime_schema.sql` 为只读参考视图。
