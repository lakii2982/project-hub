#!/bin/sh
# Q-43 一条命令 Compose 安装（可重试；绝不覆盖已有 .env 或数据卷）。
#
#   scripts/install.sh --domain pmo.example.com
#   PMO_DOMAIN=pmo.example.com scripts/install.sh
#
# 仅在本机/目标服务器执行；首次生成 .env（0600，随机密钥）后用 Docker Compose
# 构建启动，等待 /health/ 通过并打印访问地址。失败时保留 .env 与 named volumes，
# 打印可诊断的命令，修复后重试同一条命令。已有 .env 只校验、不修改。
# exit: 0 成功；2 前置/参数错误；3 安装或健康检查失败。
set -eu

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
DOMAIN="${PMO_DOMAIN:-}"

usage() {
    printf '%s\n' "用法：scripts/install.sh [--domain <域名>]" \
        "  --domain  首次生成 .env 时必填；已有 .env 时以其 PMO_DOMAIN 为准" \
        "  exit 0 成功；2 前置/参数错误；3 安装或健康检查失败"
}
die() { printf '错误：%s\n' "$1" >&2; exit "${2:-2}"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --domain) [ $# -ge 2 ] || die "--domain 缺少取值" 2; DOMAIN=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数：$1（-h 查看用法）" 2 ;;
    esac
done

command -v docker >/dev/null 2>&1 || die "未找到 docker；请先安装 Docker Engine 与 Compose v2" 2
docker compose version >/dev/null 2>&1 || die "docker 已安装但 docker compose v2 不可用；请升级/启用 Docker Compose" 3

if [ -e .env ] || [ -L .env ]; then
    printf '%s\n' "检测到已有 .env，复用且不修改（重试安全）。"
else
    [ -n "$DOMAIN" ] || die "尚未生成 .env；首次安装必须提供 --domain 或 PMO_DOMAIN" 2
    command -v openssl >/dev/null 2>&1 || die "缺少 openssl，无法生成随机密钥" 2
    secret=$(openssl rand -base64 48 | tr -d '\n')
    db_password=$(openssl rand -hex 24)
    umask 077
    tmp=$(mktemp "$ROOT/.env.XXXXXX") || die "无法创建临时 .env" 2
    trap 'rm -f "$tmp"' EXIT HUP INT TERM
    {
        printf '%s\n' "# 由 scripts/install.sh 首次生成；请独立备份，勿提交版本库。"
        printf 'PMO_DOMAIN=%s\n' "$DOMAIN"
        printf 'PMO_SCOPE=micro\n'
        printf 'DJANGO_SECRET_KEY=%s\n' "$secret"
        printf 'POSTGRES_PASSWORD=%s\n' "$db_password"
        printf 'PMO_AI_ENABLED=0\nPMO_AI_BASE_URL=\n'
        printf 'PMO_AI_MODEL=deepseek-v4.1-flash\nPMO_AI_API_KEY=\n'
    } > "$tmp"
    chmod 600 "$tmp"
    # 原子创建（O_EXCL）避免 check-then-mv 的 TOCTOU：任何并发已存在的 .env 都不覆盖。
    created_env=0
    if (set -C; cat "$tmp" > .env) 2>/dev/null; then
        chmod 600 .env
        created_env=1
    fi
    rm -f "$tmp"
    trap - EXIT HUP INT TERM
    if [ "$created_env" -eq 1 ]; then
        printf '%s\n' "已生成 .env（0600，随机密钥）；请独立安全备份，勿提交版本库。"
    else
        printf '%s\n' ".env 在安装过程中已被其他进程创建，复用现有文件（未覆盖）。" >&2
    fi
fi

env_problem=""
for key in PMO_DOMAIN DJANGO_SECRET_KEY POSTGRES_PASSWORD; do
    value=$(sed -n "s/^${key}=//p" .env | tail -n 1)
    case "$value" in
        "") env_problem=".env 缺少 ${key}" ; break ;;
        replace-*|*replace-with*) env_problem=".env 的 ${key} 仍是占位值" ; break ;;
    esac
done
[ -z "$env_problem" ] || die "${env_problem}；请手工修正已有 .env（本脚本不会覆盖它）" 2
[ -n "$(sed -n 's/^PMO_DOMAIN=//p' .env | tail -n 1)" ] || die ".env 的 PMO_DOMAIN 为空" 2

docker compose config -q || die "docker compose 配置校验失败；请检查 .env 与 compose.yaml（未改动 .env/卷）" 3

if ! docker compose up -d --build; then
    printf '%s\n' "安装失败：compose up 未成功；已保留 .env 与数据卷，未执行 down -v。" >&2
    printf '%s\n' "诊断：docker compose ps；docker compose logs --tail=100 app db proxy（注意脱敏）" >&2
    exit 3
fi

health_tries="${PMO_INSTALL_HEALTH_TRIES:-90}"
health_interval="${PMO_INSTALL_HEALTH_INTERVAL:-2}"
attempt=0
while [ "$attempt" -lt "$health_tries" ]; do
    if docker compose exec -T app python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=3)" \
        >/dev/null 2>&1; then
        break
    fi
    attempt=$((attempt + 1))
    sleep "$health_interval"
done
if [ "$attempt" -ge "$health_tries" ]; then
    printf '%s\n' "安装失败：应用未在 180 秒内通过 /health/；已保留 .env 与数据卷。" >&2
    printf '%s\n' "诊断：docker compose ps；docker compose logs --tail=100 app db proxy（注意脱敏）" >&2
    exit 3
fi

domain=$(sed -n 's/^PMO_DOMAIN=//p' .env | tail -n 1)
printf '%s\n' "安装完成：https://${domain}/" \
    "首次使用：浏览器打开 https://${domain}/setup/ 完成管理员与初始数据设置（或运行 docker compose exec app python manage.py bootstrap）。" \
    "健康检查已通过；重新执行本命令是幂等的，不会覆盖 .env 或数据卷。"
