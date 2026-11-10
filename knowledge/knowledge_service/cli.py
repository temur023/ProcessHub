"""Назначения делает оператор, а не самостоятельно зарегистрированный пользователь.

Почта sqb.uz сама по себе не даёт полномочий. user-id — проверенный ID Hub.
"""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg

from .config import Settings
from .nova import NovaClient, NovaError
from .store import Store, migrate


async def probe(settings, query=None, model_check=False):
    client = NovaClient(settings)
    try:
        result = await client.probe() if query is None else await client.check_integration(query, model_check)
        print(json.dumps(result, ensure_ascii=False))
    except NovaError as error:
        print(json.dumps({'code': error.code, 'message': str(error), 'request_id': error.request_id}, ensure_ascii=False))
        raise SystemExit(1)
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description='Администрирование сервиса документов')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('migrate')
    commands.add_parser('nova-probe')
    check = commands.add_parser('nova-check', help='Явная проверка поиска и модели, без вывода содержимого документов')
    check.add_argument('--query-file', type=Path, required=True)
    check.add_argument('--model-check', action='store_true')
    role = commands.add_parser('role')
    role.add_argument('--department-id', type=UUID, required=True)
    role.add_argument('--name', required=True)
    for verb in ('grant', 'revoke'):
        access = commands.add_parser(verb)
        access.add_argument('--user-id', type=int, required=True)
        access.add_argument('--department-id', type=UUID, required=True)
        access.add_argument('--permission', choices=['reader', 'owner', 'reviewer'], required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command in ('nova-probe', 'nova-check'):
        query = None
        if args.command == 'nova-check':
            try:
                with args.query_file.open('rb') as source:
                    raw = source.read(80_001)
                query = raw.decode('utf-8')
                if len(raw) > 80_000 or not query.strip() or len(query) > 20_000:
                    raise ValueError()
            except (OSError, UnicodeError, ValueError):
                parser.error('Нужен непустой UTF-8 файл запроса до 20 000 символов')
        asyncio.run(probe(settings, query, getattr(args, 'model_check', False)))
        return
    migrate(settings.database_url)
    if args.command == 'migrate':
        return
    if hasattr(args, 'name') and not args.name.strip():
        parser.error('Название не должно быть пустым')
    if hasattr(args, 'user_id') and args.user_id <= 0:
        parser.error('Нужен положительный user-id Hub')
    with psycopg.connect(settings.database_url) as conn:
        if args.command == 'role':
            entity_id = uuid4()
            conn.execute('INSERT INTO roles(id,department_id,name) VALUES (%s,%s,%s)', (entity_id, args.department_id, args.name.strip()))
            Store.audit(conn, 0, args.department_id, 'operator.role.created', entity_id)
            print(entity_id)
        elif args.command == 'grant':
            conn.execute('INSERT INTO access_grants(user_id,department_id,permission) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING', (args.user_id, args.department_id, args.permission))
            Store.audit(conn, 0, args.department_id, f'operator.access.granted:{args.user_id}:{args.permission}', args.department_id)
        elif args.command == 'revoke':
            conn.execute('DELETE FROM access_grants WHERE user_id=%s AND department_id=%s AND permission=%s', (args.user_id, args.department_id, args.permission))
            Store.audit(conn, 0, args.department_id, f'operator.access.revoked:{args.user_id}:{args.permission}', args.department_id)


if __name__ == '__main__':
    main()
