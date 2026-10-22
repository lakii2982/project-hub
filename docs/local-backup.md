# 本地 SQLite 一致性备份

2026-10-22验收：47条账目与1个演练附件隔离恢复通过；空库/空附件包通过；
覆盖拒绝、权限0700/0600与附件包篡改检出通过。活动库未被覆盖。

`backup_local` 用于本地或开发环境的 SQLite 一致性备份与隔离恢复验证。
生产 PostgreSQL 仍走 `scripts/backup.sh`，该生产路径不在本命令的验证范围内。

## 创建备份

输出目录必须尚不存在：

```sh
.venv/bin/python manage.py backup_local --output backups/2026-10-22-local
```

命令仅允许非 production 的 SQLite，使用 SQLite `backup()` API 从活动连接生成
`database.sqlite3`，不复制正在写入的源数据库文件。随后从这份数据库快照读取
`core_evidence`，只归档被引用的附件，并逐个核对登记 SHA256。

备份目录和文件分别设为 `0700`、`0600`。目录内包含：

- `database.sqlite3`：SQLite 快照。
- `uploads.tar.gz`：按 `MEDIA_ROOT` 相对路径保存的 Evidence 附件；无 Evidence 时为空 tar。
- `manifest.json`：格式版本、两个 artifact 的文件名和 SHA256、Evidence 路径与 SHA256。

`runtime/demo-credentials.txt` 等其他文件不会被打包，清单也不记录主机绝对路径。

## 隔离验证

```sh
.venv/bin/python scripts/verify-local-backup.py backups/2026-10-22-local
```

验证过程只在 `tempfile.TemporaryDirectory()` 中复制数据库和解包附件。tar 使用
`filter="data"`，解包前拒绝绝对路径、`..`、符号链接及非普通文件。脚本先核对
manifest、两个 artifact 和数据库中的 Evidence 记录，再让当前项目的
`.venv/bin/python manage.py verify_integrity --json` 读取临时数据库副本。

验证时固定 `PMO_ENV=development`、`PMO_DB` 指向临时副本，并清除全部
`POSTGRES_*` 环境变量，不连接真实 PostgreSQL。成功只输出 `ok`、账目数和附件数，
退出码为 0；任一步失败输出 `ok: false`，退出码为 1。
