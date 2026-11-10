"""Список пользователей платформы: только администратору, без секретов."""
from fastapi.testclient import TestClient

from app.main import app
from app.services.store import get_store
from tests.test_auth import SignedOutTest, email


class AdminUsersTest(SignedOutTest):
    def test_only_admin_sees_all_users_with_activity(self):
        admin_mail, worker_mail = email('admin'), email('worker')
        admin = TestClient(app)
        self.assertEqual(self.register(admin, name='Админ', mail=admin_mail).status_code, 200)
        worker = TestClient(app)
        self.assertEqual(self.register(worker, name='Сотрудник', mail=worker_mail).status_code, 200)

        self.assertEqual(admin.get('/api/v1/admin/users').status_code, 403)
        self.assertEqual(worker.get('/api/v1/admin/users').status_code, 403)

        get_store().set_platform_admin(admin_mail, True, 'Тест списка пользователей')
        res = admin.get('/api/v1/admin/users')
        self.assertEqual(res.status_code, 200, res.text)
        rows = {row['email']: row for row in res.json()}
        self.assertTrue(rows[admin_mail]['isAdmin'])
        got = rows[worker_mail]
        self.assertEqual((got['name'], got['isAdmin'], got['online'], got['activeSessions'], got['versions']),
                         ('Сотрудник', False, True, 1, 0))
        self.assertIsNotNone(got['lastSeenAt'])
        self.assertNotIn('password_hash', got)

        self.assertEqual(worker.post('/api/v1/auth/logout').status_code, 204)
        after = {row['email']: row for row in admin.get('/api/v1/admin/users').json()}[worker_mail]
        self.assertEqual((after['online'], after['activeSessions'], after['lastSeenAt']), (False, 0, None))
