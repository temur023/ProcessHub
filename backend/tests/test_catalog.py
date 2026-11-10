"""Пространства, подпространства и карты (``/api/v1/catalog``)."""
import unittest
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import catalog
from app.services.drawio_parser import parse_drawio_xml
from app.services.store import get_store
from tests.test_pmm_tree import _wide_map

API = '/api/v1/catalog'


class CatalogApiTest(unittest.TestCase):
    def setUp(self):
        # Пустое дерево на каждый тест.
        catalog.reset()
        self.client = TestClient(app)

    def make(self, title, kind='space', parent=None, process=None, status=201):
        res = self.client.post(f'{API}/items', json={'title': title, 'kind': kind, 'parentId': parent,
                                                     'processId': process})
        self.assertEqual(res.status_code, status, res.text)
        return res.json()

    def tree(self):
        data = self.client.get(f'{API}/').json()
        return {n['title']: n for n in data['items']}, [p['id'] for p in data['unplaced']]

    def stored_process(self):
        process = parse_drawio_xml(_wide_map(3), 'p.drawio').model_copy(update={'id': f'proc_{uuid.uuid4().hex[:8]}'})
        res = self.client.post('/api/v1/processes/', json=process.model_dump(mode='json'))
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()['id']

    def test_numbers_levels_and_counts_follow_the_tree(self):
        space = self.make('Банк')
        a = self.make('Департамент A', parent=space['id'])
        b = self.make('Департамент B', parent=space['id'])
        self.make('Карта 1', 'map', b['id'])
        self.make('Карта 2', 'map', b['id'])
        a1 = self.make('Отдел A1', parent=a['id'])
        self.make('Карта A1', 'map', a1['id'])
        nodes, _ = self.tree()
        self.assertEqual({t: n['number'] for t, n in nodes.items()}, {
            'Банк': '', 'Департамент A': '1', 'Отдел A1': '1.1', 'Карта A1': '1.1.1',
            'Департамент B': '2', 'Карта 1': '2.1', 'Карта 2': '2.2'})
        self.assertEqual([nodes[t]['level'] for t in ('Банк', 'Департамент A', 'Отдел A1', 'Карта A1')], [1, 2, 3, 4])
        self.assertEqual((nodes['Банк']['spaceCount'], nodes['Банк']['mapCount']), (3, 3))
        self.assertEqual((nodes['Департамент B']['spaceCount'], nodes['Департамент B']['mapCount']), (0, 2))

    def test_maps_live_only_inside_spaces(self):
        space = self.make('Банк')
        card = self.make('Карта', 'map', space['id'])
        self.make('Карта без места', 'map', None, status=422)
        self.make('Внутри карты', 'map', card['id'], status=422)
        self.make('Пространство в карте', 'space', card['id'], status=422)
        res =self.client.post(f'{API}/items', json={'title': '   ', 'kind': 'space'})
        self.assertEqual(res.status_code, 422)

    def test_move_keeps_the_tree_a_tree(self):
        space = self.make('Банк')
        a = self.make('A', parent=space['id'])
        a1 = self.make('A1', parent=a['id'])
        b = self.make('B', parent=space['id'])
        card = self.make('Карта', 'map', b['id'])
        move = lambda item, parent: self.client.patch(f'{API}/items/{item["id"]}', json={'parentId': parent})
        self.assertEqual(move(a, a1['id']).status_code, 422)          # внутрь своего потомка
        self.assertEqual(move(a, a['id']).status_code, 422)           # в самого себя
        self.assertEqual(move(card, None).status_code, 422)           # карта без пространства
        self.assertEqual(move(card, card['id']).status_code, 422)
        moved = move(b, a['id'])
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(moved.json()['number'], '1.2')               # после A1
        nodes, _ = self.tree()
        self.assertEqual(nodes['Карта']['number'], '1.2.1')
        top = move(a1, None)
        self.assertEqual((top.status_code, top.json()['number'], top.json()['level']), (200, '', 1))

    def test_rename_and_star(self):
        space = self.make('Банк')
        res = self.client.patch(f'{API}/items/{space["id"]}', json={'title': '  Новый   банк '})
        self.assertEqual(res.json()['title'], 'Новый банк')
        self.assertEqual(self.client.patch(f'{API}/items/{space["id"]}', json={'title': '  '}).status_code, 422)
        self.assertTrue(self.client.patch(f'{API}/items/{space["id"]}', json={'favorite': True}).json()['favorite'])
        self.assertEqual(self.client.patch(f'{API}/items/nope', json={'favorite': True}).status_code, 404)

    def test_delete_only_empty_space_and_map_keeps_its_process(self):
        process_id = self.stored_process()
        space = self.make('Банк')
        card = self.make('Карта', 'map', space['id'], process_id)
        self.assertEqual(self.client.delete(f'{API}/items/{space["id"]}').status_code, 409)
        self.assertEqual(self.client.delete(f'{API}/items/{card["id"]}').status_code, 204)
        nodes, unplaced = self.tree()
        self.assertNotIn('Карта', nodes)
        self.assertIn(process_id, unplaced)
        self.assertEqual(self.client.get(f'/api/v1/processes/{process_id}').status_code, 200)
        self.assertEqual(self.client.delete(f'{API}/items/{space["id"]}').status_code, 204)
        self.assertEqual(self.client.delete(f'{API}/items/{space["id"]}').status_code, 404)

    def test_a_process_has_one_place(self):
        process_id = self.stored_process()
        _, unplaced = self.tree()
        self.assertIn(process_id, unplaced)
        space = self.make('Банк')
        card = self.make('Карта', 'map', space['id'], process_id)
        self.assertTrue(card['hasProcess'])
        self.assertNotIn(process_id, self.tree()[1])
        self.make('Вторая', 'map', space['id'], process_id, status=409)
        empty = self.make('Пустая карта', 'map', space['id'])
        self.assertFalse(empty['hasProcess'])
        attach = lambda item, pid: self.client.patch(f'{API}/items/{item["id"]}', json={'processId': pid})
        self.assertEqual(attach(empty, process_id).status_code, 409)
        self.assertEqual(attach(empty, 'proc_missing').status_code, 422)
        self.assertEqual(attach(space, process_id).status_code, 422)
        self.assertFalse(attach(card, None).json()['hasProcess'])     # процесс снят с карты
        self.assertIn(process_id, self.tree()[1])
        self.assertTrue(attach(empty, process_id).json()['hasProcess'])  # загружен в пустую карту

    def test_tree_changed_by_another_process_is_seen_and_kept(self):
        """Другой процесс бэкенда (контейнер, второй рабочий) правит то же дерево в базе."""
        own = self.make('Своё')
        foreign = {'id': 'cat_foreign', 'kind': 'space', 'title': 'Чужое', 'position': 5,
                   'createdAt': '2026-10-04T00:00:00Z', 'favorite': False}
        get_store().upsert_catalog_items([foreign])
        titles, _ = self.tree()
        self.assertIn('Чужое', titles, 'дерево, изменённое в базе другим процессом, не видно')
        # Своя правка не затирает чужой элемент, а чужая правка — свой.
        self.client.patch(f'{API}/items/{own["id"]}', json={'title': 'Своё, переименованное'})
        get_store().upsert_catalog_items([{**foreign, 'title': 'Чужое, переименованное'}])
        titles, _ = self.tree()
        self.assertIn('Своё, переименованное', titles)
        self.assertIn('Чужое, переименованное', titles)
        get_store().delete_catalog_item('cat_foreign')
        titles, _ = self.tree()
        self.assertNotIn('Чужое, переименованное', titles)

    def test_pix_tree_transfer_keeps_numbers_and_is_repeatable(self):
        rows = [
            {'id': 'S', 'parentId': None, 'title': 'BANK', 'level': 1, 'depth': 1, 'createdOn': '2025-08-27T00:00:00Z'},
            {'id': 'D1', 'parentId': 'S', 'title': 'Департамент 1', 'level': 2, 'depth': 2},
            {'id': 'D2', 'parentId': 'S', 'title': 'Департамент 2', 'level': 2, 'depth': 2},
            {'id': 'P21', 'parentId': 'D2', 'title': 'Процесс 2.1', 'level': 3, 'depth': 3},
            {'id': 'P22', 'parentId': 'D2', 'title': 'Процесс 2.2', 'level': 3, 'depth': 3},
        ]
        self.assertEqual(catalog.import_pix_tree(rows), (5, 0))
        nodes, _ = self.tree()
        self.assertEqual({t: (n['kind'], n['number']) for t, n in nodes.items()}, {
            'BANK': ('space', ''), 'Департамент 1': ('space', '1'), 'Департамент 2': ('space', '2'),
            'Процесс 2.1': ('map', '2.1'), 'Процесс 2.2': ('map', '2.2')})
        rows[3] = {**rows[3], 'title': 'Процесс 2.1 (новое имя)'}
        self.assertEqual(catalog.import_pix_tree(rows), (0, 5))
        nodes, _ = self.tree()
        self.assertEqual(len(nodes), 5)
        self.assertEqual(nodes['Процесс 2.1 (новое имя)']['number'], '2.1')


if __name__ == '__main__':
    unittest.main()
