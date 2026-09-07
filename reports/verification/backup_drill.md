# A3 MySQL 备份/恢复演练报告

- 演练时间：2026-09-07 20:23:47
- 快照文件：`snapshot_20260907_2023.json`（体积 46.18 KB）
- 演练内容：备份 → 向 questions 插入标记行 → restore → 断言标记行消失且 15 表行数与快照一致

## 耗时

| 阶段 | 耗时（s） | 口径 |
| --- | --- | --- |
| 备份 | 0.09 | 脚本内实测（15 表全量导出 + 落盘） |
| 恢复 | 1.04 | restore_db.py 内部 DROP/建表/回灌实测 |
| 恢复（含进程启动） | 1.78 | 演练整体耗时 |

## 数据行数比对

| 表 | 快照行数 | 恢复后行数 | 一致 |
| --- | --- | --- | --- |
| active_model_pointer | 1 | 1 | ✅ |
| answer_records | 0 | 0 | ✅ |
| assignments | 0 | 0 | ✅ |
| audit_logs | 5 | 5 | ✅ |
| classes | 2 | 2 | ✅ |
| classifications | 19 | 19 | ✅ |
| daily_stats | 3 | 3 | ✅ |
| feedback | 24 | 24 | ✅ |
| knowledge_nodes | 50 | 50 | ✅ |
| model_versions | 3 | 3 | ✅ |
| paper_questions | 6 | 6 | ✅ |
| papers | 3 | 3 | ✅ |
| questions | 9 | 9 | ✅ |
| submissions | 0 | 0 | ✅ |
| users | 2 | 2 | ✅ |

## 结论

- 恢复后标记行消失，15 张业务表行数与快照完全一致，演练通过。
- 恢复方式为按迁移（V1-V3）重建表结构后逐表回灌 JSON 快照，不依赖宿主机 mysqldump。