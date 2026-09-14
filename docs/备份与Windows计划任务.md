# MySQL 备份与 Windows 计划任务

备份脚本为 `scripts/backup_db.py`，只连接 `.env` 中配置的 MySQL 业务库，导出 JSON 快照；恢复操作必须人工执行 `scripts/restore_db.py`，不属于自动任务。

## 手动验证

```powershell
venv\Scripts\python.exe scripts\backup_db.py
```

快照存放在 `backups/`，结果状态写入 `reports/verification/backup_status_latest.json`。默认保留策略为 7 份，但脚本**默认不会删除任何旧快照**；若管理员已核对可清理目标，才可显式执行：

```powershell
venv\Scripts\python.exe scripts\backup_db.py --keep 7 --prune
```

## Windows 计划任务建议

在“任务计划程序”中新建任务：

- 触发器：每天一次，选择低峰时段；
- 程序：`D:\edu_hierarchical_classifier\venv\Scripts\python.exe`；
- 参数：`scripts\backup_db.py`；
- 起始于：`D:\edu_hierarchical_classifier`；
- 使用“仅当用户登录时运行”进行首次观察，确认权限和输出后再按运维要求调整。

任务失败时先查看 `reports/verification/backup_status_latest.json` 和应用日志；不要在未确认快照文件与目标库的情况下执行恢复。
