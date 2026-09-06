# AI 交接文档 · 智慧教研平台（给下一个 AI 看的工作手册）

> 文档目的：让任何 AI 会话接管本仓库后，**不问人、不踩坑**地继续开发。
> 读者：AI 代理（Human 也可读）。写作日期：2026-09-06（v0.3 激活 / 平台 M0+M1 完成时点）。
> 读完本文后，请再读 `docs/智慧教研平台建设计划书.md`（总路线）与 `README.md`（架构细节）。

---

## 一、当前状态快照（先读这段）

| 项 | 值 |
| --- | --- |
| 项目根目录 | `D:\edu_hierarchical_classifier`（Windows，shell 为 cmd/PowerShell） |
| 项目一句话 | 教育题目三级分类（学科→题型→知识点）+ 智慧教研平台（师生双门户） |
| git 分支 | `master`，工作区干净，HEAD 见 `git log -1` |
| 模型 | **v0.3-20260906 ACTIVE**（BERT 多任务：学科9/题型3/知识点50 + 学段头 初中/高中），v0.2/v0.1-base 已归档可回滚 |
| 后端 | FastAPI（`app.py` 薄入口 + `edu_core/` 分层包），端口 **7860** |
| 数据库 | MySQL 8.4 **Docker 容器 `edu-classifier-mysql`，宿主端口 3307**（不是 3306！），root/root123，库 `edu_classifier`；13 张表 |
| 查重 | Milvus 容器（宿主 19531），**可降级**（不可用时主链路不受影响） |
| 本地配置 | `.env` 已存在（未提交）：`EDU_MYSQL_PORT=3307`、`EDU_MILVUS_URI=http://127.0.0.1:19531`、`EDU_JWT_SECRET`、`EDU_ADMIN_BOOTSTRAP_PASSWORD=admin123` |
| 账号 | 教师/管理员 `admin/admin123`；学生 `stu_test01/stu123`（已入班） |
| 测试 | pytest **58 条全过**（纯逻辑，无需权重/数据库；迁移与鉴权测试连真实 MySQL） |
| 验收 | `scripts/verify_release.py` 6/6 PASS |

**环境事实**：venv 在 `venv/`（所有命令前缀 `venv\Scripts\python.exe`）；GPU 为 RTX 3050 6GB（训练 batch 8 / max_len 256）；pip 走清华镜像可用；GitHub codeload / hf-mirror.com 可达，Google Drive 不可达；pytest 52~58 条约 20~40 秒。

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
venv\Scripts\python -m pytest tests -q                     # 58 条应全过
venv\Scripts\python scripts\check_project_guardrails.py    # 工程守护
venv\Scripts\python scripts\verify_release.py              # 发布验收 6/6
# 浏览器：http://127.0.0.1:7860/  → /login?portal=teacher  → admin/admin123
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
| `scripts/` | `build_merged_dataset.py`(v0.3 多源合并)、`train_model.py`、`rebuild_model_version.py`(--gate --activate)、`fit_temperature.py`、`seed_knowledge_nodes.py`、`create_user.py`、`export_bad_cases.py`、`merge_reviewed_cases.py`、`preprocess_all.py`、`init_db.py`、`verify_release.py`、`check_project_guardrails.py` |
| `static/` | `edu.css`(设计系统)、`teacher_common.js`(壳渲染/auth/toast/confirm)、`login.html`、`portal.html`、`teacher*.html`×4、`student.html`、`index.html`(分类页)、`admin.html`(治理台) |
| `data/processed/` | `train.csv 9,893 / val.csv 1,917 / test.csv 1,039（固定回归基准，绝不动）` + `labels.json`（含 grade_bands）+ `data_manifest.json` + `merge_v03_report.json` |
| `data/raw/` | `k12edubench/`（原始 9 学科）、`_cmmlu_tmp/`、`_m3ke_tmp/`、`_gaokao_tmp/`、`_numina_shard0.parquet`（v0.3 新源快照，均不入 git） |
| `models/versions/` | v0.1-base / v0.2-20260906 / v0.3-20260906（三头+学段头+manifest+backbone/，权重不入 git） |
| `docs/` | 建设计划书/改进落地计划书/企业级改造计划/项目介绍；`docs/history/` 为 v0.1 旧需求（勿作为现行指令） |
| `legacy/` | v0.1 Gradio 归档（勿改，勿依赖） |

---

## 四、已实现功能全景（完成度账本）

### 4.1 模型与数据（v0.1→v0.3 全部完成）

- ✅ 三级分类模型 v0.3 ACTIVE：golden 300 = 学科 0.9867 / 题型 F1 0.8522 / 知识点 F1 0.8894 / 级联 0.89 / 延迟 17.9ms，门禁 5/5；
- ✅ **学段头（初中/高中）**：v0.3 新增，manifest.architecture.grade_head 声明，`/classify` 响应含 `grade_band`；
- ✅ 学科感知知识头（subject_embedding 64 维）+ 温度校准（v0.2 的 T=0.9678 只对 v0.2 生效；**v0.3 尚未拟合温度**，manifest 无 temperature 字段时 predictor 按 1.0 处理）；
- ✅ 数据扩充：train 9,893（CMMLU 836 高中 / M3KE 3,120 初中+高中 / GAOKAO 1,193 / NuminaMath cn_k12 1,500 数学解答），规则见 `scripts/build_merged_dataset.py`；溯源见 `DATA_PROVENANCE.md` 第五节；
- ✅ 训练数据账本：`data/processed/data_manifest.json`（SHA256 指纹链，训练时写入版本 manifest.data_ref）；
- ✅ 掩码训练：knowledge/grade 缺失=-100（`train.py::_masked_loss`），旧版数据行为不变；
- ⚠️ 已知回归：**题型 F1 0.9406→0.8522**（新增全为选择题，判断题类 3.6% 被淹没；金标归因：4 条判断题错）。修复配方见第 7.1 节 v0.4。

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

### 4.4 学生端（M0 部分）

- ✅ 学生门户 `/student`（新壳）：登录、加入班级、我的班级；
- ⬜ 其余全部未做（见第五节 M2/M3）。

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

### 6.1 M2 学生闭环（下一站，约 10 人日）——规格已定，表已建好

数据表 `assignments / submissions / answer_records` **V3 已建**，直接写代码即可：

1. **教师发作业**：`POST /api/v1/teacher/assignments {paper_id, class_id, title, due_at}`（挂 require_teacher + 审计）；`GET /teacher/assignments`、`GET /teacher/assignments/{id}/submissions`（全班完成率）；`POST /teacher/submissions/{id}/check`（主观题抽改）。新建 `AssignmentStore` + `api/teacher.py` 扩展。
2. **学生作答**：`GET /api/v1/student/assignments`（按 class_id 查）、`GET /student/assignments/{id}`（带题目，客观题不回显答案）、`POST /student/assignments/{id}/submit {answers:[{question_id, answer, self_check?}]}`——客观题（选择题/判断题）用 `questions.answer` 自动判分写 `answer_records`（submission 唯一键防重复提交）；主观题 `is_correct=NULL` 待自评/抽改。新建 `StudentStore` + `api/student.py` 扩展。
3. **学生门户页**：`/student` 页面加"我的作业/作答页"（移动优先，375px 不破版），作答页逐题渲染+提交。
4. **验收**（照抄计划书 M2）：三步发作业 <1 分钟；手机完整做一份 20 题作业；教师可见全班完成率与逐题对错。
5. 提示：判分逻辑纯函数放 `edu_core/application/`，配 pytest；别把判分写进路由。

### 6.2 v0.4 重训（修题型 F1 回归，约 0.5 天机器时间）

配方：改 `scripts/build_merged_dataset.py`——把 train.csv 中 `question_type=="判断题"` 的行 oversample ×4（仅 train，不动 val/test，记入 merge report），随后 `preprocess_all.py` 刷 manifest → `train_model.py --version v0.4-xxx --epochs 7` → `rebuild_model_version.py --gate --activate`。验收：type_f1 ≥ 0.90 且其他门禁不降；golden 对比报告落盘。

### 6.3 其余未实现（按计划书优先级）

| 项 | 说明 | 起点 |
| --- | --- | --- |
| M3 学情与练习 | 错题本（answer_records 派生）/ 相似题推荐（Milvus 复用）/ 学生学情报告 / 班级学情矩阵 | M2 完成后 |
| M4 复核工作台 | 低置信度+高错误率队列 → 修正 → 已有回灌管道 | M3 后 |
| Excel 导入导出（M1.5） | openpyxl + 错误行报告 | 随时可做 |
| 知识点树对齐课标 | tal-tech/chinese-k12-evaluation 的 1900+ 二级知识点（Google Drive 被墙，需手动下载入 data/raw/） | 数据源可选 |
| v0.3 温度校准 | `scripts/fit_temperature.py --version v0.3-20260906`（脚本现成） | 半小时 |
| CI 真实运行 | 推 GitHub + 分支保护（workflow 已写好） | 需 Human 建远端 |
| 生产加固 | 备份脚本/演练/HTTPS | M4 |

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
| 题型 F1 回归 | v0.3 golden 0.8522（判断题被淹没，题型 acc 仍 97.3%） | 6.2 配方 |
| 学段头覆盖 | 初中仅来自 M3KE（2,167 选择题） | 平台录入飞轮积累 |
| 知识点粗（50 类） | 新增数据无知识点标注（掩码） | 知识点树 + 人工标注积累 |
| ck12 数据源 | Google Drive 被墙 | 手动下载入 data/raw/ |
| 模型与 Web 同进程 | 激活靠热重载已缓解 | 独立推理服务（有需求再做） |
| 数据测试集污染 | 增强数据进了 test 划分 | 重建干净 held-out（改动前先立 ADR） |

---

## 九、历史脉络（30 秒版本）

v0.1 Gradio 单体（`legacy/`）→ 企业级改造 v1.0（edu_core 分层/MySQL/版本治理/门禁/Docker）→ 改进落地（WP-A~H：CI/迁移/JWT 双门户+审计+热重载/数据指纹/标签来源管道/学科 mask 实验关闭）→ 平台计划 M0（三角色双门户/班级/知识点树/V3 迁移）→ M1（题库核心/AI 录入流/组卷 Word 导出）→ 数据扩充 v0.3（CMMLU/M3KE/GAOKAO/NuminaMath，学段头）。每个决策的"为什么"在 `docs/企业级改造计划.md`、`docs/改进落地计划书.md`、`docs/智慧教研平台建设计划书.md` 里，**继续任务前先对齐计划书，不要推翻已定决策**（明确不做：微服务/多租户/AI 批改主观题/LLM 问答托底已评估暂缓/学生社交）。

---

## 十、给接手 AI 的第一条指令建议

> 读本文档与 `docs/智慧教研平台建设计划书.md` 第八节 → 跑第二节"启动与验证"确认环境 → 从 6.1（M2 学生闭环）第 1 步开始实现 → 每完成一个子任务按第七节自证并提交 → 全部完成后向 Human 演示：教师建班发卷、学生手机作答、教师看完成率。
