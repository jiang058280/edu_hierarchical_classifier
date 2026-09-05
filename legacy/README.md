# legacy/ — v0.1-demo 旧版归档

本目录保存企业级改造前（git tag `v0.1-demo`）的原始实现，仅供参考与回退，**不进入新主链路**。

## 内容

| 文件/目录 | 说明 |
| --- | --- |
| `gradio_app.py` | 旧版单体入口（原 `app.py`，1369 行）：Gradio 界面 + 隐藏组件 REST hack + 内存题库 |
| `src/` | 旧版脚本：data_loader / model / train / predict / evaluate_* / download_data / scraper / augment_data / utils |
| `platform.html` | 旧"智慧教研平台"单文件页（大量模拟数据，iframe 嵌 Gradio） |
| `ai_input.html` | 旧 AI 录入页（对接旧 `/api/*`） |
| `ui_preview.html` | 旧界面设计稿 |
| `tools/` | 旧 CDP 散装自测脚本（已被 pytest 取代） |
| `user_models/` | 原始候选模型路径与选型记录 |
| `platform.html.newcode.tmp` | 改造过程遗留临时文件 |

## 运行旧版（如需回退对照）

```powershell
cd D:\edu_hierarchical_classifier
venv\Scripts\python legacy\gradio_app.py
# http://127.0.0.1:7860  （旧版界面与 /api/* 内存版接口）
```

依赖说明：

- 旧版仍读取项目根目录 `config.yaml`（旧配置文件，含历史硬编码路径字段，新架构不使用它，新配置见 `.env.example`）；
- 旧版 `src/utils.py` 硬编码了 `PROJECT_ROOT = D:\edu_hierarchical_classifier`，换机器需手动修改；
- 新架构入口是根目录 `app.py`（FastAPI 薄入口），与旧版端口同为 7860，**不要同时启动**。

## 为什么归档而不是删除

- 旧版是"从 0 到 1"的完整参照，面试讲述改造过程时需要；
- git 历史之外保留一份可直接运行的对照片，便于对比验证新旧行为等价；
- 新架构 `scripts/check_project_guardrails.py` 会阻止旧版 Gradio 私有 API 用法回到主链路。
