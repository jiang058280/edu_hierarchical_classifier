# AI 交接文档 · 智慧教研平台（给下一个 AI 看的工作手册）

> 文档目的：让任何 AI 会话接管本仓库后，**不问人、不踩坑**地继续开发。
> 读者：AI 代理（Human 也可读）。写作日期：2026-09-06；最近更新 2026-09-12（项目 Markdown 题库导入）。
> 总路线以 `docs/剩余工作总计划.md` 为准；`docs/智慧教研平台建设计划书.md` 为当前里程碑索引，架构细节见 `README.md`。

---

## 一、当前状态快照（先读这段）

### 2026-09-27 增量更新（优先于下方 09-26 记录）

2026-09-26 各项增强的全部未提交改动已按功能整理入库（个人文件已 gitignore，未推送远端）。R2.5 已完成教师终审并**通过正式验收**：`eval_sets/rag_eval_formal_v2.jsonl`（136 条 = 原百题 + 36 条概念题）四项阈值全过——recall@5 0.9208、引用有效率 1.0、无来源回答率 0、拒答正确率 0.9655（真实 LLM，报告 `reports/verification/rag_eval_formal_v2_run1.json`）。生产配置三项数据驱动变更已写入 `.env`：`EDU_RAG_MIN_EVIDENCE_SCORE=0.66`（消灭软拒答）、`EDU_RAG_CONVERSATION_MEMORY_ENABLED=true`（回放证明主题记忆关闭时 20/20 追问轮被拒答）、`EDU_RAG_NATIVE_STREAM_ENABLED=true`（浏览器端到端验收通过）。评测知识库版本 `rag-eval-20260927`（id=5）已激活为生产知识库（版本 1 转 ARCHIVED 可回滚）。follow_up 20 条会话回放 20/20 完成（`reports/verification/rag_replay_20260927_*.json`，replayed_pending_review，内容质量待人工抽查）。AI 智能录入已支持 DOCX/PDF/MD/TXT 多格式导入。Reranker 真实联调经用户拍板跳过（半成品模型已删除，恢复路径见任务记录）；OCR 量化基线：字符准确率 92.37%、内容准确率 97.74%（`reports/verification/ocr_quality_20260927.json`），质量门禁仍未通过、发布以人工复核为准。详见 `docs/任务推进记录_20260927.md`。

### 2026-09-26 增量更新（优先于下方历史快照）

最新：回答证据核验功能已实现，配置 `EDU_RAG_GROUNDING_CHECK_ENABLED`，默认关闭，7862 测试进程启用。生成后核验，最多一次重写及再核验，失败拒答；开启时流式正文核验后才发送。真实测试已拦截并修正现有“斜率”Bad Case，最终两题答案/引用正确、跨学段拒答。核验复用同一模型，存在误判，不得宣称消除幻觉或正式评测通过。详见 `docs/回答证据核验接入_20260926.md`，其部署状态优先于下面旧记录。

OCR 测试教师 33、测试版本 3；原扫描 OCR 资料 23 保持未发布，校对派生 Markdown 资料 24 已通过真实 HTTP 发布，7 个子块向量完整。测试版本未激活，正式激活版本仍为 1。7862 已运行复核发布门禁；本轮引用编号修复仅在脚本中验证，尚未重启该服务加载。勿把校对版发布算成 OCR 自动识别验收。

本轮修复重复来源片段引用编号不一致，真实两轮问答验证计算/增减性结论正确且跨学段无证据拒答。但模型仍补充来源未定义的“斜率”，加强提示词也未解决，因此回答严格忠实度未通过。不要以结构检查通过冒充语义验收。见 `docs/校对资料真实问答验证_20260926.md` 及 `docs/任务推进记录_20260926.md`；全量测试以最新发布验收报告为准。正式评测草稿仍待人工确认。

| 项 | 值 |
| --- | --- |
| 项目根目录 | `D:\edu_hierarchical_classifier`（Windows，shell 为 cmd/PowerShell） |
| 项目一句话 | 基于 RAG 与学情画像的个性化智慧学习平台；分类、题库、作业闭环及 RAG/画像主链路已进入持续完善阶段 |
| git 分支 | 以 `git status` / `git log -1` 为准；2026-09-27 已完成历史改动整理提交，工作区仅剩个人文件（已 gitignore） |
| 模型 | **v0.4-20260906 ACTIVE**（BERT 多任务：学科9/题型3/知识点50 + 学段头 初中/高中；判断题重采样训练，golden 题型 F1 0.9406，温度 T=0.9984），v0.3/v0.2/v0.1-base 已归档可回滚 |
| 后端 | FastAPI（`app.py` 薄入口 + `edu_core/` 分层包），端口 **7860** |
| 数据库 | MySQL 8.4 **Docker 容器 `edu-classifier-mysql`，宿主端口 3307**（不是 3306！），库 `edu_classifier`；已应用 V1～V10；凭证只从本地环境读取，不写文档 |
| 查重 | Milvus 容器（宿主 19531），**可降级**（不可用时主链路不受影响） |
| 本地配置 | `.env` 已存在（未提交）：`EDU_MYSQL_PORT=3307`、`EDU_MILVUS_URI=http://127.0.0.1:19531`、`EDU_JWT_SECRET`；`EDU_ADMIN_BOOTSTRAP_PASSWORD` 已置空（A2 整改，users 非空后无作用） |
| 账号 | 管理员 `lgq`（2026-09-07 由 admin 更名，密码线下留存不落仓库）；学生 `stu_test01`（已入班，class_id=2） |
| 测试 | 416 条通过（2026-09-27）；含判分、题型、作业、RAG、画像、批量题库、容量与干净基准回归 |
| 验收 | `scripts/verify_release.py` 标准发布检查；`scripts/verify_m2.py` 真实 MySQL/HTTP/浏览器 20 题闭环，报告含失败与清理状态 |

**环境事实**：venv 在 `venv/`（所有命令前缀 `venv\Scripts\python.exe`）；GPU 为 RTX 3050 6GB（训练 batch 8 / max_len 256）。网络可用性以实际检查为准；发布脚本使用项目内独立临时目录避免 Windows TEMP 权限问题。

---

## 二、启动与验证（每次接手先跑一遍）

```powershell
# 1) MySQL 容器必须在跑（关了服务就挂）；Milvus 可选（查重降级）
docker ps   # 需要 edu-classifier-mysql 为 Up；缺失则 docker start edu-classifier-mysql

# 2) 初始化/升级数据库（幂等，自动应用未执行的 migrations）
venv\Scripts\python scripts\init_db.py

# 3) 起服务（后台跑，预热 ~30 秒后接流量）
venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 7860

# 4) 自证
venv\Scripts\python -X utf8 -m pytest tests -q -p no:cacheprovider
venv\Scripts\python scripts\check_project_guardrails.py    # 工程守护
venv\Scripts\python scripts\verify_release.py              # 发布验收 6/6
# 浏览器：http://127.0.0.1:7860/ → 师生门户；使用线下保存的账号密码
```

页面路由：`/` 门户选择、`/login?portal=teacher|student` 独立登录页、`/teacher` 工作台、`/teacher/bank` 题库管理、`/teacher/entry` AI 录入、`/teacher/papers` 组卷、`/student` 学生门户、`/classify` 分类工具、`/admin` 治理台、`/api/docs` Swagger。

---

## 三、仓库地图（改哪里先看这里）

| 路径 | 内容 |
| --- | --- |
| `app.py` | 薄入口：lifespan 预热（preflight→迁移→引导管理员→模型预热→Milvus 探测）+ 路由注册。**注意 pages 只在根路径注册一次**（见第五节坑 #1） |
| `edu_core/api/` | 路由：`auth.py`(登录+portal)、`teacher.py`(M1 题库/组卷/班级/知识点树)、`student.py`(入班/我的班级)、`classify.py`(旧契约分类)、`questions.py`(旧题库接口)、`stats.py`、`models.py`(版本治理)、`pages.py`(页面路由+no-cache) |
| `edu_core/security/auth.py` | JWT/bcrypt/依赖：`get_current_user`、`require_teacher`、`require_student`、`require_admin`、`ensure_bootstrap_admin` |
| `edu_core/storage/` | `stores.py`（UserStore/ClassStore/KnowledgeNodeStore/QuestionStore(完整字段)/PaperStore/Classification/Feedback/Stats/Audit）、`bootstrap.py`（迁移执行器）、`migrations/V1~V3` |
| `edu_core/inference/` | `model.py`（多任务模型，manifest.architecture 声明 grade_head/subject_embedding）、`predictor.py`（版本化加载/量化/掩码/温度/embed） |
| `edu_core/training/train.py` | 多任务训练（掩码损失：knowledge/grade 缺失=-100） |
| `edu_core/data/dataset.py` | K-12EduBench 清洗管道（含题型规则推断——已知天花板） |
| `scripts/` | 模型与数据脚本之外，`import_question_bank.py` 导入 Markdown 题库，`normalize_question_options.py` 修复早期选项结构，`verify_f1_excel.py` 验收 Excel 模板/导出/100 行异步回导；`verify_release.py` 为发布总验收 |
| `knowledge_base/` | 54 份结构化 Markdown 教学题库；通过 `scripts/import_question_bank.py` 预检与幂等导入，当前业务库已有 1292 道唯一发布题，覆盖 9 学科和初高中 |
| `static/` | `edu.css`(设计系统)、`teacher_common.js`(壳渲染/auth/toast/confirm)、`login.html`、`portal.html`、`teacher*.html`×4、`student.html`、`index.html`(分类页)、`admin.html`(治理台) |
| `data/processed/` | `train.csv 11,721（v0.4 重采样后；train.pre_rebalance.csv 为 9,893 原版备份，不入 git）/ val.csv 1,917 / test.csv 1,039（固定回归基准，绝不动）` + `labels.json`（含 grade_bands）+ `data_manifest.json` + `merge_v03_report.json` + `rebalance_report.json` |
| `data/raw/` | `k12edubench/`（原始 9 学科）、`_cmmlu_tmp/`、`_m3ke_tmp/`、`_gaokao_tmp/`、`_numina_shard0.parquet`（v0.3 新源快照，均不入 git） |
| `models/versions/` | v0.1-base / v0.2-20260906 / v0.3-20260906 / **v0.4-20260906(ACTIVE)**（三头+学段头+manifest+backbone/，权重不入 git） |
| `docs/` | 剩余工作总计划/AI交接文档/改进落地计划书/企业级改造计划/项目介绍；**`docs/adr/`（ADR-01 干净基准，Proposed 待确认）**；`docs/history/` 为 v0.1 旧需求（勿作为现行指令） |
| `legacy/` | v0.1 Gradio 归档（勿改，勿依赖） |

---

## 四、已实现功能全景（完成度账本）

### 4.1 模型与数据（v0.1→v0.4）

- ✅ 三级分类模型 **v0.4-20260906 ACTIVE**：golden 300 = 学科 0.9867 / **题型 F1 0.9406** / 知识点 F1 0.8789 / 级联 0.89 / 延迟 38ms，门禁 5/5；温度 T=0.9984；
- ✅ v0.3（Archived）：学科 0.9867 / 题型 F1 0.8522 / 知识点 F1 0.8894，温度 T=0.9706；v0.2/v0.1-base 已归档；
- ✅ **学段头（初中/高中）**：v0.3 新增，manifest.architecture.grade_head 声明，`/classify` 响应含 `grade_band`；
- ✅ 学科感知知识头（subject_embedding 64 维）+ 每版本独立温度校准（manifest.temperature，缺失时 predictor 按 1.0 处理）；
- ✅ 数据扩充：train 基底 9,893（CMMLU 836 高中 / M3KE 3,120 初中+高中 / GAOKAO 1,193 / NuminaMath cn_k12 1,500 数学解答），规则见 `scripts/build_merged_dataset.py`；溯源见 `DATA_PROVENANCE.md` 第五节；
- ✅ **题型重采样（v0.4 轮，B1）**：`scripts/rebalance_train.py` 对 train 判断题 ×4、解答题 ×1.5（仅复制行、零改标注，副本 `oversampled=1`），train 9,893→**11,721**；备份 `train.pre_rebalance.csv`（不入 git）、报告 `rebalance_report.json`；val/test 未动；溯源见 `DATA_PROVENANCE.md` 第七节；
- ✅ 训练数据账本：`data/processed/data_manifest.json`（SHA256 指纹链，训练时写入版本 manifest.data_ref）；
- ✅ 掩码训练：knowledge/grade 缺失=-100（`train.py::_masked_loss`），旧版数据行为不变；
- ✅ ~~题型 F1 回归（0.9406→0.8522，判断题被淹没）~~ **已由 v0.4 重采样修复（0.8522→0.9406，判断题 golden 错误 4→1）**；已知取舍：v0.4 知识点 F1 -1.05pt（Human 拍板接受，v0.3 可回滚）。
- 📝 **B2 待办**：`docs/adr/ADR-01-clean-benchmark.md` 已起草（Proposed）——bench_clean 干净基准双轨制，**待 Human 确认 ADR 后**才实现 `scripts/build_clean_bench.py`。

### 4.2 平台基座（M0 完成）

- ✅ V3 迁移：users/questions 完整字段扩展 + classes/knowledge_nodes/papers/paper_questions/assignments/submissions/answer_records 七表（**M2 直接可用，无需再迁移**）；
- ✅ 三角色 RBAC + 双门户隔离：登录 `?portal=teacher|student` 校验角色；`require_teacher`/`require_student` 依赖；交叉访问 403（实测）；
- ✅ 班级管理：建班（6 位邀请码）/学生凭码入班/名册查看（实测全通）；
- ✅ 知识点树播种：50 个 `学科::知识点` 节点（`scripts/seed_knowledge_nodes.py`）。

### 4.3 教师端（M1 完成）

- ✅ 题库管理 `/teacher/bank`：完整题目 CRUD（选项 JSON/答案/解析/难度 1-5/学段/年级/知识点/状态 draft-pending-published）+ 五维筛选 + 分页 + 删除（含 Milvus 向量清理 + 审计）；
- ✅ AI 录入确认流 `/teacher/entry`：多题粘贴按题号切分 → 批量预标注 → 卡片逐题改字段 → 入库/存草稿；
- ✅ 组卷与试卷 `/teacher/papers`：题型配比×学段×难度随机抽题 → 勾选剔除 → 保存试卷 → **导出 Word**（python-docx，第一页试卷/第二页答案解析，RFC5987 中文文件名）；
- ✅ 独立登录页 `/login`（分栏式品牌区+悬浮卡，师生分段切换，401 自动跳转带 next 参数）；
- ✅ 工作台 `/teacher`：真实统计卡（题库/试卷/班级/知识点树）+ 班级管理。

### 4.4 作业与学生端（M2 / C1～C5）

- ✅ `grading.py` 纯判分、`question_taxonomy.py` 九学科题型目录、`AssignmentService` 与 `AssignmentStore`。
- ✅ 教师 `/teacher/assignments` 发布作业、全班逐题矩阵、最终分数人工批改；学生 `/student/assignments/{id}` 作答与结果查看。
- ✅ 学生首页蓝灰视觉重做，真实统计/筛选、班级侧栏、异常重试、375px 布局；不得添加假数据或未落地的 RAG 入口冒充功能。
- ✅ 单选/多选/判断/文本输入、缺结构化选项的字母输入、主观题自评；自评单独标识且不计客观题自动分。自动分是可判客观题正确率 ×100，最终分由教师填写，不是逐题加权总分。
- ✅ 提交前隐藏参考答案与解析、截止只读、服务端时钟校准、事务防重复交卷、本人已提交历史在转班后仍可查看。
- ✅ 试卷采用受保护引用而非独立内容快照：入卷原题禁止原地修改/删除；已发布作业的试卷禁止删除。改题需另存新题并重新组卷。
- ✅ 新建作业/提交/作答的时间由应用本地时钟统一写入，事务截止校验不用数据库 `NOW()`，避免 Docker MySQL UTC 与 Windows 本地时间相差8小时；历史行不做批量改写。迁移部署时需明确应用时区。
- ⬜ C5 技术验收后等用户确认，再整理提交并启动 R1；不跨模块自动推进。RAG、画像与个性化推荐仍是后续目标。

### 4.5 治理与质量（持续有效）

- ✅ 模型版本治理：STAGED→门禁→ACTIVE→ARCHIVED→可回滚 + 热重载（激活后无需重启）；
- ✅ 黄金回归集 300 条固化 + 门禁阈值（subject≥0.85/type≥0.85/knowledge≥0.40/cascade≥0.65/latency≤1500ms）；
- ✅ CI workflow 文件就绪（`.github/workflows/ci.yml`）——**仓库无远端，从未真实运行**，推送后生效；
- ✅ guardrails（禁 Gradio/硬编码路径/os.chdir/CORS */主链路 yaml；依赖全 `==`；结构完整性检查）。

---

## 五、硬性禁令与踩过的坑（违反即返工，全部有前科）

1. **路由注册纪律（踩过 #1 的大坑）**：`pages.py` 的页面路由**只许在根路径注册一次**（`app.include_router(pages.router)`），严禁放进 `/api/v1` 前缀循环——否则页面 HTML 会遮蔽同路径 API（曾导致 `/api/v1/teacher/papers` 返回 HTML，前端全挂）。新增 API 一律加在 teacher/student 等业务 router 并进前缀循环。
2. **改表结构必须走迁移**：新增 `migrations/V{n}__{描述}.sql`（幂等可重入，DDL 带 IF NOT EXISTS），并同步 `runtime_schema.sql` 参考视图；禁止修改已应用的迁移脚本。
3. **guardrails 强制项**（提交前必跑）：禁 import gradio、禁硬编码盘符路径、禁 os.chdir、禁 CORS `*`、禁主链路 import yaml、依赖必须 `==` 锁定。
4. **前端规范**：页面用 `edu.css` 设计系统 + `teacher_common.js`（壳渲染/401 跳登录带 next）；每个页面脚本**第一行必须是 `const $ = (id) => document.getElementById(id);`**（三次踩坑）；接口响应读数组前加 `|| []` 防御；下拉选项优先写死在 HTML；**不要用 PowerShell 内联命令改代码文件**（转义会把文件改坏，已有事故）——改代码用 Edit/Write 工具或 Python 文件补丁。
5. **训练纪律**：产物必须走 `train_model.py`（自动写 manifest.architecture/loss_weights/data_ref/temperature）→ `rebuild_model_version.py --gate --activate`；**test.csv 是固定回归基准，任何数据处理不得改动它的划分**；`build_merged_dataset.py` 有幂等护栏（检测到已并入源会拒绝）。
6. **Windows shell 陷阱**：多行 `&&` 链和内联 python -c 常静默失败；中文输出会被控制台吞——**写临时 .py 文件跑，输出重定向到文件再 Read**；临时脚本用完即删、绝不提交（已两次混入提交）。
7. **数据合规**：未成年人数据最小化；新增数据源必须登记 `DATA_PROVENANCE.md`。
8. **每次提交前**：`ruff check .` + `guardrails` + `pytest` 三绿；提交信息用 `feat:/fix:/chore:/docs:` 前缀。

---

## 六、未实现功能清单（按优先级，含"从哪开始"）

### 6.1 当前交付点：C5 验收，下一站 R1 教育知识库

M2 使用现有 V3 七表，未新增迁移。先向用户展示本轮截图和验收结果；用户确认后才进入 `docs/剩余工作总计划.md` 的 R1.1（知识资料元数据、存储和版本治理）。不能重新从教师作业发布开始实现。

复测运行 `scripts/verify_m2.py`：应用启动后，设置 `NODE_PATH` 指向带 Playwright 的依赖目录，使用已安装的 Edge 无头浏览器。脚本临时创建管理员、教师、学生及独立班级/20题试卷；凭证仅保存在内存并经 stdin 传给浏览器；finally 按本次账号 ID 与用户名核对后清理测试数据。报告与截图保存至 `reports/verification/m2qa_*/`，不更改 `stu_test01` 的作答。

Git 整理在用户确认此模块后执行，保留 `.claude/`、`docs/adr/` 及用户已有文档修改；不得 `git add .`。

### 6.2 v0.4 重训（已完成，以下仅为历史配方，不重复执行）

配方：改 `scripts/build_merged_dataset.py`——把 train.csv 中 `question_type=="判断题"` 的行 oversample ×4（仅 train，不动 val/test，记入 merge report），随后 `preprocess_all.py` 刷 manifest → `train_model.py --version v0.4-xxx --epochs 7` → `rebuild_model_version.py --gate --activate`。验收：type_f1 ≥ 0.90 且其他门禁不降；golden 对比报告落盘。

### 6.3 其余未实现（按计划书优先级）

| 项 | 说明 | 起点 |
| --- | --- | --- |
| R1 / R2 / R3 | 教育知识库 → 带引用 RAG 问答 → 学情画像、错题与巩固推荐（吸收原 M3） | C5 用户确认后，按模块推进 |
| M4 复核工作台 | 低置信度+高错误率队列 → 修正 → 已有回灌管道 | M3 后 |
| ~~M1.5 数据批量通道~~ | ✅ Markdown 题库入库、Excel 模板/异步导入/筛选导出、DOCX 切题与 AI 预标注均已完成 | — |
| 知识点树对齐课标 | tal-tech/chinese-k12-evaluation 的 1900+ 二级知识点（Google Drive 被墙，需手动下载入 data/raw/） | 数据源可选 |
| ~~v0.3 温度校准~~ | ✅ 已完成（A1，T=0.9706）；v0.4 温度 T=0.9984 亦已拟合 | — |
| CI 真实运行 | 推 GitHub + 分支保护（workflow 已写好） | 需 Human 建远端 |
| 生产加固 | ~~备份脚本/演练~~（✅ A3 完成，`backup_db.py`/`restore_db.py`，Windows 计划任务用户拍板不做）；HTTPS 待定 | M4 |
| bench_clean 干净基准（B2） | ADR-01 已起草待 Human 确认，确认后实现 `scripts/build_clean_bench.py`（约 1 天） | ADR 批准后 |

---

## 七、验证与自证方法（完成任何功能后照此执行）

1. `venv\Scripts\python -m ruff check .` → 0 违规；
2. `venv\Scripts\python scripts\check_project_guardrails.py` → 通过；
3. `venv\Scripts\python -X utf8 -m pytest tests -q` → 全过（新增功能必须带测试，模式参考 `tests/test_grade_head.py` 的纯逻辑+monkeypatch 风格）；
4. 浏览器实测（重启服务后）：逐页点击验证 + `tab.playwright.evaluate` 检查数据填充；截图自查布局（侧边栏 224px / 内容 margin-left / 无元素被遮挡）；
5. 涉及模型/数据：跑对应 golden 评估并落盘报告到 `reports/evaluation/`；
6. 阶段收尾：`verify_release.py` 6/6 → git 提交（信息用约定前缀，临时脚本绝不提交）。

---

## 八、已知技术债与修复方向

| 债 | 现状 | 方向 |
| --- | --- | --- |
| ~~题型 F1 回归~~ | ✅ v0.4 重采样已修复：golden 题型宏 F1 0.8522→0.9406（判断题错误 4→1）；取舍：知识点 F1 -1.05pt（Human 接受） | 若复发走类加权损失 Plan B |
| 学段头覆盖 | 初中仅来自 M3KE（2,167 选择题） | 平台录入飞轮积累 |
| 知识点粗（50 类） | 新增数据无知识点标注（掩码） | 知识点树 + 人工标注积累 |
| ck12 数据源 | Google Drive 被墙 | 手动下载入 data/raw/ |
| 模型与 Web 同进程 | 激活靠热重载已缓解 | 独立推理服务（有需求再做） |
| 数据测试集污染 | 增强数据进了 test 划分 | **ADR-01 已起草（Proposed）**：bench_clean 双轨制，待 Human 确认 |

---

## 九、历史脉络（30 秒版本）

v0.1 Gradio 单体 → 企业级分层、MySQL、模型版本治理 → 师生门户 M0 → 题库与组卷 M1 → 数据扩充及 v0.4 重训 → M2 作业数据闭环 → 后续 R1 知识库、R2 带引用问答、R3 画像与巩固推荐。决策依据见《剩余工作总计划》《企业级改造计划》和《改进落地计划书》。早期“暂缓 LLM 问答”的结论已由用户确认的 RAG 主线替代；仍不做微服务化、多租户、学生社交，也不把主观题自评冒充 AI 批改。

---

## 十、给接手 AI 的第一条指令建议

> 先读 `docs/剩余工作总计划.md` 与最新验收报告 → 确认用户是否已验收 C5 → 确认后进入 R1.1，不重复已完成的 M2；每个模块完成先展示效果，等用户确认后再做下一模块。
