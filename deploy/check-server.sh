#!/usr/bin/env bash
# Проверка сервера приложения перед развёртыванием Process Hub.
# Ничего не устанавливает и не меняет: только читает сведения о системе и сети.
# Запуск на сервере:  bash check-server.sh > server-report.txt 2>&1
# Файл server-report.txt не содержит паролей; его можно передать разработчику.
DB_HOST=${DB_HOST:-db-server.internal}
DB_PORT=${DB_PORT:-5432}
NOVA_HOST=${NOVA_HOST:-nova-gateway.internal}
NOVA_PORT=${NOVA_PORT:-11450}

section() { printf '\n=== %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }
port() { timeout 5 bash -c "</dev/tcp/$1/$2" 2>/dev/null && echo 'открыт' || echo 'нет ответа'; }

section 'Система'
grep -E '^(PRETTY_NAME|ID|ID_LIKE|VERSION_ID)=' /etc/os-release
uname -r
echo "CPU: $(nproc)"
free -h | head -2
df -h / /opt /var /srv 2>/dev/null

section 'Права'
id
sudo -n true 2>/dev/null && echo 'sudo без пароля: есть' || echo 'sudo без пароля: нет (возможно, с паролем)'

section 'Программы'
for p in docker podman python3 python3.13 python3.12 python3.11 pip3 node npm nginx psql pg_dump soffice antiword git curl unzip systemctl; do
    if have "$p"; then printf '%-12s %s\n' "$p" "$("$p" --version 2>&1 | head -1)"; else printf '%-12s нет\n' "$p"; fi
done
have docker && { docker compose version 2>&1 | head -1; docker info --format 'Docker daemon: {{.ServerVersion}}' 2>&1 | head -1; }

section 'Менеджер пакетов'
for p in apt-get dnf yum; do have "$p" && echo "$p"; done
have apt-get && apt-cache policy python3 nginx libreoffice-writer antiword postgresql-client 2>/dev/null | grep -E '^[a-z]|Candidate'
have dnf && dnf -q list --available python3.12 nginx libreoffice-writer postgresql 2>/dev/null | tail -n +1 | head -20

section 'Сеть'
ip -4 -brief address 2>/dev/null
echo "БД $DB_HOST:$DB_PORT -> $(port "$DB_HOST" "$DB_PORT")"
echo "Nova $NOVA_HOST:$NOVA_PORT -> $(port "$NOVA_HOST" "$NOVA_PORT")"
echo "pypi.org:443 напрямую -> $(port pypi.org 443)"
env | grep -iE '^(https?|no)_proxy=' | sed -E 's#//[^@/]*@#//***@#' || echo 'Прокси в окружении не задан'
have curl && echo "PyPI через curl (с учётом прокси): HTTP $(curl -s -m 10 -o /dev/null -w '%{http_code}' https://pypi.org/simple/fastapi/ 2>/dev/null)"

section 'Занятые порты'
(ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | head -30

section 'SELinux и межсетевой экран'
have getenforce && getenforce
systemctl is-active firewalld 2>/dev/null && echo 'firewalld активен'
have ufw && ufw status 2>/dev/null | head -3
echo
echo 'Готово.'
