"""Справочник ролей: одна ставка на роль для всех карт, своя ставка дорожки важнее."""
import unittest
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services.process_stats import DEFAULT_HOURLY_RATE
from tests.test_pmm_tree import _wide_map


class RolesTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Своя роль на каждый тест: хранилище и ставки общие на весь прогон.
        self.role = f'Kredit menejeri {uuid.uuid4().hex[:6]}'

    def tearDown(self):
        self.client.put('/api/v1/roles/rate', json={'name': self.role, 'rate': None})

    def import_map(self, steps):
        xml = _wide_map(steps).replace('value="Bank"', f'value="{self.role}"')
        res = self.client.post('/api/v1/import/file', params={'asNew': 'true'},
                               files={'file': (f'{uuid.uuid4().hex}.drawio', xml.encode(), 'application/xml')})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def lane_stats(self, process):
        res = self.client.post('/api/v1/canvas/stats', json=process)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()['lanes'][0]

    def test_role_rate_applies_to_every_map_and_lane_rate_wins(self):
        first, second = self.import_map(3), self.import_map(5)
        self.assertEqual(self.lane_stats(first)['rateSource'], 'default')
        before = self.lane_stats(first)['costUzs']

        self.assertEqual(self.client.put('/api/v1/roles/rate', json={'name': f'  {self.role.upper()} ', 'rate': 60000})
                         .status_code, 204)
        for process in (first, second):
            lane = self.lane_stats(process)
            self.assertEqual((lane['hourlyRate'], lane['rateSource'], lane['rateIsDefault']), (60000, 'role', False))
        self.assertNotEqual(self.lane_stats(first)['costUzs'], before)

        # Своя ставка дорожки в этой карте — важнее ставки роли.
        own = {**first, 'lanes': [{**first['lanes'][0], 'hourlyRate': 90000}]}
        self.assertEqual((self.lane_stats(own)['hourlyRate'], self.lane_stats(own)['rateSource']), (90000, 'lane'))

        roles = {r['name']: r for r in self.client.get('/api/v1/roles/').json()['roles']}
        role = roles[self.role]
        self.assertEqual((role['maps'], role['steps'], role['rate']), (2, 8, 60000))

        self.assertEqual(self.client.put('/api/v1/roles/rate', json={'name': self.role, 'rate': None}).status_code, 204)
        lane = self.lane_stats(first)
        self.assertEqual((lane['hourlyRate'], lane['rateSource']), (DEFAULT_HOURLY_RATE, 'default'))
        self.assertIsNone({r['name']: r for r in self.client.get('/api/v1/roles/').json()['roles']}[self.role]['rate'])

    def test_to_be_maps_do_not_count_in_role_work(self):
        as_is, to_be = self.import_map(3), self.import_map(5)
        to_be['passport']['stage'] = 'to_be'
        self.assertEqual(self.client.post('/api/v1/processes/', json=to_be).status_code, 201)
        role = {r['name']: r for r in self.client.get('/api/v1/roles/').json()['roles']}[self.role]
        self.assertEqual((role['maps'], role['steps']), (1, 3))
        brief = {p['id']: p for p in self.client.get('/api/v1/catalog/').json()['unplaced']}
        self.assertEqual((brief[to_be['id']]['stage'], brief[as_is['id']]['stage']), ('to_be', None))

    def test_negative_rate_is_refused(self):
        self.assertEqual(self.client.put('/api/v1/roles/rate', json={'name': self.role, 'rate': -1}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
