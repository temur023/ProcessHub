"""Библиотека и схемы: повторный импорт без дублей, удаление схемы, сводка в каталоге."""
import unittest
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import catalog
from tests.test_pmm_tree import _wide_map

IMPORT = '/api/v1/import/file'


class LibraryTest(unittest.TestCase):
    def setUp(self):
        catalog.reset()
        self.client = TestClient(app)
        # Своё имя на каждый тест: хранилище общее на весь прогон.
        self.name = f'Kredit {uuid.uuid4().hex[:6]}'

    def upload(self, filename, steps=3, params=None):
        return self.client.post(IMPORT, params=params or {},
                                files={'file': (filename, _wide_map(steps).encode(), 'application/xml')})

    def test_same_name_asks_then_updates_the_existing_map(self):
        first = self.upload(f'{self.name}.drawio', steps=3)
        self.assertEqual(first.status_code, 200, first.text)
        first = first.json()

        # Тот же файл без изменений — та же карта, без вопросов.
        again = self.upload(f'{self.name}.drawio', steps=3)
        self.assertEqual((again.status_code, again.json()['id']), (200, first['id']))

        # Изменённый файл под тем же именем — и копия «(1)» из загрузок — вопрос, а не вторая карта.
        for filename in (f'{self.name}.drawio', f'{self.name} (1) (2).drawio'):
            asked = self.upload(filename, steps=5)
            self.assertEqual(asked.status_code, 409, asked.text)
            detail = asked.json()['detail']
            self.assertEqual(detail['code'], 'same_name')
            self.assertEqual([m['id'] for m in detail['existing']], [first['id']])
            self.assertEqual(detail['existing'][0]['updatedBy'], 'Тестовый Сотрудник')
        names = [p['name'] for p in self.client.get('/api/v1/processes/').json()]
        self.assertEqual(sum(n.startswith(self.name) for n in names), 1)

        # Ставка дорожки, заданная на платформе, переживает обновление из файла.
        process = self.client.get(f"/api/v1/processes/{first['id']}").json()
        process['lanes'][0]['hourlyRate'] = 90000
        self.assertEqual(self.client.post('/api/v1/processes/', json=process).status_code, 201)

        updated = self.upload(f'{self.name}.drawio', steps=5, params={'replace': first['id']})
        self.assertEqual(updated.status_code, 200, updated.text)
        updated = updated.json()
        self.assertEqual(updated['id'], first['id'])
        self.assertEqual(updated['lanes'][0]['hourlyRate'], 90000)
        self.assertGreater(len(updated['nodes']), len(first['nodes']))
        versions = self.client.get(f"/api/v1/processes/{first['id']}/versions").json()
        self.assertEqual((versions[0]['source'], versions[0]['note']), ('import', f'повторный импорт {self.name}.drawio'))

    def test_as_new_keeps_a_separate_map(self):
        first = self.upload(f'{self.name}.drawio', steps=3).json()
        separate = self.upload(f'{self.name}.drawio', steps=5, params={'asNew': 'true'})
        self.assertEqual(separate.status_code, 200, separate.text)
        self.assertNotEqual(separate.json()['id'], first['id'])

    def test_replace_of_a_missing_map_is_404(self):
        self.assertEqual(self.upload('x.drawio', params={'replace': 'proc_missing'}).status_code, 404)

    def test_catalog_shows_steps_errors_and_last_author(self):
        process = self.upload(f'{self.name}.drawio').json()
        data = self.client.get('/api/v1/catalog/').json()
        brief = next(p for p in data['unplaced'] if p['id'] == process['id'])
        steps = sum(1 for n in process['nodes'] if n['type'] in ('task', 'userTask', 'serviceTask', 'subProcess'))
        errors = sum(1 for v in process['validation'] if v['level'] == 'error')
        self.assertEqual((brief['stepCount'], brief['errorCount'], brief['updatedBy']),
                         (steps, errors, 'Тестовый Сотрудник'))
        self.assertGreater(steps, 0)
        self.assertIsNotNone(brief['updatedAt'])

        space = self.client.post('/api/v1/catalog/items', json={'title': 'Банк', 'kind': 'space'}).json()
        self.client.post('/api/v1/catalog/items', json={'title': 'Кредит', 'kind': 'map', 'parentId': space['id'],
                                                       'processId': process['id']})
        items = {n['title']: n for n in self.client.get('/api/v1/catalog/').json()['items']}
        self.assertEqual(items['Кредит']['process']['stepCount'], steps)
        self.assertEqual(items['Кредит']['process']['status'], 'draft')
        self.assertIsNone(items['Банк']['process'])

    def test_scheme_in_a_space_is_not_deleted(self):
        process = self.upload(f'{self.name}.drawio').json()
        space = self.client.post('/api/v1/catalog/items', json={'title': 'Банк', 'kind': 'space'}).json()
        item = self.client.post('/api/v1/catalog/items', json={'title': 'Кредит', 'kind': 'map', 'parentId': space['id'],
                                                              'processId': process['id']}).json()
        refused = self.client.delete(f"/api/v1/processes/{process['id']}")
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn('Кредит', refused.json()['detail'])
        self.assertEqual(self.client.get(f"/api/v1/processes/{process['id']}").status_code, 200)

        # Карта убрана из пространства — схема в «Без пространства», её можно удалить.
        self.assertEqual(self.client.delete(f"/api/v1/catalog/items/{item['id']}").status_code, 204)
        self.assertEqual(self.client.delete(f"/api/v1/processes/{process['id']}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/v1/processes/{process['id']}").status_code, 404)
        unplaced = [p['id'] for p in self.client.get('/api/v1/catalog/').json()['unplaced']]
        self.assertNotIn(process['id'], unplaced)


if __name__ == '__main__':
    unittest.main()
