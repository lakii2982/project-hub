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

验证脚本优先使用 `PMO_PYTHON`，其次项目 `.venv`，最后当前解释器；不依赖固定 `.venv` 路径。验证时固定 `PMO_ENV=development`、`PMO_DB` 指向临时副本，并清除全部
`POSTGRES_*` 环境变量，不连接真实 PostgreSQL。成功只输出 `ok`、账目数和附件数，
退出码为 0；任一步失败输出 `ok: false`，退出码为 1。
## 隔离恢复（Q-43）

```sh
.venv/bin/python manage.py restore_local --backup runtime/backups/2026-10-22-local --target runtime/restored-2026-10-22
```

目标目录必须尚不存在；命令校验 manifest、数据库/附件 SHA256 与 SQLite 完整性后，只复制数据库并安全解包附件，失败会删除本次创建的目标目录，绝不覆盖活动库或已有恢复目录。成功后按输出提示以隔离环境启动只读核对：

```sh
PMO_DB=runtime/restored-2026-10-22/database.sqlite3 \
PMO_MEDIA_ROOT=runtime/restored-2026-10-22/uploads \
.venv/bin/python manage.py verify_integrity --json
PMO_DB=runtime/restored-2026-10-22/database.sqlite3 \
PMO_MEDIA_ROOT=runtime/restored-2026-10-22/uploads \
.venv/bin/python manage.py runserver 127.0.0.1:8770 --noreload
```

恢复后登录/工作台/项目收益与现金/开票与收款/现金页面数字应与备份前一致（Q-43 实测见 [验证记录](verification.md)）。`/status/` 只读显示最近备份的存在性与大小，不替代 `verify-local-backup.py` 或 `restore_local` 的哈希校验。

## 2026-11-04 Q-43 实测

干净 SQLite 本地安装（`sh scripts/local.sh`，uv 缓存）exit 0，重试同命令 exit 0 且 `runtime/pmo.sqlite3` SHA-256 不变；`backup_local` 备份 52 条账目 + 1 个附件，`verify-local-backup.py` 返回 `{"ok": true, "entries": 52, "attachments": 1}`；`restore_local` 恢复到全新目录后数据库 SHA-256 与备份一致、`verify_integrity --json` 为 `ok=true/errors=[]`、52 条账目。浏览器源库与恢复库的工作台/项目/开票/现金页面文本一致，owner 控制台 0 错误；`/status/` 显示版本、sqlite 连接、0 待应用迁移、最近备份清单存在、附件 1 缺失 0，不含密钥或主机路径。该次为 SQLite 本地演练；Docker Compose/PostgreSQL 真实安装因本机无 Docker/PG 未执行，安装脚本的生成 `.env`/重试/失败诊断由 7 项 fake-docker 回归覆盖，真实 PG 恢复目标仍为 `not_run`。
