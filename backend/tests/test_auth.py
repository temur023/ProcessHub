"""Регистрация, вход, выход, закрытый без сессии API и авторы версий.

Остальные тесты ходят в API от имени подставленного сотрудника
(``conftest.signed_in``); здесь подмена снята — работает настоящая сессия.
"""
import unittest
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import COOKIE, current_user, verify_password
from app.services.store import get_store
from tests.test_pmm_tree import _wide_map

PASSWORD = 'длинный-пароль-1'


def email(name='ivan'):
    """Своя почта на каждый тест: сотрудники в хранилище общие на весь прогон."""
    return f'{name}.{uuid.uuid4().hex[:8]}@sqb.uz'


class SignedOutTest(unittest.TestCase):
    def setUp(self):
        override = app.dependency_overrides.pop(current_user, None)
        if override:
            self.addCleanup(app.dependency_overrides.__setitem__, current_user, override)
        self.client = TestClient(app)

    def register(self, client=None, name='Иван Петров', mail=None, password=PASSWORD):
        return (client or self.client).post('/api/v1/auth/register', json={
            'name': name, 'email': mail or email(), 'password': password})


class AuthTest(SignedOutTest):
    def test_registration_signs_in(self):
        mail = email()
        res = self.register(name='  Иван   Петров ', mail='  ' + mail.upper() + ' ')
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual((res.json()['name'], res.json()['email']), ('Иван Петров', mail))
        cookie = res.headers['set-cookie']
        self.assertIn(f'{COOKIE}=', cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=lax', cookie)
        me = self.client.get('/api/v1/auth/me')
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()['email'], mail)
        self.assertFalse(me.json()['isAdmin'])

    def test_browser_cannot_assign_admin_and_existing_session_refreshes_permissions(self):
        mail = email('admin')
        result = self.client.post('/api/v1/auth/register', json={
            'name': 'Сотрудник', 'email': mail, 'password': PASSWORD, 'isAdmin': True})
        self.assertFalse(result.json()['isAdmin'])
        get_store().set_platform_admin(mail, True, 'Проверка первичного назначения')
        self.assertTrue(self.client.get('/api/v1/auth/me').json()['isAdmin'])
        another = email('backup')
        self.register(TestClient(app), mail=another)
        get_store().set_platform_admin(another, True, 'Резервный администратор')
        get_store().set_platform_admin(mail, False, 'Отзыв')
        self.assertFalse(self.client.get('/api/v1/auth/me').json()['isAdmin'])

    def test_registration_refusals(self):
        for mail in ('ivan@gmail.com', 'ivan@sqb.uz.example.com', 'ivan@notsqb.uz', 'ivan sqb.uz', '@sqb.uz'):
            res = self.register(mail=mail)
            self.assertEqual(res.status_code, 422, mail)
            self.assertIn('@sqb.uz', res.json()['detail'])
        res = self.register(password='1234567')
        self.assertEqual(res.status_code, 422)
        self.assertIn('не короче 8', res.json()['detail'])
        res = self.register(name='   ')
        self.assertEqual(res.status_code, 422)
        self.assertIn('имя', res.json()['detail'])
        self.assertNotIn(COOKIE, self.client.cookies)
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code, 401)

    def test_same_email_in_any_case_is_taken(self):
        mail = email()
        self.assertEqual(self.register(mail=mail).status_code, 200)
        res = self.register(TestClient(app), mail=mail.upper())
        self.assertEqual(res.status_code, 409)
        self.assertIn('уже зарегистрирована', res.json()['detail'])

    def test_sign_in(self):
        mail = email()
        self.register(TestClient(app), mail=mail)
        for body in ({'email': mail, 'password': PASSWORD + 'x'}, {'email': email('nobody'), 'password': PASSWORD}):
            res = self.client.post('/api/v1/auth/login', json=body)
            self.assertEqual(res.status_code, 401)
            self.assertEqual(res.json()['detail'], 'Неверная почта или пароль')
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code, 401)
        res = self.client.post('/api/v1/auth/login', json={'email': f' {mail.upper()} ', 'password': PASSWORD})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.client.get('/api/v1/auth/me').json()['email'], mail)

    def test_sign_out_ends_the_session(self):
        self.register()
        token = self.client.cookies[COOKIE]
        self.assertEqual(self.client.get('/api/v1/processes/').status_code, 200)
        self.assertEqual(self.client.post('/api/v1/auth/logout').status_code, 204)
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code, 401)
        # Контроль: cookie закрытой сессии, присланная снова, не открывает API.
        stale = TestClient(app)
        stale.cookies.set(COOKIE, token)
        self.assertEqual(stale.get('/api/v1/processes/').status_code, 401)

    def test_api_is_closed_without_a_session(self):
        for method, path in (('get', '/api/v1/processes/'), ('get', '/api/v1/catalog/'),
                             ('post', '/api/v1/import/xml'), ('post', '/api/v1/canvas/stats'),
                             ('get', '/api/v1/processes/x/versions'), ('post', '/api/v1/analytics/x/mining/recalculate'),
                             ('get', '/api/v1/auth/me')):
            res = getattr(self.client, method)(path)
            self.assertEqual(res.status_code, 401, path)
            self.assertEqual(res.json()['detail'], 'Войдите в систему')
        forged = TestClient(app)
        forged.cookies.set(COOKIE, 'not-a-session')
        self.assertEqual(forged.get('/api/v1/processes/').status_code, 401)
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.assertEqual(self.client.get('/api/docs').status_code, 200)

    def test_session_expires_after_30_idle_days(self):
        self.register()
        store = get_store()
        self.addCleanup(setattr, store, 'session_seconds', store.session_seconds)
        self.assertEqual(store.session_seconds, 30 * 24 * 3600)
        store.session_seconds = 0
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code, 401)

    def test_password_is_stored_hashed(self):
        a, b = email('a'), email('b')
        self.register(TestClient(app), mail=a)
        self.register(TestClient(app), mail=b)
        hash_a, hash_b = get_store().find_user(a)[1], get_store().find_user(b)[1]
        self.assertTrue(hash_a.startswith('scrypt$'))
        self.assertNotIn(PASSWORD, hash_a)
        self.assertNotEqual(hash_a, hash_b)  # соль своя у каждого
        self.assertTrue(verify_password(PASSWORD, hash_a))
        self.assertFalse(verify_password(PASSWORD + 'x', hash_a))


class AuthorTest(SignedOutTest):
    """Кто сохранил — тот и автор версии, каким бы путём ни сохранял."""

    def test_versions_have_their_authors(self):
        ivan, olga = TestClient(app), TestClient(app)
        ivan_mail, olga_mail = email('ivan'), email('olga')
        self.register(ivan, name='Иван Петров', mail=ivan_mail)
        self.register(olga, name='Ольга Ким', mail=olga_mail)
        res = ivan.post('/api/v1/import/xml', json={'xml': _wide_map(3), 'fileName': 'authors.drawio'})
        self.assertEqual(res.status_code, 200, res.text)
        pid = res.json()['id']

        def edit(client, name):
            process = client.get(f'/api/v1/processes/{pid}').json()
            res = client.post('/api/v1/processes/', json={**process, 'name': name})
            self.assertEqual(res.status_code, 201, res.text)

        edit(olga, 'Правка Ольги')
        edit(olga, 'Ещё правка Ольги')
        edit(ivan, 'Правка Ивана')
        self.assertEqual(olga.post(f'/api/v1/processes/{pid}/versions/1/restore').status_code, 200)
        xml = ivan.get(f'/api/v1/import/{pid}/export/drawio').text
        self.assertIn('Qadam 2', xml)
        revision = ivan.get(f'/api/v1/processes/{pid}').json()['revision']
        res = ivan.post(f'/api/v1/import/{pid}/drawio', json={'xml': xml.replace('Qadam 2', 'Qadam ikki'),
                                                              'revision': revision})
        self.assertEqual(res.status_code, 200, res.text)

        versions = olga.get(f'/api/v1/processes/{pid}/versions').json()
        self.assertEqual([(v['version'], v['source'], v['author'], v['authorEmail']) for v in versions], [
            (5, 'drawio', 'Иван Петров', ivan_mail),
            (4, 'restore', 'Ольга Ким', olga_mail),
            (3, 'edit', 'Иван Петров', ivan_mail),
            (2, 'edit', 'Ольга Ким', olga_mail),
            (1, 'import', 'Иван Петров', ivan_mail),
        ])
        self.assertEqual(olga.get(f'/api/v1/processes/{pid}/versions/2').json()['name'], 'Ещё правка Ольги')


if __name__ == '__main__':
    unittest.main()


def test_demo_login_is_off_by_default_and_signs_into_the_configured_account(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    monkeypatch.delenv('SQB_DEMO_LOGIN', raising=False)
    assert client.get('/api/v1/auth/options').json() == {'demo': False, 'demoEmail': None}
    assert client.post('/api/v1/auth/demo').status_code == 404
    monkeypatch.setenv('SQB_DEMO_LOGIN', 'outsider@example.com')
    assert client.post('/api/v1/auth/demo').status_code == 404
    monkeypatch.setenv('SQB_DEMO_LOGIN', 'Demo.Stand@sqb.uz')
    assert client.get('/api/v1/auth/options').json() == {'demo': True, 'demoEmail': 'demo.stand@sqb.uz'}
    first = client.post('/api/v1/auth/demo')
    assert first.status_code == 200 and first.json()['email'] == 'demo.stand@sqb.uz'
    assert first.json()['isAdmin'] is False
    assert 'sqb_session=' in first.headers['set-cookie'] and 'HttpOnly' in first.headers['set-cookie']
    second = TestClient(app).post('/api/v1/auth/demo')
    assert second.json()['id'] == first.json()['id']
