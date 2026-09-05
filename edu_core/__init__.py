"""edu_core — 教育题目层级分类系统核心包。

分层设计（对齐 knowforge-rag-platform 的 qa_core 模式）：

- config/         全局配置（pydantic-settings）、启动 preflight、日志
- inference/      模型定义与推理（BERT 共享主干 + 学科/题型/知识点三头）
- data/           数据加载、标签映射、数据集划分
- training/       多任务微调训练
- application/    应用编排层：分类服务（分类→置信度分级→留痕→统计）
- storage/        MySQL 存储层：runtime_schema.sql + bootstrap + 各 Store
- governance/     模型版本治理：注册/激活/回滚（STAGED/ACTIVE/ARCHIVED）
- quality/        评测（golden set 回归）与质量门禁
- dedup/          Milvus 题目语义查重（增强组件，可降级）
- observability/  阶段耗时与结构化日志
- api/            FastAPI 路由层：classify/questions/stats/models/pages

分层约束：
- api 只做参数校验与响应组装，业务规则在 application；
- application 不直接写 SQL，全部通过 storage 的 Store；
- inference 不感知 HTTP 与数据库。
"""
