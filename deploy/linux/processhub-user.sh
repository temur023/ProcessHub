#!/usr/bin/env bash
# Process Hub без root — временный тестовый стенд под учётной записью сопровождающего,
# пока нет прав на полную установку (install.sh). Всё живёт в ~/processhub; веб-интерфейс —
# на своём порту (по умолчанию 8080) без nginx (front.py); просмотр Word выключен.
#
#   ./processhub-user.sh install [--port 8080]       установить или обновить из распакованного комплекта
#   ~/processhub/processhub-user.sh start|stop|restart
#   ~/processhub/processhub-user.sh check            базы, Nova, службы
#   ~/processhub/processhub-user.sh import-data ФАЙЛ заменить данные снимком data_export.py
#   ~/processhub/processhub-user.sh admin EMAIL      назначить главного администратора
#
# После перезагрузки сервера службы поднимает cron (@reboot). Журналы — ~/processhub/logs.
set -euo pipefail

BUNDLE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APP=$HOME/processhub
PY=$APP/venv/bin/python
PIDFILE=$APP/state/supervisor.pid

say() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }

port() { cat "$APP/etc/port" 2>/dev/null || echo 8080; }
running() { [[ -f $PIDFILE ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }
placeholders() { grep -l 'CHANGE_ME' "$APP/etc/api.env" "$APP/etc/documents.env" 2>/dev/null || true; }

probe() {
    "$PY" - "$1" <<'PY'
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=5) as r:
        print(r.status)
except Exception as e:
    print(getattr(e, 'code', None) or type(e).__name__)
PY
}

start() {
    if running; then note "службы уже работают (порт $(port))"; return; fi
    [[ -z $(placeholders) ]] || die "впишите пароли баз вместо CHANGE_ME: $(placeholders)"
    mkdir -p "$APP/logs" "$APP/state"
    # Прежний запуск оборвался (kill -9, сбой)? Его службы держали бы порты — снимаем их группу,
    # только если в ней действительно наши процессы (номер мог достаться другой программе).
    local old
    old=$(cat "$PIDFILE" 2>/dev/null || true)
    [[ -n $old ]] && pgrep -g "$old" -f "$APP/venv" >/dev/null && kill -TERM -- "-$old" 2>/dev/null && sleep 2
    # Отдельный сеанс: службы не завершатся при выходе из SSH.
    setsid nohup "$PY" "$APP/supervise.py" run "$(port)" >> "$APP/logs/supervisor.log" 2>&1 < /dev/null &
    local i
    for i in $(seq 30); do
        [[ $(probe http://127.0.0.1:8000/api/health) == 200 && $(probe http://127.0.0.1:8010/health) == 200 ]] && break
        sleep 2
    done
    if [[ $(probe http://127.0.0.1:8000/api/health) == 200 ]]; then
        note "службы запущены, веб-интерфейс: http://$(hostname -I | awk '{print $1}'):$(port)/"
    else
        note "службы запущены, но API пока не отвечает (нет базы?): ~/processhub/processhub-user.sh check"
    fi
}

stop() {
    running || { note 'службы не запущены'; return 0; }
    local pid i
    pid=$(cat "$PIDFILE")
    kill -TERM "$pid"
    for i in $(seq 20); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    # Не остановились за 20 с — вся группа процессов разом.
    kill -0 "$pid" 2>/dev/null && kill -KILL -- "-$pid" 2>/dev/null || true
    rm -f "$PIDFILE"
    note 'службы остановлены'
}

check() {
    [[ -x $PY ]] || die 'не установлено: ./processhub-user.sh install'
    say "Версия: $(cat "$APP/VERSION")"
    local missing
    missing=$(placeholders)
    if [[ -n $missing ]]; then note "не заполнены пароли баз (CHANGE_ME): $missing"; else note 'пароли баз заполнены'; fi
    grep -q '^NOVA_API_KEY=.\+' "$APP/etc/api.env" && note 'ключ Nova задан' || note 'ключ Nova не задан: анализ через Nova выключен'

    say 'Базы данных (с этого сервера)'
    local db='import os, sys, psycopg
try:
    with psycopg.connect(os.environ.get(sys.argv[1], ""), connect_timeout=5) as c:
        print("доступна, PostgreSQL", c.info.server_version // 10000)
except Exception as e:
    print("НЕТ:", str(e).strip().splitlines()[0])'
    printf '   sqb:       '; "$PY" "$APP/supervise.py" exec api "$PY" -c "$db" SQB_DATABASE_URL || true
    printf '   knowledge: '; "$PY" "$APP/supervise.py" exec documents "$PY" -c "$db" KNOWLEDGE_DATABASE_URL || true

    say 'Nova'
    printf '   '; "$PY" "$APP/supervise.py" exec documents "$PY" -c 'import os, socket, urllib.parse
url = urllib.parse.urlparse(os.environ.get("NOVA_BASE_URL", ""))
if not url.hostname:
    print("адрес не задан")
else:
    port = url.port or (443 if url.scheme == "https" else 80)
    try:
        socket.create_connection((url.hostname, port), timeout=5).close()
        print(f"{url.hostname}:{port} доступен")
    except OSError as e:
        print(f"{url.hostname}:{port} НЕТ: {e}")' || true

    say "Службы: $(running && echo 'работают' || echo 'не запущены')"
    printf '   API платформы  127.0.0.1:8000  %s\n' "$(probe http://127.0.0.1:8000/api/health)"
    printf '   документы      127.0.0.1:8010  %s\n' "$(probe http://127.0.0.1:8010/health)"
    printf '   веб-интерфейс  порт %-9s %s\n' "$(port)" "$(probe "http://127.0.0.1:$(port)/api/health")"
    note "журналы: $APP/logs (tail -n 50 $APP/logs/api.log)"
}

import_data() {
    [[ -f ${1:-} ]] || die 'укажите файл: ~/processhub/processhub-user.sh import-data processhub-data-<время>.tar.gz'
    [[ -z $(placeholders) ]] || die 'сначала впишите пароли баз в ~/processhub/etc/*.env'
    say 'Все карты, пользователи и документы в базах будут заменены данными из файла'
    local answer db var kind
    read -r -p '   Продолжить? Введите yes: ' answer
    [[ $answer == yes ]] || die 'отменено'
    stop
    for db in sqb knowledge; do
        if [[ $db == sqb ]]; then var=SQB_DATABASE_URL kind=api; else var=KNOWLEDGE_DATABASE_URL kind=documents; fi
        if ! "$PY" "$APP/supervise.py" exec "$kind" "$PY" "$APP/data_import.py" "$db" "$var" < "$1"; then
            start
            die "загрузка базы $db не выполнена, её данные не изменены (см. сообщение выше)"
        fi
    done
    start
    note 'Пользователям нужно войти заново: сессии входа не переносятся.'
}

admin() {
    [[ -n ${1:-} ]] || die 'укажите почту: ~/processhub/processhub-user.sh admin имя@sqb.uz'
    "$PY" "$APP/supervise.py" exec api "$PY" -m app.admin_cli grant --email "$1" \
        --reason "${2:-Первичное назначение на тестовом стенде}"
}

install_all() {
    local port_value
    port_value=$(port)
    while [[ $# -gt 0 ]]; do
        case $1 in
            --port) port_value=$2; shift 2 ;;
            *) die "неизвестный параметр: $1" ;;
        esac
    done
    [[ $port_value =~ ^[0-9]+$ && $port_value -ge 1024 ]] || die "порт — число от 1024 (ниже без root нельзя): $port_value"
    [[ $EUID -ne 0 ]] || die 'этот вариант — для обычной учётной записи; от root ставьте install.sh'
    [[ $(uname -m) == x86_64 ]] || die "комплект собран для x86_64, а сервер — $(uname -m)"

    say "Установка в $APP (комплект $(cat "$BUNDLE/VERSION"))"
    stop
    mkdir -p "$APP/etc" "$APP/logs" "$APP/state"
    chmod 700 "$APP" "$APP/etc"
    local part
    for part in backend knowledge web drawio; do
        rm -rf "${APP:?}/$part"
        cp -R "$BUNDLE/$part" "$APP/$part"
    done
    rm -rf "$APP/python" "$APP/venv"
    tar -xzf "$BUNDLE/python.tar.gz" -C "$APP"
    "$APP/python/bin/python3" -m venv "$APP/venv"
    PIP_DISABLE_PIP_VERSION_CHECK=1 "$PY" -m pip install --quiet --no-index \
        --find-links "$BUNDLE/wheels" -r "$BUNDLE/requirements.txt"
    cp "$BUNDLE/front.py" "$BUNDLE/supervise.py" "$BUNDLE/data_import.py" "$BUNDLE/processhub-user.sh" "$BUNDLE/VERSION" "$APP/"
    chmod 755 "$APP/processhub-user.sh"

    say "Настройки в $APP/etc"
    local secret
    secret=$(grep -h '^SQB_KNOWLEDGE_SECRET=' "$APP/etc/api.env" "$APP/etc/documents.env" 2>/dev/null | head -1 | cut -d= -f2- || true)
    [[ ${#secret} -ge 32 ]] || secret=$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')
    if [[ ! -f $APP/etc/api.env ]]; then
        (umask 077; sed "s|@SECRET@|$secret|" "$BUNDLE/api.env.example" > "$APP/etc/api.env")
        note "создан $APP/etc/api.env"
    fi
    if [[ ! -f $APP/etc/documents.env ]]; then
        # Просмотра Word без root нет: LibreOffice ставит администратор.
        (umask 077; sed -e "s|@SECRET@|$secret|" -e 's|@PREVIEW_URL@||' "$BUNDLE/documents.env.example" > "$APP/etc/documents.env")
        note "создан $APP/etc/documents.env"
    fi
    chmod 600 "$APP/etc/api.env" "$APP/etc/documents.env"
    echo "$port_value" > "$APP/etc/port"

    say 'Автозапуск после перезагрузки сервера (cron @reboot)'
    # «no crontab for …» — cron доступен, просто записей ещё нет.
    local listing allowed=yes
    listing=$(crontab -l 2>&1) || { [[ $listing == *'no crontab'* ]] && listing='' || allowed=''; }
    if [[ -n $allowed ]]; then
        { printf '%s\n' "$listing" | grep -v -e 'processhub-user.sh start' -e '^$' || true
          echo "@reboot $APP/processhub-user.sh start >> $APP/logs/supervisor.log 2>&1"; } | crontab -
        note 'добавлен'
    else
        note 'cron недоступен: после перезагрузки запускайте ~/processhub/processhub-user.sh start'
    fi

    local missing
    missing=$(placeholders)
    if [[ -n $missing ]]; then
        say 'Осталось вписать пароли баз'
        note "Замените CHANGE_ME в файлах: $missing (nano $APP/etc/api.env)"
        note "и запустите: $APP/processhub-user.sh start"
        return
    fi
    say 'Запуск служб'
    start
    check
}

case ${1:-} in
    install) shift; install_all "$@" ;;
    start) start ;;
    stop) stop ;;
    restart) stop; start ;;
    check|status) check ;;
    import-data) shift; import_data "$@" ;;
    admin) shift; admin "$@" ;;
    *) sed -n '2,12p' "${BASH_SOURCE[0]}" ;;
esac
