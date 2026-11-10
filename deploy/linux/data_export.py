"""Снимок данных Process Hub (карты, пользователи, документы) для переноса на сервер.

Запуск на компьютере разработчика окружением с psycopg:
    knowledge\\.venv\\Scripts\\python deploy\\linux\\data_export.py

Адреса баз — как у скриптов scripts\\*.ps1: из окружения, иначе из .env, иначе по
умолчанию. Результат — transfer/processhub-data-<время>.tar.gz; на сервере его загружает
sudo /opt/processhub/install.sh import-data <файл> (data_import.py).

Формат не зависит от версии PostgreSQL: каждая таблица — вывод COPY в текстовом виде,
снятый в одной транзакции REPEATABLE READ, плюс manifest.json со схемой таблиц,
числом строк и значениями последовательностей. Сессии входа не переносятся.
"""
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
SKIP_TABLES = {'sqb': {'sessions'}, 'knowledge': set()}


def database_urls():
    env = {}
    dotenv = ROOT / '.env'
    if dotenv.is_file():
        for line in dotenv.read_text(encoding='utf-8').splitlines():
            match = re.match(r'\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$', line)
            if match and match.group(2):
                env[match.group(1)] = match.group(2).strip('"\'')
    env.update({key: value for key, value in os.environ.items() if value})
    return {
        'sqb': env.get('SQB_DATABASE_URL') or f"postgresql://sqb:{env.get('SQB_DB_PASSWORD', 'sqb')}@localhost:5432/sqb",
        'knowledge': env.get('KNOWLEDGE_DATABASE_URL')
        or f"postgresql://knowledge:{env.get('KNOWLEDGE_DB_PASSWORD', 'knowledge-local-only')}@localhost:5432/knowledge",
    }


def schema(conn):
    """Таблицы схемы public со столбцами, родители — раньше детей (по внешним ключам)."""
    columns = {}
    for table, name, kind in conn.execute("""
            select c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
            from pg_class c join pg_namespace n on n.oid = c.relnamespace
            join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
            where n.nspname = 'public' and c.relkind = 'r' and a.attgenerated = ''
            order by c.relname, a.attnum"""):
        columns.setdefault(table, []).append([name, kind])
    parents = {table: set() for table in columns}
    for child, parent in conn.execute("""
            select c.relname, p.relname from pg_constraint k
            join pg_class c on c.oid = k.conrelid join pg_class p on p.oid = k.confrelid
            join pg_namespace n on n.oid = c.relnamespace
            where k.contype = 'f' and n.nspname = 'public'"""):
        if child != parent:
            parents[child].add(parent)
    order = []
    while parents:
        ready = sorted(table for table, needs in parents.items() if not needs - set(order))
        if not ready:
            sys.exit('Циклические внешние ключи: ' + ', '.join(sorted(parents)))
        order += ready
        for table in ready:
            del parents[table]
    return [(table, columns[table]) for table in order]


def ident(name):
    return '"' + name.replace('"', '""') + '"'


def export(db, url, workdir):
    with psycopg.connect(url) as conn:
        # Один снимок на все таблицы базы: службы могут работать во время выгрузки.
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        conn.read_only = True
        tables, files = [], []
        for table, columns in schema(conn):
            if table in SKIP_TABLES[db]:
                tables.append({'name': table, 'columns': columns, 'skipped': True})
                continue
            path = Path(workdir) / f'{db}.{table}.copy'
            rows = 0
            with path.open('wb') as stream, conn.cursor().copy(
                    f'copy {ident(table)} ({", ".join(ident(c[0]) for c in columns)}) to stdout') as copy:
                for chunk in copy:
                    stream.write(chunk)
                    rows += bytes(chunk).count(b'\n')
            tables.append({'name': table, 'columns': columns, 'rows': rows})
            files.append((f'{db}/{table}.copy', path))
        # last_value пуст, если последовательностью ещё не пользовались.
        sequences = dict(conn.execute("select sequencename, last_value from pg_sequences where schemaname = 'public'"))
        version = conn.info.server_version
        conn.rollback()
    print(f'  {db}: {sum(t.get("rows", 0) for t in tables)} строк в {len(files)} таблицах')
    return {'server_version': version, 'tables': tables, 'sequences': sequences}, files


def main():
    out = ROOT / 'transfer'
    out.mkdir(exist_ok=True)
    archive = out / time.strftime('processhub-data-%Y%m%d-%H%M.tar.gz')
    manifest = {'format': 1, 'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'databases': {}}
    with tempfile.TemporaryDirectory() as workdir:
        files = []
        for db, url in database_urls().items():
            manifest['databases'][db], db_files = export(db, url, workdir)
            files += db_files
        # manifest.json — первым: импорт читает архив потоком и сверяет схему до загрузки.
        with tarfile.open(archive, 'w:gz', compresslevel=6) as tar:
            data = json.dumps(manifest, ensure_ascii=False, indent=1).encode()
            info = tarfile.TarInfo('manifest.json')
            info.size, info.mtime = len(data), int(time.time())
            tar.addfile(info, io.BytesIO(data))
            for name, path in files:
                tar.add(path, name)
    print(f'Готово: {archive} ({archive.stat().st_size / 1e6:.0f} МБ)')
    print('Внутри данные банка и хэши паролей: передавайте только на сервер платформы.')


if __name__ == '__main__':
    main()
