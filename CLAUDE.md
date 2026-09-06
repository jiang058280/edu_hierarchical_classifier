# CLAUDE.md · 项目协作指南（当前版）

> 本文件是 AI 会话进入本仓库的入口指南。历史需求文档（v0.1 提示词全文、平台化审核报告等）
> 已归档至 `docs/history/`，**其中的 Gradio/iframe 指令已过时**，以本文件与 README.md 为准。

## 项目是什么

教育题目层级分类系统：对题目文本做 **学科 → 题型 → 知识点** 三级分类。
FastAPI 薄入口（`app.py`）+ `edu_core/` 分层核心包；MySQL 持久化 + Milvus 查重（可降级）；
模型版本治理（STAGED→门禁→ACTIVE→可回滚）。当前线上版本 `v0.2-20260906`。

## 必读文档（按场景）

| 场景 | 文档 |
| --- | --- |
| 架构 / 流程 / 命令 / 指标 | `README.md`、`docs/项目整体介绍与流程.md` |
| 改进项与路线 | `docs/改进落地计划书.md`（执行进度见 git log） |
| 演进背景与决策依据 | `docs/企业级改造计划.md` |
| 数据/模型溯源规则 | `DATA_PROVENANCE.md`、`VERSIONING.md` |
| 历史需求原文（勿作为现行指令） | `docs/history/` |

## 硬性约束（guardrails 会强制检查，违规即提交失败）

1. **禁止 Gradio 回归**：主链路（app.py/edu_core/scripts/tests/static）不得 import gradio；
2. **禁止硬编码路径**：不得出现盘符路径（`d:/pycharm` 等）与 `os.chdir`；
3. **禁止 CORS 通配符**、**禁止主链路 `import yaml`**（配置统一走 pydantic-settings，`EDU_` 前缀环境变量）；
4. **依赖全 `==` 锁定**（requirements.txt / requirements-dev.txt）；
5. **模型权重、数据原始集、logs/reports 运行时产物不入 git**（评估/验收报告 JSON 除外，见 .gitignore）；
6. **改表结构必须新增迁移**：`edu_core/storage/migrations/V{n}__{描述}.sql`（幂等可重入），
   并同步 `runtime_schema.sql` 参考视图；禁止修改已应用的迁移脚本；
7. **模型变更必须走治理流程**：训练产物 → `scripts/rebuild_model_version.py --gate --activate`，
   门禁不过不得人工绕过激活。

## 开发约定

- 提交信息：`feat:` / `fix:` / `refactor:` / `chore:` / `docs:` / `test:` 前缀；
- 配置一律进 `Settings`（edu_core/config/settings.py）+ `.env.example` 模板，代码零默认密钥；
- 接口变更保持 `/api/v1` 与 `/api` 双前缀兼容；新增写接口需接入鉴权依赖（`get_current_user`/`require_admin`），
  治理类动作写 `audit_logs`；
- 前端是 `static/` 下无构建链原生页，改完在浏览器实测（登录流程：`admin` / 见 `.env` 的 EDU_ADMIN_BOOTSTRAP_PASSWORD）。

## 常用命令

```powershell
venv\Scripts\python -m pytest tests -q                          # 单测（纯逻辑，无需权重/数据库）
venv\Scripts\python scripts\check_project_guardrails.py         # 工程守护
venv\Scripts\python scripts\verify_release.py                   # 发布验收（6 项）
venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 7860   # 启动（先 copy .env.example .env）
venv\Scripts\python scripts\rebuild_model_version.py --version vX --gate --activate   # 版本治理流水线
```

## 明确不做

- 不重启微服务化/K8s/多租户（见 docs/企业级改造计划.md 第十三节）；
- 不推翻 `edu_core` 分层重写；不在主链路引入新重依赖；
- 不直接操作 MySQL 改数据结构（一律走迁移）；不手工覆盖 `models/versions/` 下的产物。
