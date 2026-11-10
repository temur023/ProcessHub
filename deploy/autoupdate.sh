#!/usr/bin/env bash
# Автообновление Process Hub в Docker из Git: новые коммиты текущей ветки клона (на сервере —
# server-deploy) забираются, контейнеры пересобираются и перезапускаются. Запускается таймером systemd.
#
#   sudo bash deploy/autoupdate.sh --install                  включить: проверка каждые 5 минут, первая — сразу
#   sudo bash deploy/autoupdate.sh                            одна проверка; обновляет, если есть новое
#   sudo systemctl disable --now processhub-autoupdate.timer  выключить автообновление
#   journalctl -u processhub-autoupdate -n 50                 что было при последних запусках
#
# Если сборка не удалась, работают прежние контейнеры, а следующий запуск попробует снова:
# развёрнутым считается только коммит, после которого сборка и запуск прошли успешно.
set -euo pipefail

STATE=/var/lib/processhub-autoupdate/deployed

install_timer() {
    local repo=$1
    cat > /etc/systemd/system/processhub-autoupdate.service <<EOF
[Unit]
Description=Process Hub: обновление из Git и пересборка контейнеров
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=HOME=/root
ExecStart=/usr/bin/bash $repo/deploy/autoupdate.sh
TimeoutStartSec=1h
EOF
    cat > /etc/systemd/system/processhub-autoupdate.timer <<'EOF'
[Unit]
Description=Process Hub: проверка обновлений в Git каждые 5 минут

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --quiet --now processhub-autoupdate.timer
    systemctl start --no-block processhub-autoupdate.service
    echo 'автообновление включено, первая проверка уже идёт: journalctl -fu processhub-autoupdate'
}

main() {
    [[ $EUID -eq 0 ]] || { echo 'запустите через sudo' >&2; exit 1; }
    local repo owner home branch target deployed image name args=()
    repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
    if [[ ${1:-} == --install ]]; then install_timer "$repo"; return; fi

    # Прокси банка — для GitHub и сборки; без файла — напрямую.
    [[ -f /etc/profile.d/proxy.sh ]] && . /etc/profile.d/proxy.sh
    # Git — от владельца клона: его токен GitHub и без жалоб git на чужой каталог.
    owner=$(stat -c %U "$repo")
    home=$(getent passwd "$owner" | cut -d: -f6)
    git_as_owner() {
        runuser -u "$owner" -- env HOME="$home" http_proxy="${http_proxy:-}" https_proxy="${https_proxy:-}" \
            no_proxy="${no_proxy:-}" git -C "$repo" "$@"
    }

    exec 9>/run/processhub-autoupdate.lock
    flock -n 9 || { echo 'обновление уже идёт'; return 0; }

    branch=$(git_as_owner symbolic-ref --short HEAD)
    git_as_owner fetch -q origin "$branch"
    target=$(git_as_owner rev-parse "origin/$branch")
    deployed=$(cat "$STATE" 2>/dev/null || true)
    [[ $target == "$deployed" ]] && return 0

    echo "обновление $branch: ${deployed:0:7} -> ${target:0:7}"
    git_as_owner merge -q --ff-only "$target"
    cd "$repo"
    # Сборщик Docker сам в интернет за базовыми образами не ходит: новых нет на сервере — качает демон (через прокси).
    for image in $(awk '/^FROM/ {print $2}' app/Dockerfile backend/Dockerfile knowledge/Dockerfile \
                   document-preview/Dockerfile | sort -u); do
        docker image inspect "$image" >/dev/null 2>&1 || docker pull -q "$image"
    done
    if [[ -n ${https_proxy:-} ]]; then
        for name in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; do args+=(--build-arg "$name=$https_proxy"); done
    fi
    docker compose build "${args[@]}"
    docker compose up -d
    mkdir -p "$(dirname "$STATE")"
    echo "$target" > "$STATE"
    echo "готово: $(git_as_owner log -1 --format='%h %s' "$target")"
}

# Вызов и выход — одной строкой: git merge может заменить этот файл, пока он выполняется.
main "$@"; exit $?
