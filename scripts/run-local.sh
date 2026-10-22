#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then printf '%s\n' '请先运行 sh scripts/local.sh 安装依赖'; exit 1; fi
exec .venv/bin/python manage.py runserver 127.0.0.1:8765 --noreload
