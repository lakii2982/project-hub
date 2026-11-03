#!/usr/bin/env python3
"""Q-39 one-command local demo environment (1.0 micro scope).

    python scripts/demo.py --slice micro

For a brand-new database file the command prepares an isolated SQLite demo
database (additive migrations + idempotent ``seed_demo``); when the file already
exists it must already be this script's demo database (all organizations are
``is_demo=1`` and all migrations are applied) and is then reused without any
migration or write. Zero-byte files, empty/non-project SQLite files, real-company
databases and old-schema demo databases are refused before any write. The command
then starts ``manage.py runserver`` in the background, waits for ``/health/``,
prints the URL, rehearsal accounts and a 5-step walkthrough, and exits 0 while the
server keeps running. ``--stop`` stops the server started by this script after
validating its state file (repository, port and database path). Repeat runs are
idempotent (no duplicate rows, no database writes, reuse a running server). Only
127.0.0.1 is bound; demo names/amounts are fictional and the random password is
only written to a 0600 credentials file (never printed).
"""
import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SLICES = ("micro",)
DEFAULT_PORT = 8765
DEFAULT_DB = "runtime/demo.sqlite3"
DEFAULT_RUNTIME = "runtime"
STATE_NAME = "demo-server.json"
ROLE_LABELS = {"owner": "老板", "finance": "项目财务", "pm": "项目经理",
               "member": "项目成员", "auditor": "只读审阅", "newbie": "缺费率成员"}
STEPS = (
    ("登录工作台", "用老板账号登录：本月开票/已收款/已支出、资金账户余额、未收款合计与"
     "项目收入/成本/毛利一屏可见（Q-33）。", "/"),
    ("项目收益与现金", "打开 项目 → PH-26001 云桥 CRM 二期开发：合同额→开票→收款→"
     "人工/费用成本→毛利→项目现金净额（Q-34）。", "/projects/"),
    ("工时与人工成本", "打开 工时 → 按周填报（免审批），系统按成员费率快照算人工成本；"
     "工作台显示缺费率缺口（newbie 为缺费率演示账号）。", "/time/week/"),
    ("费用与付款", "打开 费用：登记一笔支出并确认（只计一次成本），再登记分次付款"
     "（只计现金），核对 8 周未付计划（Q-36）。", "/expenses/"),
    ("开票与收款", "打开 开票与收款：登记发票与部分收款，核对未收款和资金账户派生余额"
     "（Q-37）。", "/billing/"),
)


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
           "`PMO_PYTHON=.venv/bin/python python scripts/demo.py --slice micro`。")
    raise SystemExit(2)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python scripts/demo.py",
        description="启动带演练数据的小微版本机演示环境（仅监听 127.0.0.1）。")
    parser.add_argument("--slice", default="micro", choices=SLICES,
                        help="演示切片；1.0 小微版使用 micro（默认）。")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"本地端口，默认 {DEFAULT_PORT}。")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"演练 SQLite 文件（相对仓库根目录），默认 {DEFAULT_DB}；"
                             "已有文件必须已是本脚本演练库，否则拒绝且不写入。")
    parser.add_argument("--runtime-dir", default=DEFAULT_RUNTIME,
                        help=f"凭据/日志/服务状态的运行时目录，默认 {DEFAULT_RUNTIME}/。")
    parser.add_argument("--no-serve", action="store_true",
                        help="只准备数据库并打印脚本，不启动服务器（CI/测试用）。")
    parser.add_argument("--stop", action="store_true",
                        help="停止由本脚本启动的后台服务器后退出。")
    return parser.parse_args(argv)


def _resolve(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def child_env(db_path, runtime_dir):
    env = {key: value for key, value in os.environ.items() if not key.startswith("POSTGRES_")}
    env.update({"PMO_DB": str(db_path), "PMO_SCOPE": "micro", "PMO_ENV": "development",
                "DJANGO_DEBUG": "1", "PYTHONUNBUFFERED": "1",
                "PMO_DEMO_RUNTIME_DIR": str(runtime_dir)})
    return env


def run_manage(env, *args):
    return subprocess.run([sys.executable, str(ROOT / "manage.py"), *args], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=300)


def preflight_database(db_path, env):
    """Classify an existing DB; refuse anything that is not this script's demo DB."""
    if not db_path.exists():
        return "new"
    if db_path.stat().st_size == 0:
        _error(f"{db_path} 是 0 字节文件，不能确认是演练库；拒绝迁移或写入。"
               "若它确实是仅用于演练的文件，请删除后重新运行。")
        raise SystemExit(3)
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "core_organization" not in tables:
                _error(f"{db_path} 不是项目中枢演练库（空 SQLite 或非项目数据库）；"
                       "拒绝迁移或写入。若确认是仅用于演练的文件，请删除后重新运行。")
                raise SystemExit(3)
            rows = connection.execute(
                "SELECT is_demo FROM core_organization").fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        _error(f"{db_path} 不是可读的 SQLite 数据库；拒绝处理以免破坏现有数据。")
        raise SystemExit(3)
    if not rows:
        _error(f"{db_path} 是项目中枢数据库但没有任何公司，无法确认是演练库；"
               "拒绝迁移或写入。若确认是仅用于演练的文件，请删除后重新运行。")
        raise SystemExit(3)
    if any(not is_demo for is_demo, in rows):
        _error("该数据库已有真实公司数据，拒绝混入演练数据；本命令不会迁移、覆盖或清空"
               "数据库。请换用默认演练库（runtime/demo.sqlite3）或全新的 DB 文件。")
        raise SystemExit(3)
    pending = run_manage(env, "migrate", "--check")
    if pending.returncode != 0:
        details = (pending.stdout or pending.stderr).strip()[-500:]
        _error(f"{db_path} 是旧结构的演练库（存在待应用迁移），本命令不会就地升级或覆盖"
               f"已有数据库。请删除仅用于演练的该文件后重新运行。\n{details}")
        raise SystemExit(3)
    return "demo"


def setup_database(db_path, env, is_existing_demo):
    """Migrate only a brand-new file; an existing demo DB is reused without writes."""
    if not is_existing_demo:
        migrated = run_manage(env, "migrate", "--noinput")
        if migrated.returncode != 0:
            _error(f"数据库迁移失败：{(migrated.stderr or migrated.stdout).strip()[-800:]}")
            raise SystemExit(3)
    seeded = run_manage(env, "seed_demo")
    output = f"{seeded.stdout}\n{seeded.stderr}"
    if seeded.returncode != 0:
        _error(f"演练数据初始化失败：{output.strip()[-800:]}")
        raise SystemExit(4)
    return "已建立" if "演练数据已建立" in output else "已存在，复用（未重复造数、未迁移）"


def read_credentials(path):
    if not path.is_file():
        return None
    accounts, password = [], ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("演练密码："):
            password = line.split("：", 1)[1].strip()
        elif ": " in line and line.split(":", 1)[0] in ROLE_LABELS:
            role, username = line.split(": ", 1)
            accounts.append((role, username))
    return (accounts, password) if accounts and password else None


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


def _write_state(runtime_dir, state):
    path = _state_path(runtime_dir)
    path.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _clear_state(runtime_dir):
    try:
        _state_path(runtime_dir).unlink()
    except OSError:
        pass


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


def _state_matches(state, runtime_dir, port, db_path):
    return (state.get("repo") == str(ROOT) and state.get("runtime_dir") == str(runtime_dir)
            and state.get("port") == port and state.get("db") == str(db_path)
            and _pid_alive(state["pid"]) and _pid_is_our_server(state["pid"], port))


def _health_ok(port, timeout=1.0):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _port_in_use(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def _wait_health(port, attempts=80, delay=0.25):
    for _ in range(attempts):
        if _health_ok(port, timeout=0.5):
            return True
        time.sleep(delay)
    return False


def stop_server(runtime_dir, port, db_path):
    state = _read_state(runtime_dir)
    if state is None:
        print("没有由 demo.py 启动的后台服务器需要停止。")
        if _health_ok(port):
            print(f"提示：127.0.0.1:{port} 仍有服务在监听，但不由本脚本托管，未做处理。")
        return 0
    if not _state_matches(state, runtime_dir, port, db_path):
        if (state.get("repo") != str(ROOT) or state.get("runtime_dir") != str(runtime_dir)
                or state.get("port") != port or state.get("db") != str(db_path)):
            _error("服务状态文件记录的仓库/运行时目录/端口/数据库与本次 --stop 参数不一致；"
                   "拒绝停止，避免误杀其他进程。请使用启动时的参数，或手动处理。")
            return 3
        _clear_state(runtime_dir)
        print("后台服务器已不在运行，已清理陈旧状态文件。")
        return 0
    pid = state["pid"]
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not _pid_alive(pid):
            break
        time.sleep(0.25)
    if _pid_alive(pid):
        os.kill(pid, signal.SIGKILL)
    _clear_state(runtime_dir)
    print(f"已停止演练服务器（pid {pid}）。数据库与演练账号保留，可再次运行 demo.py 复用。")
    return 0


def ensure_server(port, db_path, runtime_dir, env):
    """Return 'reused'/'started'; raise SystemExit(5) on any mismatch/failure."""
    state = _read_state(runtime_dir)
    health = _health_ok(port)
    if state is not None:
        if not _state_matches(state, runtime_dir, port, db_path):
            if (state.get("repo") != str(ROOT) or state.get("runtime_dir") != str(runtime_dir)
                    or state.get("port") != port or state.get("db") != str(db_path)):
                _error("服务状态文件记录的仓库/运行时目录/端口/数据库与本次参数不一致；"
                       "拒绝复用或停止该服务。请使用匹配参数，或先按原参数执行 --stop。")
                raise SystemExit(5)
            _clear_state(runtime_dir)
        elif health:
            print(f"演练服务器已在运行，复用：http://127.0.0.1:{port}/")
            return "reused"
        elif _wait_health(port, attempts=20):
            return "reused"
        else:
            os.kill(state["pid"], signal.SIGTERM)
            time.sleep(0.5)
            _clear_state(runtime_dir)
    elif health:
        _error(f"端口 {port} 的 /health/ 不是由本脚本启动的演练服务（缺少可校验的服务状态）；"
               "拒绝复用。请用 --port 换端口，或先停止该服务。")
        raise SystemExit(5)
    if _port_in_use(port):
        _error(f"端口 {port} 已被其他程序占用；请用 --port 指定其它端口，或先停止占用进程。")
        raise SystemExit(5)
    log_path = runtime_dir / "demo-server.log"
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "manage.py"), "runserver",
             f"127.0.0.1:{port}", "--noreload"], cwd=ROOT, env=env, stdout=log_file,
            stderr=subprocess.STDOUT, start_new_session=True)
    _write_state(runtime_dir, {"pid": process.pid, "repo": str(ROOT),
                               "runtime_dir": str(runtime_dir), "port": port,
                               "db": str(db_path),
                               "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    if _wait_health(port):
        return "started"
    try:
        process.terminate()
    except OSError:
        pass
    _clear_state(runtime_dir)
    tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]) \
        if log_path.is_file() else ""
    _error(f"演练服务器启动失败（端口 {port}）。日志：{log_path}\n{tail}")
    raise SystemExit(5)


def print_summary(url, db_path, credentials_path, seed_state, accounts,
                  runtime_dir, no_serve=False, server_state="started"):
    base = url.rstrip("/")
    status = ("（未启动：--no-serve；去掉该参数即可启动）" if no_serve else
              "（复用已在运行的后台服务器）" if server_state == "reused" else
              "（已在后台启动；本命令已返回）")
    print("\n项目中枢 · 小微版演练环境\n" + "=" * 56)
    print(f"地址    ：{url}{status}")
    print(f"数据库  ：{db_path}（仅新建或已确认的演练库；已有库不迁移、不覆盖）")
    print(f"凭据    ：{credentials_path}（0600，不进 Git）")
    print(f"演练数据：{seed_state}")
    print(f"服务器  ：日志 {runtime_dir / 'demo-server.log'}，"
          f"状态 {runtime_dir / STATE_NAME}")
    print("\n演练账号（角色 / 登录名；随机密码见凭据文件，不打印到控制台）：")
    for role, username in accounts:
        print(f"  - {ROLE_LABELS.get(role, role):<5s} / {username}")
    print(f"演练密码：见 {credentials_path}（0600，随机生成，不打印到控制台/日志）")
    print("\n5 步演示（在浏览器打开打印的地址后按顺序操作）：")
    for index, (title, description, path) in enumerate(STEPS, 1):
        print(f"  {index}. {title}：{description}")
        print(f"     入口：{base}{path}")
    print(f"\n停止服务器：python scripts/demo.py --stop --db {db_path} --port {url.rsplit(':', 1)[-1].rstrip('/')}")
    print("说明：只监听 127.0.0.1，不开放到局域网；重复运行幂等，不会重复造数或写入已有数据库；"
          "演示数据均为虚构，不含真实客户、工资或凭据。")
    print("提示：删除 runtime/demo.sqlite3 可重建演练库；演练账号密码见凭据文件。\n" + "=" * 56)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(argv)
    ensure_django_interpreter(argv)
    db_path = _resolve(args.db)
    runtime_dir = _resolve(args.runtime_dir)
    if not args.stop and os.environ.get("PMO_ENV") == "production":
        _error("当前环境是 production；一条命令演示只允许开发/演练数据库，拒绝启动。")
        return 3
    runtime_dir.mkdir(parents=True, exist_ok=True)
    if args.stop:
        return stop_server(runtime_dir, args.port, db_path)
    if not 1 <= args.port <= 65535:
        _error("端口须在 1–65535 之间。")
        return 2
    db_path.parent.mkdir(parents=True, exist_ok=True)
    env = child_env(db_path, runtime_dir)
    state_kind = preflight_database(db_path, env)
    seed_state = setup_database(db_path, env, is_existing_demo=state_kind == "demo")
    credentials_path = runtime_dir / "demo-credentials.txt"
    credentials = read_credentials(credentials_path)
    if credentials is None:
        _error(f"找不到演练凭据 {credentials_path}。该文件由 seed_demo 首次建立；"
               "若演练账密已丢失，请删除仅用于演练的数据库后重新运行。")
        return 4
    accounts, _password = credentials
    url = f"http://127.0.0.1:{args.port}/"
    if args.no_serve:
        print_summary(url, db_path, credentials_path, seed_state, accounts,
                      runtime_dir, no_serve=True)
        return 0
    server_state = ensure_server(args.port, db_path, runtime_dir, env)
    print_summary(url, db_path, credentials_path, seed_state, accounts,
                  runtime_dir, server_state=server_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
