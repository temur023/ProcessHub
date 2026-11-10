-- Базы Process Hub на сервере БД (db-server.internal). Нужен PostgreSQL 15 или новее:
-- на 14 часть проверок дублей должностей в модуле «Документы» не работает.
-- Выполняет администратор PostgreSQL под суперпользователем, например: psql -U postgres -f db-setup.sql
-- Пароли подставляются при выполнении и передаются отдельно; в Git их не сохраняют.
-- Таблицы создают сами сервисы при первом запуске, поэтому пользователи — владельцы своих баз.

CREATE ROLE sqb LOGIN PASSWORD '<пароль для sqb>';
CREATE DATABASE sqb OWNER sqb ENCODING 'UTF8' TEMPLATE template0;

CREATE ROLE knowledge LOGIN PASSWORD '<пароль для knowledge>';
CREATE DATABASE knowledge OWNER knowledge ENCODING 'UTF8' TEMPLATE template0;

-- В pg_hba.conf разрешить подключение только с сервера приложения (app-server.internal):
--   host  sqb        sqb        app-server.internal/32  scram-sha-256
--   host  knowledge  knowledge  app-server.internal/32  scram-sha-256
-- и перечитать настройки: SELECT pg_reload_conf();
