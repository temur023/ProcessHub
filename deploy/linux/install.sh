#!/usr/bin/env bash
# Установка и обновление Process Hub на сервере Linux (x86_64, systemd) без Docker
# и без интернета: Python и все пакеты — в комплекте. Запуск из распакованного комплекта:
#
#   sudo ./install.sh [--port 80] [--tls-cert FILE --tls-key FILE]   установить или обновить
#                     [--api-port 8080]                               API отдельно, через nginx
#                     [--local-db]                                    базы в PostgreSQL этого сервера
#   sudo ./install.sh check                                           проверить базы, Nova и службы
#   sudo ./install.sh admin EMAIL                                     назначить главного администратора
#   sudo ./install.sh import-data ФАЙЛ                                заменить данные снимком data_export.py
#
# Повторный запуск с новым комплектом обновляет код и перезапускает службы; настройки
# в /etc/processhub (пароли баз, ключ Nova) не трогает. Порт и сертификат запоминаются.
set -euo pipefail
shopt -u patsub_replacement 2>/dev/null || true

BUNDLE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APP=/opt/processhub
ETC=/etc/processhub
UNITS=(processhub-api processhub-documents processhub-documents-worker processhub-documents-analysis processhub-preview)

say() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
die() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }

# Команда от имени служебной учётной записи с её файлом настроек — так же, как её видит служба.
envrun() {
    local user=$1 file=$2 dir=$3
    shift 3
    systemd-run --quiet --wait --pipe --collect --uid="$user" --gid="$user" \
        -p EnvironmentFile="$file" -p WorkingDirectory="$dir" -E PYTHONDONTWRITEBYTECODE=1 "$@"
}

# Ответ HTTP-адреса с этого сервера (сертификат не проверяется: адрес локальный).
probe() {
    "$APP/venv/bin/python" - "$1" <<'PY'
import ssl, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=5, context=ssl._create_unverified_context()) as r:
        print(r.status)
except Exception as e:
    print(getattr(e, 'code', None) or type(e).__name__)
PY
}

load_conf() {
    PORT=80 API_PORT='' TLS_CERT='' TLS_KEY=''
    # shellcheck source=/dev/null
    [[ -f $ETC/install.conf ]] && . "$ETC/install.conf"
    return 0
}

placeholders() { grep -l 'CHANGE_ME' "$ETC/api.env" "$ETC/documents.env" 2>/dev/null || true; }

check() {
    load_conf
    say "Версия: $(cat "$APP/VERSION" 2>/dev/null || echo 'не установлена')"
    [[ -x $APP/venv/bin/python ]] || die 'Process Hub не установлен: sudo ./install.sh'

    say 'Настройки'
    local missing
    missing=$(placeholders)
    if [[ -n $missing ]]; then note "не заполнены пароли баз (CHANGE_ME): $missing"; else note 'пароли баз заполнены'; fi
    grep -q '^NOVA_API_KEY=.\+' "$ETC/api.env" && note 'ключ Nova задан' || note 'ключ Nova не задан: анализ через Nova выключен'

    say 'Базы данных (с этого сервера)'
    local db='import os, sys, psycopg
url = os.environ.get(sys.argv[1], "")
try:
    with psycopg.connect(url, connect_timeout=5) as c:
        print("доступна, PostgreSQL", c.info.server_version // 10000)
except Exception as e:
    print("НЕТ:", str(e).strip().splitlines()[0])'
    printf '   sqb:       '; envrun sqb "$ETC/api.env" "$APP/backend" "$APP/venv/bin/python" -c "$db" SQB_DATABASE_URL || true
    printf '   knowledge: '; envrun knowledge "$ETC/documents.env" "$APP/knowledge" "$APP/venv/bin/python" -c "$db" KNOWLEDGE_DATABASE_URL || true

    say 'Nova'
    printf '   '; envrun knowledge "$ETC/documents.env" "$APP/knowledge" "$APP/venv/bin/python" -c 'import os, socket, urllib.parse
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

    say 'Службы'
    local unit
    for unit in "${UNITS[@]}"; do
        systemctl is-enabled --quiet "$unit" 2>/dev/null || continue
        printf '   %-34s %s\n' "$unit" "$(systemctl is-active "$unit" || true)"
    done
    note "журнал службы: journalctl -u processhub-api -n 50"

    say 'Ответы по HTTP'
    local scheme=http
    [[ -n $TLS_CERT ]] && scheme=https
    printf '   API платформы  127.0.0.1:8000  %s\n' "$(probe http://127.0.0.1:8000/api/health)"
    printf '   документы      127.0.0.1:8010  %s\n' "$(probe http://127.0.0.1:8010/health)"
    systemctl is-enabled --quiet processhub-preview 2>/dev/null &&
        printf '   просмотр Word  127.0.0.1:8020  %s\n' "$(probe http://127.0.0.1:8020/health)"
    printf '   nginx          порт %-9s %s\n' "$PORT" "$(probe "$scheme://127.0.0.1:$PORT/api/health")"
    [[ -n $API_PORT ]] && printf '   nginx, API     порт %-9s %s\n' "$API_PORT" "$(probe "http://127.0.0.1:$API_PORT/api/health")"
    if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q 'Status: active'; then
        note "включён ufw: порт $PORT должен быть разрешён (sudo ufw allow $PORT/tcp)"
    fi
    if systemctl is-active --quiet firewalld 2>/dev/null; then
        note "включён firewalld: порт $PORT должен быть разрешён (sudo firewall-cmd --permanent --add-port=$PORT/tcp && sudo firewall-cmd --reload)"
    fi
}

admin() {
    [[ -n ${1:-} ]] || die 'укажите почту: sudo ./install.sh admin имя@sqb.uz'
    note 'Учётная запись должна быть уже зарегистрирована в платформе.'
    envrun sqb "$ETC/api.env" "$APP/backend" "$APP/venv/bin/python" -m app.admin_cli grant \
        --email "$1" --reason "${2:-Первичное назначение при установке на сервер}"
}

import_data() {
    [[ -f ${1:-} ]] || die 'укажите файл: sudo /opt/processhub/install.sh import-data processhub-data-<время>.tar.gz'
    [[ -x $APP/venv/bin/python ]] || die 'Process Hub не установлен: sudo ./install.sh'
    [[ -z $(placeholders) ]] || die 'сначала впишите пароли баз в /etc/processhub/*.env и запустите установку'
    say 'Все карты, пользователи и документы на сервере будут заменены данными из файла'
    local answer
    read -r -p '   Продолжить? Введите yes: ' answer
    [[ $answer == yes ]] || die 'отменено'
    systemctl stop processhub.target
    # Архив передаётся на вход: служебным учётным записям не нужен доступ к папке с файлом.
    local db var env dir
    for db in sqb knowledge; do
        if [[ $db == sqb ]]; then var=SQB_DATABASE_URL env=$ETC/api.env dir=$APP/backend
        else var=KNOWLEDGE_DATABASE_URL env=$ETC/documents.env dir=$APP/knowledge; fi
        if ! envrun "$db" "$env" "$dir" "$APP/venv/bin/python" "$APP/data_import.py" "$db" "$var" < "$1"; then
            systemctl start processhub.target
            die "загрузка базы $db не выполнена, её данные не изменены (см. сообщение выше)"
        fi
    done
    systemctl start processhub.target
    note 'Пользователям нужно войти заново: сессии входа не переносятся.'
    check
}

# PostgreSQL на этом же сервере (--local-db): кластер, вход по паролю с 127.0.0.1, базы sqb
# и knowledge со случайными паролями — сразу в файлы настроек вместо CHANGE_ME.
setup_local_db() {
    command -v postgresql-setup >/dev/null || die 'нет PostgreSQL: sudo dnf install -y postgresql-server'
    local data=/var/lib/pgsql/data
    [[ -f $data/PG_VERSION ]] || postgresql-setup --initdb
    # Службы ходят по TCP с паролем, а по умолчанию там ident — без системной учётки не пустит.
    sed -i -E 's/^(host[[:space:]]+all[[:space:]]+all[[:space:]]+(127\.0\.0\.1\/32|::1\/128)[[:space:]]+)(ident|peer|md5)[[:space:]]*$/\1scram-sha-256/' \
        "$data/pg_hba.conf"
    systemctl enable --quiet --now postgresql
    systemctl reload postgresql
    local db file var password
    for db in sqb knowledge; do
        if [[ $db == sqb ]]; then file=$ETC/api.env var=SQB_DATABASE_URL; else file=$ETC/documents.env var=KNOWLEDGE_DATABASE_URL; fi
        grep -q "^$var=.*CHANGE_ME" "$file" || { note "$db: база уже указана в $file"; continue; }
        password=$("$APP/python/bin/python3" -c 'import secrets; print(secrets.token_hex(24))')
        # SQL с паролем — через вход psql, а не в командной строке, которую видят другие процессы.
        printf '%s\n' "DO \$\$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '$db') THEN ALTER ROLE $db LOGIN PASSWORD '$password';
            ELSE CREATE ROLE $db LOGIN PASSWORD '$password'; END IF; END \$\$;" |
            (cd /tmp && runuser -u postgres -- psql -q -v ON_ERROR_STOP=1)
        (cd /tmp && runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname = '$db'") | grep -q 1 ||
            (cd /tmp && runuser -u postgres -- createdb -O "$db" -E UTF8 -T template0 "$db")
        sed -i "s|^$var=.*|$var=postgresql://$db:$password@127.0.0.1:5432/$db|" "$file"
        note "$db: база и пароль созданы, адрес записан в $file"
    done
}

install_all() {
    load_conf
    local local_db=''
    while [[ $# -gt 0 ]]; do
        case $1 in
            --local-db) local_db=yes; shift ;;
            --port) PORT=$2; shift 2 ;;
            --api-port) API_PORT=$2; shift 2 ;;
            --tls-cert) TLS_CERT=$2; shift 2 ;;
            --tls-key) TLS_KEY=$2; shift 2 ;;
            *) die "неизвестный параметр: $1" ;;
        esac
    done
    [[ $PORT =~ ^[0-9]+$ ]] || die "порт должен быть числом: $PORT"
    [[ -z $API_PORT || ( $API_PORT =~ ^[0-9]+$ && $API_PORT != "$PORT" ) ]] ||
        die "порт API должен быть числом и отличаться от порта сайта: $API_PORT"
    [[ -z $TLS_CERT && -z $TLS_KEY ]] || [[ -r $TLS_CERT && -r $TLS_KEY ]] ||
        die 'для HTTPS нужны оба файла: --tls-cert и --tls-key'

    say "Проверка сервера (комплект $(cat "$BUNDLE/VERSION"))"
    [[ $(uname -m) == x86_64 ]] || die "комплект собран для x86_64, а сервер — $(uname -m)"
    command -v systemctl >/dev/null || die 'нужен systemd'
    local office_packages='apt-get install --no-install-recommends libreoffice-writer libreoffice-calc libreoffice-impress python3-uno fonts-liberation fonts-crosextra-carlito'
    command -v dnf >/dev/null && office_packages='dnf install libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-pyuno liberation-fonts google-carlito-fonts'
    command -v nginx >/dev/null || die "не установлен nginx. Администратору сервера: sudo ${office_packages%% *} install nginx"
    local preview=yes
    if ! command -v soffice >/dev/null || ! /usr/bin/python3 -c 'import uno' 2>/dev/null; then
        preview=no
        note 'LibreOffice не найден: просмотр Word внутри платформы будет выключен (остальное работает).'
        note "Чтобы включить: sudo $office_packages"
        note 'и запустите install.sh снова. В Oracle Linux / RHEL 10 этих пакетов в репозиториях нет.'
    fi

    say 'Служебные учётные записи: sqb, knowledge, preview (без входа в систему)'
    local user
    for user in sqb knowledge preview; do
        id -u "$user" >/dev/null 2>&1 ||
            useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$user"
    done

    say "Файлы платформы в $APP"
    systemctl stop processhub.target 2>/dev/null || true
    install -d -m 755 "$APP"
    local part
    for part in backend knowledge document-preview web drawio; do
        rm -rf "${APP:?}/$part"
        cp -R "$BUNDLE/$part" "$APP/$part"
    done
    rm -rf "$APP/python" "$APP/venv"
    tar -xzf "$BUNDLE/python.tar.gz" -C "$APP"
    "$APP/python/bin/python3" -m venv "$APP/venv"
    PIP_DISABLE_PIP_VERSION_CHECK=1 "$APP/venv/bin/pip" install --quiet --no-index \
        --find-links "$BUNDLE/wheels" -r "$BUNDLE/requirements.txt"
    cp "$BUNDLE/install.sh" "$BUNDLE/VERSION" "$BUNDLE/data_import.py" "$APP/"
    chown -R root:root "$APP"
    chmod -R u=rwX,go=rX "$APP"
    chmod 755 "$APP/install.sh"

    say "Настройки в $ETC"
    install -d -m 755 "$ETC"
    local secret preview_url=''
    [[ $preview == yes ]] && preview_url=http://127.0.0.1:8020
    secret=$(grep -h '^SQB_KNOWLEDGE_SECRET=' "$ETC/api.env" "$ETC/documents.env" 2>/dev/null | head -1 | cut -d= -f2- || true)
    [[ ${#secret} -ge 32 ]] || secret=$("$APP/python/bin/python3" -c 'import secrets; print(secrets.token_urlsafe(48))')
    # Файлы с секретом закрыты сразу при создании, права служб выдаются ниже.
    if [[ ! -f $ETC/api.env ]]; then
        (umask 077; sed "s|@SECRET@|$secret|" "$BUNDLE/api.env.example" > "$ETC/api.env")
        note "создан $ETC/api.env"
    fi
    if [[ ! -f $ETC/documents.env ]]; then
        (umask 077; sed -e "s|@SECRET@|$secret|" -e "s|@PREVIEW_URL@|$preview_url|" "$BUNDLE/documents.env.example" > "$ETC/documents.env")
        note "создан $ETC/documents.env"
    fi
    # Адрес просмотра Word следует за наличием LibreOffice: поставили позже — включится при повторной установке.
    sed -i "s|^DOCUMENT_PREVIEW_URL=.*|DOCUMENT_PREVIEW_URL=$preview_url|" "$ETC/documents.env"
    # Тот же сервис показывает Excel и PowerPoint из материалов карт — ему нужен и API.
    if grep -q '^DOCUMENT_PREVIEW_URL=' "$ETC/api.env"; then
        sed -i "s|^DOCUMENT_PREVIEW_URL=.*|DOCUMENT_PREVIEW_URL=$preview_url|" "$ETC/api.env"
    else
        printf 'DOCUMENT_PREVIEW_URL=%s\n' "$preview_url" >> "$ETC/api.env"
    fi
    if [[ -n $local_db ]]; then
        say 'PostgreSQL на этом сервере'
        setup_local_db
    fi
    chown root:sqb "$ETC/api.env" && chmod 640 "$ETC/api.env"
    chown root:knowledge "$ETC/documents.env" && chmod 640 "$ETC/documents.env"
    printf 'PORT=%q\nAPI_PORT=%q\nTLS_CERT=%q\nTLS_KEY=%q\n' "$PORT" "$API_PORT" "$TLS_CERT" "$TLS_KEY" > "$ETC/install.conf"

    say 'Службы systemd'
    cp "$BUNDLE"/systemd/* /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --quiet processhub.target
    local unit
    for unit in "${UNITS[@]}"; do
        if [[ $unit == processhub-preview && $preview == no ]]; then
            systemctl disable --quiet --now "$unit" 2>/dev/null || true
        else
            systemctl enable --quiet "$unit"
        fi
    done

    say "nginx: порт $PORT$([[ -n $TLS_CERT ]] && echo ', HTTPS')"
    local listen=$PORT tls='' site link='' default_removed='' main_conf=/etc/nginx/nginx.conf main_edited=''
    if [[ -n $TLS_CERT ]]; then
        listen="$PORT ssl"
        tls=$'    ssl_certificate '"$TLS_CERT"$';\n    ssl_certificate_key '"$TLS_KEY"$';\n    ssl_protocols TLSv1.2 TLSv1.3;'
    fi
    if [[ -d /etc/nginx/sites-enabled ]]; then
        site=/etc/nginx/sites-available/processhub
        link=/etc/nginx/sites-enabled/processhub
    else
        site=/etc/nginx/conf.d/processhub.conf
        # Red Hat: стандартный сайт описан прямо в nginx.conf и тоже помечен «по умолчанию».
        if grep -q 'listen[^;]*default_server' "$main_conf"; then
            [[ -f $main_conf.processhub.bak ]] || cp -p "$main_conf" "$main_conf.processhub.bak"
            sed -i 's/\(listen[^;]*\) default_server/\1/' "$main_conf"
            main_edited=yes
            note "со стандартного сайта в $main_conf снят признак default_server (копия: nginx.conf.processhub.bak)"
        fi
    fi
    # Сервер без IPv6: «listen [::]:80» стандартного сайта не даёт nginx запуститься вовсе.
    if [[ ! -e /proc/net/if_inet6 ]] && grep -q '^[[:space:]]*listen[[:space:]]\+\[::\]:' "$main_conf"; then
        [[ -f $main_conf.processhub.bak ]] || cp -p "$main_conf" "$main_conf.processhub.bak"
        sed -i -E 's/^([[:space:]]*)(listen[[:space:]]+\[::\]:)/\1# \2/' "$main_conf"
        main_edited=yes
        note "IPv6 на сервере выключен: в $main_conf закомментированы строки listen [::]"
    fi
    local conf
    conf=$(<"$BUNDLE/processhub.nginx.conf")
    conf=${conf//@LISTEN@/$listen}
    conf=${conf//@TLS@/$tls}
    if [[ -n $API_PORT ]]; then
        conf+=$'\n'$(<"$BUNDLE/processhub-api.nginx.conf")
        conf=${conf//@API_PORT@/$API_PORT}
    fi
    printf '%s\n' "$conf" > "$site"
    [[ -n $link ]] && ln -sfn "$site" "$link"
    # Стандартная страница nginx тоже отвечает «по умолчанию» и перехватила бы адрес платформы.
    if [[ -L /etc/nginx/sites-enabled/default ]]; then
        default_removed=$(readlink /etc/nginx/sites-enabled/default)
        rm /etc/nginx/sites-enabled/default
        note 'отключена стандартная страница nginx (sites-enabled/default)'
    fi
    local test_log
    test_log=$(mktemp)
    if ! nginx -t 2>"$test_log"; then
        rm -f "$site" ${link:+"$link"}
        [[ -n $default_removed ]] && ln -s "$default_removed" /etc/nginx/sites-enabled/default
        [[ -n $main_edited ]] && cp -p "$main_conf.processhub.bak" "$main_conf"
        cat "$test_log" >&2
        die 'nginx отклонил настройку — изменения nginx отменены (см. сообщение выше)'
    fi
    rm -f "$test_log"
    systemctl enable --quiet nginx
    systemctl reload-or-restart nginx

    local missing
    missing=$(placeholders)
    if [[ -n $missing ]]; then
        say 'Осталось вписать пароли баз'
        note "Замените CHANGE_ME в файлах: $missing"
        note 'и запустите sudo ./install.sh ещё раз — службы стартуют после этого.'
        return
    fi

    say 'Запуск служб'
    systemctl restart processhub.target
    local i
    for i in $(seq 30); do
        [[ $(probe http://127.0.0.1:8000/api/health) == 200 && $(probe http://127.0.0.1:8010/health) == 200 ]] && break
        sleep 2
    done
    check
    say "Готово. Адрес платформы: $([[ -n $TLS_CERT ]] && echo https || echo http)://<адрес сервера>:$PORT/"
    note 'Первый вход: зарегистрируйтесь в платформе, затем: sudo /opt/processhub/install.sh admin ваша@sqb.uz'
}

[[ $EUID -eq 0 ]] || die 'запустите через sudo'
case ${1:-} in
    check) check ;;
    admin) shift; admin "$@" ;;
    import-data) shift; import_data "$@" ;;
    -h|--help) sed -n '2,11p' "${BASH_SOURCE[0]}" ;;
    *) install_all "$@" ;;
esac
