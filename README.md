# SQB Process Hub

Платформа карт процессов банка: импорт draw.io, холст с правкой и статистикой,
история версий, регламент, выгрузка в PIX (`.pmm`) и Processet (BPMN).

Подробности бэкенда — [backend/README.md](backend/README.md).

## Запуск целиком в Docker

Нужен Docker Desktop (на Windows и Mac одинаково).

```bash
docker compose up -d --build
```

Приложение — http://localhost:8080, документация API — http://localhost:8080/api/docs.
Поднимаются четыре сервиса: `db` (PostgreSQL), `api` (FastAPI), `drawio` (редактор
draw.io, развёрнутый у себя — карты банка не уходят на diagrams.net) и `web`
(собранный фронтенд в nginx, который пересылает `/api` в `api`, а `/drawio/` — в
`drawio`). Карты и история версий живут в томе `pgdata` и переживают пересборку и
перезапуск.

Карту можно править прямо на платформе в draw.io — кнопка «Открыть в draw.io»
над картой. Правки сохраняются в ту же карту сами, через пару секунд после
изменения, и при закрытии редактора; правки одного сотрудника подряд в течение
10 минут — одна версия истории («Правка в draw.io»). Если карту за это время
сохранил кто-то другой, сохранение отклоняется, а не стирает его правку.

Без входа платформа закрыта. Сотрудник регистрируется сам — имя, рабочая почта
`@sqb.uz` и пароль; под этим именем его правки видны в «Истории версий». Пароли
хранятся хэшем, сессия живёт 30 дней от последнего действия. Письма платформа не
шлёт, поэтому почта не подтверждается, а забытый пароль сейчас не восстановить.

На тестовом стенде можно включить **быстрый вход** — кнопку на странице входа
без почты и пароля: `SQB_DEMO_LOGIN=<почта@sqb.uz>` в `.env`. Кнопка входит под
этим аккаунтом; если его нет, создаётся обычный аккаунт без прав (права выдаёт
администратор). На рабочем сервере переменную оставляют пустой — тогда кнопки
нет, а `/api/v1/auth/demo` отвечает 404.

Резервная копия базы:

```bash
docker compose exec db pg_dump -U sqb sqb > backup.sql
```

## Разработка

В Docker только база; бэкенд и фронтенд запускаются локально с горячей
перезагрузкой.

```bash
docker compose up -d db drawio
cd backend && PYTHONPATH=. .venv/bin/uvicorn app.main:app --reload --port 8000
cd app && npm run dev
```

Фронтенд — http://localhost:5173 (Vite пересылает `/api` на бэкенд, `/drawio/` — в
контейнер draw.io на порту 8081). Docker-стек и локальный бэкенд работают с одной
и той же базой, поэтому их можно держать запущенными одновременно.

Тесты бэкенда идут без Docker на хранилище в памяти; с тестовой базой — на
PostgreSQL (базу `sqb_test` один раз создаёт
`docker compose exec db createdb -U sqb sqb_test`):

```bash
cd backend && .venv/bin/python -m pytest tests -q
cd backend && SQB_TEST_DATABASE_URL=postgresql://sqb:sqb@localhost:5433/sqb_test .venv/bin/python -m pytest tests -q
```

## На сервере

### Модуль документов департаментов

Новый раздел находится **внутри Process Hub** на `/knowledge`, использует общий
вход, навигацию и оформление. Самостоятельна только серверная часть — `knowledge`.
Настройка `.env`, Nova, департаментов и прав: [knowledge/README.md](knowledge/README.md).
План и фактическая готовность: [docs/knowledge/implementation.md](docs/knowledge/implementation.md).
Анализ инструкции с положением департамента через Nova, этапы, ограничения и подключение (доступ ИБ, ключ ДИИ): [docs/knowledge/ANALYSIS.md](docs/knowledge/ANALYSIS.md).

### Размещение платформы

Тот же `docker compose up -d --build` из клона репозитория; обновление —
`git pull` и снова `docker compose up -d --build`. Перед первым запуском в `.env`
(образец — `.env.example`): свои `SQB_DB_PASSWORD`, `KNOWLEDGE_DB_PASSWORD`,
`SQB_KNOWLEDGE_SECRET`, порты `SQB_WEB_PORT=3000` (страницы) и `SQB_API_PORT=8080`
(API) и `COMPOSE_PROFILES=knowledge`. Базы PostgreSQL — контейнеры `db` и
`knowledge-db` на том же сервере.

Автообновление: `sudo bash deploy/autoupdate.sh --install` ставит таймер systemd —
каждые 5 минут новые коммиты ветки клона забираются, контейнеры пересобираются и
перезапускаются (журнал — `journalctl -u processhub-autoupdate`, выключить —
`sudo systemctl disable --now processhub-autoupdate.timer`). Всё, что попало в ветку,
оказывается на сервере — для тестового стенда.

Данные с другой машины переносятся снимком `deploy/linux/data_export.py`: службы
сначала запускаются (создают таблицы), затем
`docker compose stop api knowledge knowledge-worker knowledge-analysis` и загрузка:

```bash
docker compose run --rm -T -v "$PWD/deploy/linux:/deploy:ro" api python /deploy/data_import.py sqb SQB_DATABASE_URL < снимок.tar.gz
docker compose run --rm -T -v "$PWD/deploy/linux:/deploy:ro" knowledge python /deploy/data_import.py knowledge KNOWLEDGE_DATABASE_URL < снимок.tar.gz
docker compose up -d
```

Платформу — за HTTPS: тогда cookie сессии уходит только по защищённому соединению
(бэкенду нужно знать схему от прокси — `uvicorn --proxy-headers --forwarded-allow-ips <адрес nginx>`).

## Подключение к Nova

Сверка должностных инструкций с положениями идёт через RAG-сервис Nova Platform
(документация — https://nova.example.internal/docs) — базу знаний и языковую модель
банка (код — `backend/app/services/nova/`).
Настройки — в файле `.env` рядом с `docker-compose.yml`, образец — `.env.example`:

- `NOVA_BASE_URL` — адрес RAG-сервиса: с сервера банка `http://nova-gateway.internal:11450/api/v1/services/rag`;
- `NOVA_API_KEY` — ключ `nova_…` со страницы https://nova.example.internal/keys; хранится только на сервере;
- `NOVA_COLLECTION` (`ops_policies`), `NOVA_MAX_PARALLEL` (2), `NOVA_TIMEOUT` (300 с);
- `NOVA_FAKE=1` — заглушка без сети вместо Gateway, для разработки.

Без Docker (корпоративный компьютер) сервер `.env` сам не читает; из корня проекта:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check-nova.ps1     # связь, ключ, база знаний — без платформы
powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1  # сервер платформы с настройками из .env
powershell -ExecutionPolicy Bypass -File scripts\start-documents.ps1  # модуль «Документы»: API (8010), подготовка, анализ через Nova
powershell -ExecutionPolicy Bypass -File scripts\start-drawio.ps1  # редактор draw.io (8081): из transfer\drawio-webapp.zip или с GitHub
```

Модулю документов нужен PostgreSQL 15 или новее (на 14 часть проверок дублей
должностей не работает) и своя база рядом с основной — один раз, в psql под `postgres`:

```sql
CREATE ROLE knowledge LOGIN PASSWORD 'тот же, что KNOWLEDGE_DB_PASSWORD в .env';
CREATE DATABASE knowledge OWNER knowledge;
```

Таблицы создаются сами. Департаменты переносятся из дерева пространств, а
главный администратор назначается командами из [knowledge/README.md](knowledge/README.md)
(`app.admin_cli`, `app.knowledge_catalog_cli`; без Docker — `.venv\Scripts\python -m …`
в папке `backend` после `. ..\scripts\env.ps1`, когда в `.env` заданы адреса из конца `.env.example`). Без Docker просмотр Word внутри
платформы строит установленный Microsoft Word: `start-documents.ps1` поднимает
`document-preview\word_service.py` на порту 8020 (без Word — только скачивание
оригиналов). Старые `.doc` без Docker не подготавливаются —
их сохраняют как `.docx`; анализ DOCX и PDF работает.

Проверка подключения — `GET /api/v1/nova/status` (после входа): связь, ключ и
доступ к базе положений или понятная причина ошибки. Без адреса и ключа
подключение выключено, остальная платформа работает как раньше.
