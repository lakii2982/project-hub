"""一个入口查看任务、调度本地客户端及取消、恢复运行。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


def _common(parser, *, child=False):
    default = argparse.SUPPRESS if child else None
    parser.add_argument("--repository", type=Path, default=default, help="本地仓库目录")
    parser.add_argument("--config", type=Path, default=default, help="本机仓库及客户端路径配置")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS if child else False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    _common(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "tasks", "run", "status", "cancel", "recover", "assemble"):
        command = commands.add_parser(name)
        _common(command, child=True)
        if name == "run":
            command.add_argument("task_id")
            command.add_argument("--agent", required=True,
                                 choices=("kimi_code", "opencode", "dsh", "claude_code"))
            command.add_argument("--scope", action="append")
            command.add_argument("--run-id")
            command.add_argument("--timeout", type=int, default=900)
            command.add_argument("--mode", choices=("implement", "review"), default="implement")
            command.add_argument("--execute", action="store_true")
        elif name == "status":
            command.add_argument("run_id", nargs="?")
        elif name == "cancel":
            command.add_argument("run_id")
        elif name == "assemble":
            command.add_argument("run_ids", nargs="+")
    args = parser.parse_args(argv)
    try:
        from .controller.config import load_config
        repositories, configs = load_config(args.config)
        if args.command == "doctor":
            from .doctor import doctor
            result = doctor({key: value.executable for key, value in (configs or {}).items()})
            result["agents"] = [item for item in result["agents"]
                                if item["id"] in {"kimi_code", "opencode", "dsh", "claude_code"}]
        else:
            from .controller.service import Controller
            controller = Controller(args.repository or Path.cwd(), repositories, configs=configs)
            if args.command == "run":
                result = controller.run(args.task_id, args.agent, scope=args.scope,
                                        execute=args.execute, run_id=args.run_id,
                                        timeout=args.timeout, mode=args.mode)
            elif args.command == "tasks":
                result = controller.tasks()
            elif args.command == "status":
                result = controller.status(args.run_id)
            elif args.command == "cancel":
                result = controller.cancel(args.run_id)
            elif args.command == "recover":
                result = controller.recover()
            else:
                result = controller.assemble(args.run_ids)
        print(json.dumps(result, ensure_ascii=False, default=str))
        if args.command == "run" and args.execute and isinstance(result, dict):
            if result.get("state") in {"failed", "cancelled", "timed_out", "quarantined", "interrupted", "config_required"}:
                return 1
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        text = str(exc)
        code = text if re.fullmatch(r"[a-z][a-z0-9_]{0,120}", text) else "controller_refused"
        print(json.dumps({"error": code}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
