"""Профиль сотрудника: статистика его версий, смена имени и пароля."""
from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import COOKIE
from tests.test_auth import PASSWORD, SignedOutTest, email
from tests.test_pmm_tree import _wide_map


class ProfileTest(SignedOutTest):
    def signed_in(self, name, mail):
        client = TestClient(app)
        self.assertEqual(self.register(client, name=name, mail=mail).status_code, 200)
        return client

    def import_map(self, client, name):
        res = client.post('/api/v1/import/xml', json={'xml': _wide_map(3), 'fileName': f'{name}.drawio'})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def edit(self, client, pid, name):
        process = client.get(f'/api/v1/processes/{pid}').json()
        self.assertEqual(client.post('/api/v1/processes/', json={**process, 'name': name}).status_code, 201)

    def test_statistics_count_only_own_versions(self):
        ivan_mail = email('ivan')
        ivan = self.signed_in('Иван Петров', ivan_mail)
        olga = self.signed_in('Ольга Ким', email('olga'))
        fresh = ivan.get('/api/v1/profile').json()
        self.assertEqual(fresh['user']['email'], ivan_mail)
        self.assertEqual((fresh['versions'], fresh['bySource'], fresh['maps'], fresh['lastSavedAt'], fresh['recentMaps']),
                         (0, {}, 0, None, []))

        first = self.import_map(ivan, 'Карта Ивана')
        self.edit(ivan, first['id'], 'Карта Ивана, правка')
        second = self.import_map(olga, 'Карта Ольги')
        self.edit(ivan, second['id'], 'Карта Ольги, правка Ивана')
        self.edit(olga, second['id'], 'Карта Ольги, снова Ольга')
        self.assertEqual(olga.post(f'/api/v1/processes/{first["id"]}/versions/1/restore').status_code, 200)

        got = ivan.get('/api/v1/profile').json()
        # Импорт и правка подряд — разные версии; правка Ивана на карте Ольги — его.
        self.assertEqual((got['versions'], got['bySource'], got['maps']), (3, {'import': 1, 'edit': 2}, 2))
        self.assertEqual([(m['id'], m['name'], m['versions']) for m in got['recentMaps']], [
            (second['id'], 'Карта Ольги, снова Ольга', 1),
            (first['id'], 'Карта Ивана', 2),
        ])
        self.assertEqual(got['lastSavedAt'], got['recentMaps'][0]['lastSavedAt'])
        # Контроль: правки Ольги в базе есть, но в профиль Ивана не попали.
        theirs = olga.get('/api/v1/profile').json()
        self.assertEqual((theirs['versions'], theirs['bySource']), (3, {'import': 1, 'edit': 1, 'restore': 1}))

    def test_rename(self):
        client = self.signed_in('Иван Петров', email())
        pid = self.import_map(client, 'Карта')['id']
        res = client.patch('/api/v1/profile', json={'name': '  Иван   Сидоров '})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()['name'], 'Иван Сидоров')
        self.assertEqual(client.get('/api/v1/auth/me').json()['name'], 'Иван Сидоров')
        self.assertEqual(client.get(f'/api/v1/processes/{pid}/versions').json()[0]['author'], 'Иван Сидоров')
        res = client.patch('/api/v1/profile', json={'name': '   '})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(client.get('/api/v1/auth/me').json()['name'], 'Иван Сидоров')

    def test_password_change(self):
        mail = email()
        here = self.signed_in('Иван Петров', mail)
        there = TestClient(app)
        self.assertEqual(there.post('/api/v1/auth/login', json={'email': mail, 'password': PASSWORD}).status_code, 200)

        res = here.post('/api/v1/profile/password', json={'currentPassword': 'не тот', 'newPassword': 'новый-пароль-2'})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()['detail'], 'Текущий пароль неверный')
        res = here.post('/api/v1/profile/password', json={'currentPassword': PASSWORD, 'newPassword': 'short'})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(there.get('/api/v1/auth/me').status_code, 200)  # отказы ничего не меняют

        res = here.post('/api/v1/profile/password', json={'currentPassword': PASSWORD, 'newPassword': 'новый-пароль-2'})
        self.assertEqual(res.status_code, 204, res.text)
        self.assertEqual(here.get('/api/v1/auth/me').status_code, 200)
        self.assertEqual(there.get('/api/v1/auth/me').status_code, 401)
        login = TestClient(app).post('/api/v1/auth/login', json={'email': mail, 'password': PASSWORD})
        self.assertEqual(login.status_code, 401)
        login = TestClient(app).post('/api/v1/auth/login', json={'email': mail, 'password': 'новый-пароль-2'})
        self.assertEqual(login.status_code, 200)
        self.assertIn(COOKIE, login.cookies)

    def test_profile_is_closed_without_a_session(self):
        self.assertEqual(self.client.get('/api/v1/profile').status_code, 401)
        self.assertEqual(self.client.patch('/api/v1/profile', json={'name': 'X'}).status_code, 401)
        self.assertEqual(self.client.post('/api/v1/profile/password',
                                          json={'currentPassword': 'a', 'newPassword': 'b'}).status_code, 401)
