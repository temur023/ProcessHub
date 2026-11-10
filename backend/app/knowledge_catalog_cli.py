"""Копирует только папки департаментов выбранного пространства из собственной базы Hub."""
import argparse
import json
import os

import httpx

from app.routers.knowledge import context_token
from app.services.catalog import snapshot
from app.services.store import close_store, get_store


def catalog_departments(space_id, expected_count=None):
    tree = snapshot({})
    root = next((item for item in tree.items if item.id == space_id), None)
    if root is None or root.kind != 'space' or root.parentId is not None:
        raise ValueError('Выберите существующее пространство верхнего уровня')
    items = [item for item in tree.items if item.parentId == root.id and item.kind == 'space']
    if not items or (expected_count is not None and len(items) != expected_count):
        raise ValueError(f'Количество папок департаментов: {len(items)}; перенос не выполнен')
    return {'space_id': root.id, 'departments': [
        {'catalog_item_id': item.id, 'name': item.title, 'number': item.number, 'position': index}
        for index, item in enumerate(items)]}


def main():
    parser = argparse.ArgumentParser(description='Перенос папок департаментов из пространства Hub в документы')
    parser.add_argument('--space-id', required=True)
    parser.add_argument('--actor-email', required=True, help='Существующий главный администратор, разрешивший перенос')
    parser.add_argument('--expected-count', type=int)
    args = parser.parse_args()
    try:
        found = get_store().find_user(args.actor_email.strip().lower())
        if not found or not found[0].isAdmin:
            raise ValueError('Нужен существующий главный администратор Hub')
        payload = catalog_departments(args.space_id, args.expected_count)
        secret = os.environ.get('SQB_KNOWLEDGE_SECRET', '')
        url = os.environ.get('SQB_KNOWLEDGE_URL', '').rstrip('/')
        if len(secret) < 32 or not url:
            raise ValueError('Сервис документов не подключён')
        body = json.dumps(payload, ensure_ascii=False).encode()
        actor = found[0]
        credential = context_token(secret, actor.id, 'POST', '/catalog-departments', body, actor.isAdmin)
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            response = client.post(url + '/catalog-departments', content=body, headers={
                'Authorization': 'Bearer ' + credential, 'Content-Type': 'application/json'})
        if response.status_code != 200:
            raise ValueError(f'Перенос не выполнен: сервис документов ответил {response.status_code}')
        print(json.dumps(response.json(), ensure_ascii=False))
    except (ValueError, httpx.HTTPError) as error:
        parser.exit(1, str(error) + '\n')
    finally:
        close_store()


if __name__ == '__main__':
    main()
