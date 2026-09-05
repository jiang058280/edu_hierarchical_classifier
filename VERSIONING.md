# 版本与发布流程（VERSIONING）

对齐 knowforge-rag-platform 的版本治理思路：**模型版本、代码版本、发布验收三线并行**。

## 一、模型版本（运行时资产）

- 目录：`models/versions/<version>/`（三头权重 + `manifest.json`，权重不入 git）；
- 注册表：MySQL `model_versions` 表 + `active_model_pointer` 单行指针；
- 状态机：`STAGED`（注册）→ 门禁通过 → `ACTIVE`（服务加载）→ 新版本激活后旧版本 `ARCHIVED`；
- 服务启动时强校验 active 版本（manifest + 三头 + backbone_ref），缺失即拒绝启动。

### 版本命名

```
v{major}.{minor}-{YYYYMMDD}-{HHMMSS}     # 训练产物，如 v0.2-20260905-143000
v0.1-base                                 # 旧版权重迁移基线
```

### 标准流程

```powershell
# 1. 训练
venv\Scripts\python scripts\train_model.py --version v0.2-20260905

# 2. 注册 -> golden set 评估 -> 门禁 -> 激活（任一环节不达标即阻断）
venv\Scripts\python scripts\rebuild_model_version.py --version v0.2-20260905 --gate --activate

# 3. 重启服务加载新版本
venv\Scripts\python -m uvicorn app:app --port 7860

# 回滚（可选）
venv\Scripts\python scripts\rebuild_model_version.py --version v0.1-base --skip-evaluation --activate
# 或通过 API：POST /api/v1/models/rollback
```

## 二、代码版本（git）

| tag | 含义 |
| --- | --- |
| `v0.1-demo` | 改造前 Gradio 单体基线（冻结） |
| 后续 | 按功能里程碑打 tag（如 `v1.0-enterprise`） |

提交规范（沿用约定式前缀）：`feat:` / `fix:` / `refactor:` / `chore:` / `docs:`。

## 三、发布验收

每次封版前运行：

```powershell
venv\Scripts\python scripts\check_project_guardrails.py   # 工程守护
venv\Scripts\python -m pytest tests -q                    # 单元测试
venv\Scripts\python scripts\verify_release.py             # 汇总验收 -> reports/verification/v1_release_latest.json
```

`verify_release.py` 检查项：guardrails / pytest / 数据资产（labels+golden set）/ 模型版本目录规范 / MySQL 表结构就绪。

## 四、评估报告与门禁基线

- 评估报告：`reports/evaluation/<version>_evaluation.json`（golden set 300 条）；
- 门禁阈值（`EDU_GATE_*` 可调）：subject_acc ≥ 0.85、type_f1 ≥ 0.85、knowledge_f1 ≥ 0.40、cascade_acc ≥ 0.65、avg_latency_ms ≤ 1500；
- 门禁是激活的唯一通行证：`rebuild_model_version.py --gate --activate` 中门禁失败直接阻断，版本停在 STAGED。

## 五、Bad Case 与再训练闭环

```powershell
venv\Scripts\python scripts\export_bad_cases.py        # 导出 eval_sets/bad_cases.json
# 人工复核 corrected_* 与 review_status 字段
# 复核通过样本并入训练集 -> 回到第一节训练新版本 -> 门禁对比后激活
```
