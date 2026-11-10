# Process Hub на сервере Linux без Docker

Комплект `processhub-<версия>.tar.gz` содержит всё для установки без интернета:
код служб, собранный веб-интерфейс, редактор draw.io, Python 3.13 для Linux и пакеты Python.
На сервер ничего не скачивается.

## Что нужно на сервере

- Linux x86_64 с systemd (Red Hat/Rocky/Oracle Linux 8+ или Ubuntu 22.04+), права `sudo`.
- **nginx** — обязательно: `sudo dnf install nginx` (Ubuntu: `sudo apt-get install nginx`).
- **LibreOffice** — для просмотра Word внутри платформы, по желанию:
  `sudo dnf install libreoffice-writer libreoffice-pyuno liberation-fonts google-carlito-fonts`
  (Ubuntu: `sudo apt-get install --no-install-recommends libreoffice-writer python3-uno fonts-liberation fonts-crosextra-carlito`).
  Без него всё остальное работает, Word-файлы открываются как оригиналы. В Oracle Linux / RHEL 10
  LibreOffice убран из репозиториев — там просмотр Word остаётся выключенным.
- Сеть с сервера приложения: база PostgreSQL (`db-server.internal`, порт — у администраторов БД)
  и Nova (`nova-gateway.internal:11450`).
- Базы `sqb` и `knowledge` созданы по [../db-setup.sql](../db-setup.sql). В `pg_hba.conf`
  разрешён сервер приложения `app-server.internal`. Доступ с терминального сервера `admin-pc.internal`
  платформе не нужен: в базу ходят только её службы.

Файлы `.doc` (Word 97–2003) без конвертера antiword не подготавливаются. Пакет antiword
из Ubuntu содержит известные ошибки памяти, поэтому комплект его не ставит. Такие файлы
сохраните как `.docx` перед загрузкой.

## 1. Собрать комплект (на компьютере разработчика)

```bash
python deploy/linux/build_bundle.py
```

Результат — `transfer/processhub-<версия>.tar.gz` (около 90 МБ). Скрипт сам проверяет,
что пакеты Python ставятся без интернета.

## 2. Перенести на сервер

Путь на сервер приложения — через терминальный сервер банка: удалённый рабочий стол
`mstsc /v:jump-host.internal:3330` (шлюз `control.uzsqb.local`) открывает `TERMINAL-IT`
(Windows Server, `admin-pc.internal`). Скопируйте архивы туда, а оттуда — на сервер приложения:
встроенными `scp`/`ssh` Windows (проверка: `where ssh`), либо через WinSCP и PuTTY.
Порт SSH сервера приложения уточните у администраторов.

## 3. Установить

```bash
tar -xzf processhub-<версия>.tar.gz
cd processhub-<версия>
sudo ./install.sh --port <порт веб-интерфейса>
```

Базы на этом же сервере, без отдельного сервера БД: сначала `sudo dnf install -y nginx postgresql-server`,
затем `sudo ./install.sh --port 3000 --api-port 8080 --local-db` (`--api-port` — API отдельно
на своём порту через nginx; сайт и так ходит в него по `/api/`). Скрипт подготовит PostgreSQL (вход по паролю
только с `127.0.0.1`), создаст базы `sqb` и `knowledge` со случайными паролями и сам впишет их
в файлы настроек — дальше сразу запустятся службы.

С отдельным сервером БД скрипт создаст `/etc/processhub/api.env` и `/etc/processhub/documents.env`
и попросит вписать пароли баз вместо `CHANGE_ME` (и ключ Nova, если он уже выдан):

```bash
sudo nano /etc/processhub/api.env
sudo nano /etc/processhub/documents.env
sudo ./install.sh
```

После этого службы запустятся, и скрипт покажет проверку: доступны ли базы и Nova,
работают ли службы, отвечает ли nginx.

HTTPS — когда будет сертификат:

```bash
sudo ./install.sh --port 443 --tls-cert /etc/ssl/processhub.crt --tls-key /etc/ssl/processhub.key
```

Порт и сертификат запоминаются: следующие обновления их не требуют.

## 4. Перенести данные (карты, пользователи, документы)

На компьютере разработчика — снимок обеих баз:

```bash
knowledge\.venv\Scripts\python deploy\linux\data_export.py
```

Результат — `transfer/processhub-data-<время>.tar.gz`. В нём данные банка и хэши паролей:
передавайте его только на сервер платформы, тем же путём, что и комплект. На сервере,
после установки (службы должны хотя бы раз запуститься — они создают таблицы):

```bash
sudo /opt/processhub/install.sh import-data ~/processhub-data-<время>.tar.gz
```

- Все данные на сервере **заменяются** снимком; скрипт попросит подтвердить словом `yes`.
- Каждая база загружается одной транзакцией: при любой ошибке она остаётся прежней.
- Комплект и снимок должны быть одной версии кода. Если схема таблиц разошлась,
  импорт откажет и покажет, какие столбцы различаются.
- Сессии входа не переносятся — все входят заново. Главный администратор
  переносится вместе с пользователями, шаг 5 тогда не нужен.

## 5. Первый администратор (если данные не переносились)

Зарегистрируйтесь в платформе с рабочей почтой, затем:

```bash
sudo /opt/processhub/install.sh admin ваша.почта@sqb.uz
```

## Обновление

Новый комплект распаковывается рядом и ставится той же командой `sudo ./install.sh`.
Код заменяется, настройки и данные (они в базах) остаются.

## Тестовый стенд без root

Пока нет прав на установку, тот же комплект запускается под обычной учётной записью
(`processhub-user.sh`): всё в `~/processhub`, службы следит `supervise.py`, веб-интерфейс —
`front.py` вместо nginx на порту от 1024 (по умолчанию 8080), автозапуск — `cron @reboot`.
Просмотр Word выключен. Это временный вариант: службы работают под личной учётной записью,
пароли баз — в `~/processhub/etc` (права 600). С правами переходят на `install.sh`.

```bash
./processhub-user.sh install --port 8080
nano ~/processhub/etc/api.env ~/processhub/etc/documents.env   # пароли баз вместо CHANGE_ME
~/processhub/processhub-user.sh start
~/processhub/processhub-user.sh import-data ~/processhub-data-<время>.tar.gz
~/processhub/processhub-user.sh check
```

Остальные команды: `stop`, `restart`, `admin EMAIL`. Журналы — `~/processhub/logs`.

## Права на сервере

Установка, обновление и `import-data` выполняются от root: их запускает администратор
сервера или сопровождающий с полным `sudo` на время работ. Для повседневного сопровождения
достаточно узких прав — файл `/etc/sudoers.d/processhub` (проверка: `visudo -cf <файл>`):

```
tirgashev ALL=(root) /usr/bin/systemctl start processhub.target, /usr/bin/systemctl stop processhub.target, \
    /usr/bin/systemctl restart processhub.target, /usr/bin/systemctl --no-pager status processhub*, \
    /opt/processhub/install.sh check, sudoedit /etc/processhub/api.env, sudoedit /etc/processhub/documents.env
```

`status` — только с `--no-pager`: из пейджера `less`, запущенного от root, можно выйти в оболочку root.

## Управление

| Что | Команда |
|---|---|
| Проверка всего | `sudo /opt/processhub/install.sh check` |
| Заменить данные снимком | `sudo /opt/processhub/install.sh import-data ФАЙЛ` |
| Состояние служб | `systemctl status processhub.target` |
| Перезапуск | `sudo systemctl restart processhub.target` |
| Журнал службы | `journalctl -u processhub-api -n 100` |

Службы: `processhub-api` (8000), `processhub-documents` (8010), `processhub-documents-worker`,
`processhub-documents-analysis`, `processhub-preview` (8020). Все слушают только `127.0.0.1`.
Снаружи открыт один порт nginx: веб-интерфейс, `/api/` и `/drawio/`.

| Где | Что |
|---|---|
| `/opt/processhub` | код, Python, пакеты (владелец root) |
| `/etc/processhub/*.env` | пароли баз, секрет, ключ Nova (права 640) |
| `/var/lib/processhub-api` | кэш раскладки холста |
