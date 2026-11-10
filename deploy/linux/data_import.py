"""Загрузка снимка data_export.py в базу сервера. Вызывается из install.sh import-data:

    python data_import.py <sqb|knowledge> <переменная с адресом базы>  < processhub-data-….tar.gz

Всё — в одной транзакции: сверка схемы, очистка таблиц, загрузка строк, значения
последовательностей. Ошибка на любом шаге откатывает транзакцию, и данные на сервере
остаются прежними. Триггеры защиты истории и автозаполнения на время загрузки
выключаются (это право владельца таблиц): переносятся готовые строки, а не новые действия.
"""
import json
import os
import sys
import tarfile

import psycopg


def ident(name):
    return '"' + name.replace('"', '""') + '"'


def columns(conn):
    """Столбцы таблиц public: имя -> тип. Порядок не важен — COPY идёт по именам."""
    found = {}
    for table, name, kind in conn.execute("""
            select c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
            from pg_class c join pg_namespace n on n.oid = c.relnamespace
            join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
            where n.nspname = 'public' and c.relkind = 'r' and a.attgenerated = ''"""):
        found.setdefault(table, {})[name] = kind
    return found


def main():
    db, variable = sys.argv[1], sys.argv[2]
    archive = tarfile.open(fileobj=sys.stdin.buffer, mode='r|gz')
    members = iter(archive)
    head = next(members, None)
    if head is None or head.name != 'manifest.json':
        sys.exit('Это не снимок данных Process Hub (нет manifest.json в начале архива)')
    manifest = json.load(archive.extractfile(head))
    if manifest.get('format') != 1 or db not in manifest['databases']:
        sys.exit(f'Снимок другого формата или без базы {db}')
    source = manifest['databases'][db]
    expected = {table['name']: dict(table['columns']) for table in source['tables']}

    with psycopg.connect(os.environ[variable]) as conn:
        current = columns(conn)
        if not current:
            sys.exit(f'В базе {db} нет таблиц: сначала запустите службы (sudo ./install.sh), они создадут схему')
        if current != expected:
            for table in sorted(set(current) | set(expected)):
                here, there = current.get(table), expected.get(table)
                if here is None or there is None:
                    print(f'  {table}: таблица есть только {"в снимке" if here is None else "на сервере"}', file=sys.stderr)
                elif here != there:
                    diff = sorted(name for name in set(here) | set(there) if here.get(name) != there.get(name))
                    print(f'  {table}: различаются столбцы {", ".join(diff)}', file=sys.stderr)
            sys.exit(f'Схема базы {db} на сервере не совпадает со снимком: нужен комплект той же версии, что при выгрузке')

        tables = sorted(current)
        for table in tables:
            conn.execute(f'alter table {ident(table)} disable trigger user')
        conn.execute('truncate ' + ', '.join(ident(table) for table in tables))
        loaded = {}
        prefix = db + '/'
        # Таблицы в архиве уже идут в порядке внешних ключей: сначала родители.
        for member in members:
            if not member.name.startswith(prefix):
                continue
            table = member.name.removeprefix(prefix).removesuffix('.copy')
            names = ', '.join(ident(name) for name in expected[table])
            stream = archive.extractfile(member)
            with conn.cursor().copy(f'copy {ident(table)} ({names}) from stdin') as copy:
                while chunk := stream.read(1 << 20):
                    copy.write(chunk)
            loaded[table] = conn.execute(f'select count(*) from {ident(table)}').fetchone()[0]
        for table in source['tables']:
            if not table.get('skipped') and loaded.get(table['name']) != table['rows']:
                sys.exit(f'Таблица {table["name"]}: загружено {loaded.get(table["name"], 0)} строк из {table["rows"]}')
        for sequence, value in source['sequences'].items():
            if value is None:
                conn.execute('select setval(%s::regclass, 1, false)', [ident(sequence)])
            else:
                conn.execute('select setval(%s::regclass, %s)', [ident(sequence), value])
        # Отложенные проверки внешних ключей — сейчас: иначе триггеры не включить обратно.
        conn.execute('set constraints all immediate')
        for table in tables:
            conn.execute(f'alter table {ident(table)} enable trigger user')
        conn.commit()
    print(f'{db}: загружено {sum(loaded.values())} строк в {len(loaded)} таблиц (снимок от {manifest["created"]})')


if __name__ == '__main__':
    main()
