#!/bin/sh
# Consistent offline backup: app writes stop briefly; DB remains up.
set -eu
umask 077

backup_dir="${1:-backups/$(date +%Y%m%d-%H%M%S)}"
if [ -e "$backup_dir" ] || [ -L "$backup_dir" ]; then
    printf '错误：输出目录已存在，拒绝覆盖：%s\n' "$backup_dir" >&2
    exit 1
fi

if [ "$backup_dir" = "${backup_dir%/*}" ]; then
    backup_parent="."
else
    backup_parent="${backup_dir%/*}"
fi

mkdir -p "$backup_parent"
if ! mkdir -m 700 "$backup_dir"; then
    printf '错误：无法创建输出目录：%s\n' "$backup_dir" >&2
    exit 1
fi

incomplete_marker="$backup_dir/.incomplete"
: > "$incomplete_marker"

original_services=""
restore_complete=0

restore_original_services() {
    [ -z "$original_services" ] || docker compose start $original_services >/dev/null
}

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$restore_complete" -eq 0 ] && [ -n "$original_services" ]; then
        restore_original_services >/dev/null 2>&1 || true
    fi
    exit "$status"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

running_services=$(docker compose ps --status running --services)
for service in app worker; do
    for running_service in $running_services; do
        if [ "$running_service" = "$service" ]; then
            original_services="$original_services $service"
            original_services="${original_services# }"
            break
        fi
    done
done

if [ -n "$original_services" ]; then
    docker compose stop $original_services
fi

docker compose exec -T db sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$backup_dir/database.dump"
docker compose run --rm --no-deps --entrypoint python app -c 'import sys,tarfile; t=tarfile.open(fileobj=sys.stdout.buffer,mode="w|gz"); t.add("/app/runtime",arcname="runtime"); t.close()' > "$backup_dir/evidence.tar.gz"
docker compose exec -T db pg_restore --list < "$backup_dir/database.dump" > "$backup_dir/database.contents.txt"

(
    cd "$backup_dir"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum database.dump evidence.tar.gz database.contents.txt
    else
        shasum -a 256 database.dump evidence.tar.gz database.contents.txt
    fi
) > "$backup_dir/SHA256SUMS"

restore_original_services
restore_complete=1
rm "$incomplete_marker"

printf '%s\n' "备份已保存到 ${backup_dir}；.env须通过独立安全渠道备份。"
