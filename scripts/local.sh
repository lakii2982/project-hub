#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
PMO_PYTHON="${PMO_PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
    "$PMO_PYTHON" -c 'import sys; assert sys.version_info >= (3,12), "请使用Python 3.12及以上，或通过PMO_PYTHON指定路径"'
    "$PMO_PYTHON" -m venv .venv
fi
if command -v uv >/dev/null 2>&1; then
    UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.cache/uv}" uv pip install --python .venv/bin/python -r requirements.txt
else
    .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python manage.py migrate --noinput
printf '%s\n' '一条命令演练环境：python scripts/demo.py --slice micro（打印地址/账号/5 步脚本，停止：python scripts/demo.py --stop）' '真实公司初始化：.venv/bin/python manage.py bootstrap --company 你的公司 --username owner' '真实公司启动：sh scripts/run-local.sh'
