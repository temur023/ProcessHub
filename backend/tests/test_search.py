"""Поиск по всем картам: шаг по названию, по роли (дорожке) и по ИТ-системе — с картой и страницей."""
import unittest
import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.test_honest_data import MAP


class SearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.role = f'Qidiruv roli {uuid.uuid4().hex[:6]}'
        xml = MAP.replace('value="Bank"', f'value="{cls.role}"')
        res = cls.client.post('/api/v1/import/file', params={'asNew': 'true'},
                              files={'file': (f'search-{uuid.uuid4().hex[:6]}.drawio', xml.encode(), 'application/xml')})
        assert res.status_code == 200, res.text
        cls.process = res.json()

    def search(self, q, field='step'):
        res = self.client.get('/api/v1/search/', params={'q': q, 'field': field})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def mine(self, result):
        return [h for h in result['hits'] if h['processId'] == self.process['id']]

    def test_by_step_name_ignores_case(self):
        hits = self.mine(self.search('swift XABARINI'))
        self.assertEqual([h['nodeName'] for h in hits], ['SWIFT xabarini yuborish'])
        self.assertEqual((hits[0]['pageId'], hits[0]['laneName'], hits[0]['system']), ('', self.role, 'SWIFT'))

    def test_by_role_finds_every_step_of_the_lane(self):
        result = self.search(self.role.upper(), 'role')
        self.assertEqual(len(self.mine(result)), 3)
        self.assertEqual(result['total'], 3)

    def test_by_system(self):
        hits = self.mine(self.search('iabs', 'system'))
        self.assertEqual([h['nodeName'] for h in hits], ['Hisobni tekshirish'])

    def test_like_wildcards_are_literal(self):
        self.assertEqual(self.mine(self.search('%_%')), [])

    def test_short_query_is_refused(self):
        self.assertEqual(self.client.get('/api/v1/search/', params={'q': 'a'}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
