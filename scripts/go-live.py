#!/usr/bin/env python3
"""Q-52 演练库 → 正式库一键切换（本机 SQLite 正式库）。

    python scripts/go-live.py --check          # 只读检查：不创建/迁移/写入任何库
    python scripts/go-live.py                  # 切换到默认正式库 runtime/pmo.sqlite3
    python scripts/go-live.py --db PATH        # 指定目标正式库文件
    python scripts/go-live.py --stop-demo      # 只停止由 scripts/demo.py 启动的演练服务

安全约定：演练服务仍在运行时拒绝切换（先执行 ``python scripts/demo.py --stop``）；
目标库已有公司（演练库同样算已有公司）或不是本工具的空库时拒绝且不写任何数据；
只有空/不存在的目标库才会执行 ``migrate --noinput``，随后校验没有 is_demo 组织并打印
``/setup/`` 入口与下一步（生产部署用 ``scripts/install.sh``）。命令绝不删除/覆盖演练库、
不写入演练数据、不启动长期服务、输出不含密码或密钥。重复执行幂等：已迁移的空库再次
执行不会重复应用迁移、不会写入业务数据。

exit: 0 成功（--check 含“可以安全切换”）；2 演练服务运行中或环境前置拒绝；
      3 目标库不安全拒绝；4 迁移/校验失败。
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = "runtime/pmo.sqlite3"
DEFAULT_DEMO_DB = "runtime/demo.sqlite3"
DEFAULT_RUNTIME = "runtime"
STATE_NAME = "demo-server.json"
SETUP_PORT = 8000
# 迁移本身会写入的框架元数据表：它们有行不代表有业务事实。
METADATA_TABLES = frozenset({
    "django_migrations", "django_content_type", "django_session", "django_admin_log",
    "auth_permission", "auth_group", "auth_group_permissions", "sqlite_sequence",
})
EXIT_OK = 0
EXIT_PRECONDITION = 2
EXIT_UNSAFE_TARGET = 3
EXIT_FAILED = 4


def _error(message):
    print(f"错误：{message}", file=sys.stderr)


def _can_import_django(python):
    try:
        return subprocess.run([python, "-c", "import django"], cwd=ROOT,
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_django_interpreter(argv):
    """Re-exec with .venv/PMO_PYTHON when the caller cannot import Django."""
    if _can_import_django(sys.executable):
        return
    current = Path(sys.executable).resolve()
    for candidate in (os.environ.get("PMO_PYTHON"), str(ROOT / ".venv" / "bin" / "python"),
                      str(ROOT / ".venv" / "Scripts" / "python.exe")):
        path = Path(candidate) if candidate else None
        if path and path.exists() and os.access(path, os.X_OK) \
                and path.resolve() != current and _can_import_django(str(path)):
            os.execv(str(path), [str(path), str(Path(__file__).resolve()), *argv])
    _error("当前 Python 无法导入 Django。请先运行 `sh scripts/local.sh`，或用 "
           "`PMO_PYTHON=.venv/bin/python python scripts/go-live.py`。")
    raise SystemExit(EXIT_PRECONDITION)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python scripts/go-live.py",
        description="停止演练环境后把本机正式库（SQLite）迁移到最新结构并打印 /setup/ 入口。")
    parser.add_argument("--check", action="store_true",
                        help="只读检查演练服务/目标库状态与下一步，不创建、不迁移、不写库。")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"目标正式库 SQLite 文件（相对仓库根目录），默认 {DEFAULT_DB}；"
                             "已有公司或非本工具结构的文件会被拒绝且不写入。")
    parser.add_argument("--stop-demo", action="store_true",
                        help="停止由 scripts/demo.py 启动的演练服务后退出；不执行切换。")
    parser.add_argument("--demo-db", default=DEFAULT_DEMO_DB,
                        help=f"演练库文件（仅用于识别目标库是否指向演练库），默认 {DEFAULT_DEMO_DB}。")
    parser.add_argument("--runtime-dir", default=DEFAULT_RUNTIME,
                        help=f"演练服务状态/凭据所在运行时目录，默认 {DEFAULT_RUNTIME}/。")
    return parser.parse_args(argv)


def _resolve(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _same_path(left, right):
    return Path(left).resolve() == Path(right).resolve()


def child_env(db_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("POSTGRES_")}
    env.update({"PMO_DB": str(db_path), "PMO_SCOPE": "micro", "PMO_ENV": "development",
                "DJANGO_DEBUG": "1", "PYTHONUNBUFFERED": "1"})
    return env


def run_manage(env, *args):
    try:
        return subprocess.run([sys.executable, str(ROOT / "manage.py"), *args], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as error:
        _error(f"无法运行 manage.py {args[0]}：{error}")
        return None


# --------------------------------------------------------------------------- 状态

def _state_path(runtime_dir):
    return runtime_dir / STATE_NAME


def _read_state(runtime_dir):
    try:
        state = json.loads(_state_path(runtime_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or type(state.get("pid")) is not int:
        return None
    return state


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_is_our_server(pid, port):
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    command = result.stdout.strip()
    return (result.returncode == 0 and str(ROOT / "manage.py") in command
            and f"runserver 127.0.0.1:{port}" in command)


def demo_service_status(runtime_dir):
    """Return {'running', 'state', 'foreign'} for the demo.py-managed server."""
    state = _read_state(runtime_dir)
    if state is None:
        return {"running": False, "state": None, "foreign": False}
    foreign = (state.get("repo") != str(ROOT)
               or state.get("runtime_dir") != str(runtime_dir))
    if foreign:
        return {"running": False, "state": state, "foreign": True}
    port = state.get("port")
    running = (type(port) is int and _pid_alive(state["pid"])
               and _pid_is_our_server(state["pid"], port))
    return {"running": running, "state": state, "foreign": False}


def describe_service(service, runtime_dir):
    state = service.get("state")
    state_path = _state_path(runtime_dir)
    if service["running"]:
        return (f"运行中（pid {state['pid']}，http://127.0.0.1:{state['port']}/；"
                f"状态文件 {state_path}）")
    if service.get("foreign"):
        return f"未确认（{state_path} 记录的是其它仓库/运行时目录，未视为本仓库演练服务）"
    if state:
        return "未运行（状态文件存在但进程已不在；可用 python scripts/demo.py --stop 清理）"
    return "未运行（未找到由 scripts/demo.py 启动的后台服务记录）"


# --------------------------------------------------------------------------- 目标库

def classify_database(path):
    """Read-only classification of the would-be formal database."""
    if not path.exists():
        return {"kind": "missing"}
    try:
        if path.stat().st_size == 0:
            return {"kind": "empty_file"}
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except (OSError, sqlite3.Error) as error:
        return {"kind": "unreadable", "detail": str(error)}
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "core_organization" not in tables:
            return {"kind": "not_project" if tables else "empty_sqlite",
                    "tables": sorted(tables)[:8]}
        total, demo = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN is_demo=1 THEN 1 ELSE 0 END), 0) "
            "FROM core_organization").fetchone()
        if total:
            return {"kind": "demo_orgs" if demo == total else "real_orgs",
                    "orgs": total, "demo_orgs": demo}
        # 没有公司记录时仍不能把任何业务行当“空库”：扫描全部用户表（排除迁移元数据
        # 和 SQLite 内部表），任一表有行都拒绝。旧实现只抽样 4 张表，会漏掉员工/账本/
        # 审计/单据/工时/插件表，从而把有历史数据的库误判为空并迁移覆盖。
        business = {}
        for table in sorted(tables):
            if table in METADATA_TABLES or table.startswith("sqlite_"):
                continue
            try:
                count = connection.execute(
                    'SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0]
            except sqlite3.Error:
                return {"kind": "unreadable", "detail": f"无法读取表 {table}"}
            if count:
                business[table] = count
        if business:
            return {"kind": "data_without_org", "business": business}
        migrations = None
        if "django_migrations" in tables:
            migrations = connection.execute(
                "SELECT COUNT(*) FROM django_migrations").fetchone()[0]
        return {"kind": "empty_initialized", "migrations": migrations}
    except sqlite3.OperationalError:
        # 有 core_organization 但列结构不兼容（旧结构/其它工具）时同样按不可识别处理。
        return {"kind": "not_project"}
    except sqlite3.DatabaseError:
        return {"kind": "unreadable"}
    finally:
        connection.close()


def describe_target(info, path):
    kind = info["kind"]
    if kind == "missing":
        return "不存在（切换时将新建空库并迁移）"
    if kind == "empty_file":
        return "空文件（0 字节；切换时将迁移）"
    if kind == "empty_sqlite":
        return "空 SQLite（无表；切换时将迁移）"
    if kind == "empty_initialized":
        return (f"已初始化的空库（已应用迁移 {info.get('migrations')} 条、无公司；"
                "重复切换幂等）")
    if kind == "demo_orgs":
        return f"演练库（{info['orgs']} 个公司全部 is_demo=1）；不能作为正式库"
    if kind == "real_orgs":
        return f"已有公司（{info['orgs']} 个，含非演练数据）；拒绝覆盖/混入"
    if kind == "data_without_org":
        return "有业务数据但没有公司记录（结构异常）；拒绝"
    if kind == "unreadable":
        return "无法读取的数据库文件；拒绝"
    return f"非项目中枢数据库（{path}）；拒绝"


def refusal_reason(info, path):
    kind = info["kind"]
    if kind in ("missing", "empty_file", "empty_sqlite", "empty_initialized"):
        return None
    if kind == "demo_orgs":
        return (f"目标库 {path} 里已有公司且全部是演练数据（is_demo=1），这是演练库，"
                "不能作为正式库；拒绝迁移，未写入任何数据。")
    if kind == "real_orgs":
        return (f"目标库 {path} 已有公司（含非演练数据）；为避免覆盖或混入真实账，"
                "拒绝迁移，未写入任何数据。正式库请指向空/不存在的文件"
                f"（默认 {DEFAULT_DB}）。")
    if kind == "data_without_org":
        return (f"目标库 {path} 已有业务数据但没有公司记录（结构异常）；"
                "拒绝迁移，未写入任何数据。")
    if kind == "not_project":
        return (f"目标库 {path} 不是本工具的空库（已有其他表或缺少项目中枢结构）；"
                "拒绝迁移，未写入任何数据。")
    return (f"目标库 {path} 不是可读的 SQLite 数据库；拒绝迁移，未写入任何数据。")


def demo_org_count(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM core_organization WHERE is_demo=1").fetchone()[0]
    finally:
        connection.close()


# --------------------------------------------------------------------------- 各模式

def stop_demo(runtime_dir):
    state = _read_state(runtime_dir)
    if state is None:
        print("没有检测到由 scripts/demo.py 启动的演练服务（状态文件不存在或不可解析）；"
              "无需停止。")
        return EXIT_OK
    if (state.get("repo") != str(ROOT)
            or state.get("runtime_dir") != str(runtime_dir)):
        _error("演练服务状态文件记录的仓库/运行时目录与本次不一致；拒绝停止，避免误杀其他"
               "进程。请使用启动时的参数执行 python scripts/demo.py --stop。")
        return EXIT_UNSAFE_TARGET
    try:
        stopped = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "demo.py"), "--stop",
             "--db", str(state.get("db", "")), "--port", str(state.get("port", "")),
             "--runtime-dir", str(runtime_dir)],
            cwd=ROOT, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        _error(f"无法调用 scripts/demo.py --stop：{error}")
        return EXIT_FAILED
    if stopped.stdout:
        print(stopped.stdout.strip())
    if stopped.returncode != 0:
        _error(f"停止演练服务失败（python scripts/demo.py --stop exit "
               f"{stopped.returncode}）：{(stopped.stderr or stopped.stdout).strip()[-500:]}")
        return EXIT_FAILED
    print("下一步：重新运行 python scripts/go-live.py 完成演练库→正式库切换。")
    return EXIT_OK


def run_check(db_path, demo_db, runtime_dir, service):
    print("项目中枢 · 演练库 → 正式库切换检查（--check 只读：不创建、不迁移、不写库）")
    print("=" * 56)
    print(f"演练服务：{describe_service(service, runtime_dir)}")
    print(f"演练库  ：{demo_db}（{'存在；未改动' if demo_db.exists() else '不存在'}；"
          "本命令不会删除或覆盖它）")
    info = classify_database(db_path)
    print(f"目标正式库：{db_path}")
    print(f"目标库状态：{describe_target(info, db_path)}")
    print("-" * 56)
    if service["running"]:
        print("结论：演练服务仍在运行，不能切换。请先停止演练服务：")
        print("  python scripts/demo.py --stop")
        print("然后重新运行 python scripts/go-live.py --check。")
        return EXIT_PRECONDITION
    if _same_path(db_path, demo_db):
        print(f"结论：目标库指向演练库（{db_path}），不能作为正式库；拒绝切换（未写入）。")
        return EXIT_UNSAFE_TARGET
    reason = refusal_reason(info, db_path)
    if reason:
        print(f"结论：目标库不能作为正式库，拒绝切换（未写入任何数据）。原因：{reason}")
        return EXIT_UNSAFE_TARGET
    print("结论：可以安全切换（演练服务未运行，目标库为空/不存在或已初始化为空库）。")
    print(f"下一步：python scripts/go-live.py --db {db_path}")
    return EXIT_OK


def run_switch(db_path, demo_db, runtime_dir, service, info):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    env = child_env(db_path)
    migrated = run_manage(env, "migrate", "--noinput")
    if migrated is None:
        return EXIT_FAILED
    if migrated.returncode != 0:
        detail = (migrated.stderr or migrated.stdout).strip().splitlines()[-6:]
        _error("目标库迁移失败，未继续：" + " / ".join(detail)[-800:])
        return EXIT_FAILED
    applied_now = "Applying " in migrated.stdout
    verified = run_manage(env, "migrate", "--check")
    if verified is None:
        return EXIT_FAILED
    if verified.returncode != 0:
        _error("迁移后校验失败：目标库仍有待应用迁移或命令未成功；请检查目标库结构。")
        return EXIT_FAILED
    after = classify_database(db_path)
    if after["kind"] != "empty_initialized":
        _error(f"迁移后目标库状态异常（{describe_target(after, db_path)}）；"
               "未继续，请检查该文件。")
        return EXIT_FAILED
    demo_orgs = demo_org_count(db_path)

    print()
    print("项目中枢 · 演练库 → 正式库切换")
    print("=" * 56)
    print(f"演练服务：{describe_service(service, runtime_dir)}")
    print(f"演练库  ：{demo_db}（存在；未删除、未覆盖、未写入）"
          if demo_db.exists() else f"演练库  ：{demo_db}（不存在）")
    print(f"目标正式库：{db_path}")
    print("数据库迁移："
          + ("已应用迁移并补齐到最新结构" if applied_now
             else "已是最新结构（重复执行未重复迁移）"))
    print(f"演练数据校验：正式库中 is_demo=1 的公司 {demo_orgs} 个（未写入演练数据）")
    print("-" * 56)
    print("下一步：")
    print("  1. 本机首次启用（空库）：先启动本机服务，再在浏览器打开首次启用向导")
    print("       .venv/bin/python manage.py runserver 127.0.0.1:8000")
    print(f"       http://127.0.0.1:{SETUP_PORT}/setup/")
    print("  2. 生产部署：scripts/install.sh --domain <域名>")
    print("     （Docker Compose + PostgreSQL；首次生成 0600 .env 与随机密钥，"
          "重复执行幂等，不覆盖已有 .env/数据卷）")
    print("     安装完成后浏览器打开 https://<域名>/setup/ 首次启用。")
    print("  3. 再次检查：python scripts/go-live.py --check")
    print("说明：演练库不会被删除或覆盖；正式库未写入演练数据；本命令不启动长期服务，"
          "输出不含密码/密钥。")
    print("=" * 56)
    return EXIT_OK


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(argv)
    ensure_django_interpreter(argv)
    db_path = _resolve(args.db)
    demo_db = _resolve(args.demo_db)
    runtime_dir = _resolve(args.runtime_dir)

    if args.stop_demo:
        return stop_demo(runtime_dir)
    if os.environ.get("PMO_ENV") == "production" or os.environ.get("POSTGRES_HOST"):
        _error("当前是 production/PostgreSQL 环境；本命令只用于本机 SQLite 正式库初始化。"
               "生产部署请运行 scripts/install.sh --domain <域名>（Docker Compose + "
               "PostgreSQL），安装后打开 https://<域名>/setup/。本命令未写入任何数据库。")
        return EXIT_PRECONDITION

    service = demo_service_status(runtime_dir)
    if args.check:
        return run_check(db_path, demo_db, runtime_dir, service)
    if service["running"]:
        _error(f"演练服务仍在运行（pid {service['state']['pid']}，"
               f"http://127.0.0.1:{service['state']['port']}/）。切换正式库前请先停止演练服务：\n"
               "  python scripts/demo.py --stop\n"
               "本命令未写入任何数据库，也未删除演练库。")
        return EXIT_PRECONDITION
    if _same_path(db_path, demo_db):
        _error(f"目标库指向演练库（{db_path}），不能作为正式库；拒绝切换，未写入任何文件。"
               f"请用 --db 指定正式库路径（默认 {DEFAULT_DB}）。")
        return EXIT_UNSAFE_TARGET
    info = classify_database(db_path)
    reason = refusal_reason(info, db_path)
    if reason:
        _error(reason)
        return EXIT_UNSAFE_TARGET
    return run_switch(db_path, demo_db, runtime_dir, service, info)


if __name__ == "__main__":
    raise SystemExit(main())
