# 本地测试环境

更新日期：2026-10-27。用于macOS/Python3.12+和SQLite演练，地址[http://127.0.0.1:8765/](http://127.0.0.1:8765/)，登录路径`/login/`。当前发布版本0.1.0，开发主线已包含PH-022至025；全站UI/基础资料独立模块仍待PH-026。

## 首次使用

在项目目录执行：

```sh
sh scripts/local.sh
.venv/bin/python manage.py seed_demo
sh scripts/run-local.sh
```

若系统python3低于3.12，用PMO_PYTHON指定已安装的3.12+解释器后运行local.sh。seed_demo仅用于开发空库：已存在演练公司时保留数据与密码，已有真实公司时拒绝混入演练数据。不要删除数据库来重置账号。

五个角色用户名为owner、finance、pm、member、auditor。随机演练密码只保存在本机`runtime/demo-credentials.txt`（0600），不进入Git、截图或发布包。登录后可使用经营总览、项目空间、项目详情六页签、工时、审批和财务等入口；不同角色按既有权限显示。图中净现金流不是利润，预算余额不是银行余额。

## 已有环境更新

先确认`git status --short`为空；有修改时先保留并核对归属，不自动stash/reset。干净工作区同步main：

```sh
git fetch --prune origin
git switch main
git merge --ff-only origin/main
```

停止旧的run-local进程。在项目目录创建备份父目录，再选一个尚不存在的备份名：

```sh
mkdir -p runtime/backups
.venv/bin/python manage.py backup_local --output runtime/backups/before-update-YYYYMMDD-HHMMSS
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py verify_integrity --json
sh scripts/run-local.sh
```

将备份名中的日期时间替换为本次值；备份不覆盖已有目录。数据库是`runtime/pmo.sqlite3`，附件在`runtime/uploads/`。浏览器重新打开登录页并真实登录，确认总览、项目详情页签与工时表单；仅看到监听端口不足以证明可用。run-local使用`--noreload`，更新代码后必须重启。

## 回归与停止

```sh
TMPDIR=/private/tmp .venv/bin/python manage.py test tests --verbosity 1
.venv/bin/python manage.py verify_integrity --json
```

TMPDIR写法用于macOS测试夹具的规范路径，避免/var与/private/var别名影响既有路径断言。环境限制跳过、Popen对象回收告警与实际通过数量分别记录在verification.md及任务报告，不视为生产并发证明。

在启动服务的终端按Ctrl+C停止。仅监听127.0.0.1，不开放到局域网；这是开发测试服务，生产部署见operations.md。runtime、备份、凭据、附件、.env和虚拟环境均不得提交。
