"""Хранилище карт: ревизии, защита от перезаписи, история версий, дерево, переезд из JSON.

Одни и те же проверки идут на хранилище в памяти и на PostgreSQL. Второе —
только при ``SQB_TEST_DATABASE_URL`` и в своей схеме, чтобы не трогать
данные остальных тестов.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from app.models.process import BusinessProcess, Geometry, ProcessNode
from app.services.store import (
    MemoryStore, ProcessConflict, UserExists, migrate_from_files, restore,
)
from app.services.drawio_parser import parse_drawio_xml
from tests.test_pmm_tree import _wide_map

TEST_DATABASE_URL = os.environ.get('SQB_TEST_DATABASE_URL')
SCHEMA = 'store_contract'


def sample(pid='proc-store-1', name='Карта') -> BusinessProcess:
    process = parse_drawio_xml(_wide_map(3), 'store.drawio')
    return process.model_copy(update={'id': pid, 'name': name})


def renamed(process: BusinessProcess, name: str) -> BusinessProcess:
    return process.model_copy(update={'name': name})


class StoreContract:
    """Проверки, общие для обоих хранилищ; ``make_store`` даёт пустое."""

    def make_store(self):
        raise NotImplementedError

    def setUp(self):
        self.store = self.make_store()

    def test_save_and_read_back(self):
        saved = self.store.save(sample(), 'import')
        self.assertEqual(saved.revision, 1)
        got = self.store.get('proc-store-1')
        self.assertEqual(got.model_dump(), saved.model_dump())
        info = self.store.info('proc-store-1')
        self.assertEqual((info.name, info.nodeCount, info.revision), ('Карта', len(saved.nodes), 1))
        self.assertIsNone(self.store.get('nope'))
        self.assertIsNone(self.store.info('nope'))

    def test_list_is_sorted_by_name_without_loading_maps(self):
        self.store.save(sample('b', 'Бета'), 'import')
        self.store.save(sample('a', 'альфа'), 'import')
        self.assertEqual([i.id for i in self.store.infos()], ['a', 'b'])

    def test_read_copy_does_not_change_the_stored_map(self):
        self.store.save(sample(), 'import')
        got = self.store.get('proc-store-1')
        got.name = 'изменено без сохранения'
        self.assertEqual(self.store.get('proc-store-1').name, 'Карта')

    def test_save_from_an_outdated_copy_is_refused(self):
        first = self.store.save(sample(), 'import')
        second = self.store.save(renamed(first, 'Вторая'), 'edit', expected_revision=first.revision)
        self.assertEqual(second.revision, 2)
        with self.assertRaises(ProcessConflict) as caught:
            self.store.save(renamed(first, 'По старой копии'), 'edit', expected_revision=first.revision)
        self.assertEqual(caught.exception.revision, 2)
        self.assertEqual(self.store.get('proc-store-1').name, 'Вторая')
        # Без ревизии — без проверки (шаблон, импорт).
        self.assertEqual(self.store.save(renamed(first, 'Третья'), 'edit').revision, 3)

    def test_edits_in_a_row_make_one_version_and_later_ones_a_new_one(self):
        self.store.squash_seconds = 600
        base = self.store.save(sample(), 'import')
        self.store.save(renamed(base, 'Правка 1'), 'edit')
        self.store.save(renamed(base, 'Правка 2'), 'edit')
        versions = self.store.versions('proc-store-1')
        self.assertEqual([(v.version, v.source) for v in versions], [(2, 'edit'), (1, 'import')])
        self.assertEqual(versions[0].name, 'Правка 2')
        self.assertEqual(versions[0].revision, 3)
        self.assertEqual(self.store.get_version('proc-store-1', 2).name, 'Правка 2')
        self.store.squash_seconds = 0
        self.store.save(renamed(base, 'Правка 3'), 'edit')
        self.assertEqual([v.version for v in self.store.versions('proc-store-1')], [3, 2, 1])
        self.assertEqual(self.store.get_version('proc-store-1', 1).name, 'Карта')

    def test_drawio_saves_in_a_row_make_one_version(self):
        self.store.squash_seconds = 600
        ivan = self.store.create_user('ivan@sqb.uz', 'Иван Петров', 'h')
        olga = self.store.create_user('olga@sqb.uz', 'Ольга Ким', 'h')
        base = self.store.save(sample(), 'import', author=ivan.id)
        self.store.save(renamed(base, 'draw.io 1'), 'drawio', author=ivan.id)
        self.store.save(renamed(base, 'draw.io 2'), 'drawio', author=ivan.id)
        versions = self.store.versions('proc-store-1')
        self.assertEqual([(v.version, v.source, v.revision) for v in versions], [(2, 'drawio', 3), (1, 'import', 1)])
        self.assertEqual(self.store.get_version('proc-store-1', 2).name, 'draw.io 2')
        # Другой сотрудник, правка холста после draw.io и draw.io после окна — новые версии.
        self.store.save(renamed(base, 'draw.io Ольги'), 'drawio', author=olga.id)
        self.store.save(renamed(base, 'холст Ольги'), 'edit', author=olga.id)
        self.store.save(renamed(base, 'draw.io Ольги снова'), 'drawio', author=olga.id)
        self.store.squash_seconds = 0
        self.store.save(renamed(base, 'draw.io Ольги позже'), 'drawio', author=olga.id)
        self.assertEqual([v.source for v in self.store.versions('proc-store-1')],
                         ['drawio', 'drawio', 'edit', 'drawio', 'drawio', 'import'])

    def test_import_always_makes_a_new_version(self):
        base = self.store.save(sample(), 'import')
        self.store.save(renamed(base, 'Новый файл'), 'import')
        self.assertEqual(len(self.store.versions('proc-store-1')), 2)

    def test_saving_the_same_content_makes_no_version(self):
        self.store.squash_seconds = 0
        base = self.store.save(sample(), 'import')
        self.store.save(base, 'edit')
        versions = self.store.versions('proc-store-1')
        self.assertEqual([v.version for v in versions], [1])
        self.assertEqual(versions[0].revision, 2)

    def test_analytics_and_registry_amend_the_latest_version(self):
        self.store.squash_seconds = 0
        base = self.store.save(sample(), 'import')
        metrics = base.miningMetrics.model_copy(update={'conformanceRate': 42.0})
        self.store.save(base.model_copy(update={'miningMetrics': metrics}), 'analytics')
        self.store.save(renamed(base, 'С кейсом'), 'registry')
        versions = self.store.versions('proc-store-1')
        self.assertEqual(len(versions), 1)
        self.assertEqual(self.store.get_version('proc-store-1', 1).name, 'С кейсом')

    def test_restore_brings_old_content_back_as_a_new_version(self):
        self.store.squash_seconds = 0
        base = self.store.save(sample(), 'import')
        self.store.save(renamed(base, 'Испорчено'), 'edit')
        back = restore(self.store, 'proc-store-1', 1)
        self.assertEqual(back.name, 'Карта')
        self.assertEqual(back.revision, 3)
        self.assertEqual(self.store.get('proc-store-1').name, 'Карта')
        top = self.store.versions('proc-store-1')[0]
        self.assertEqual((top.version, top.source, top.note), (3, 'restore', 'из версии 1'))
        self.assertIsNone(restore(self.store, 'proc-store-1', 99))

    def test_delete_removes_the_map_and_its_history(self):
        self.store.save(sample(), 'import')
        self.assertTrue(self.store.delete('proc-store-1'))
        self.assertIsNone(self.store.get('proc-store-1'))
        self.assertEqual(self.store.versions('proc-store-1'), [])
        self.assertFalse(self.store.delete('proc-store-1'))

    def test_catalog_round_trip(self):
        items = [{'id': 'cat_1', 'kind': 'space', 'title': 'Банк', 'createdAt': '2026-10-04'},
                 {'id': 'cat_2', 'kind': 'map', 'title': 'Кредит', 'parentId': 'cat_1', 'createdAt': '2026-10-04'}]
        self.store.save_catalog(items)
        self.assertEqual(sorted(self.store.load_catalog(), key=lambda i: i['id']), items)
        self.store.save_catalog([])
        self.assertEqual(self.store.load_catalog(), [])

    def test_catalog_items_are_written_one_by_one(self):
        a = {'id': 'cat_a', 'kind': 'space', 'title': 'A', 'createdAt': '2026-10-04'}
        b = {'id': 'cat_b', 'kind': 'space', 'title': 'B', 'createdAt': '2026-10-04'}
        self.store.upsert_catalog_items([a])
        self.store.upsert_catalog_items([b])
        self.store.upsert_catalog_items([{**a, 'title': 'A2'}])
        got = {i['id']: i['title'] for i in self.store.load_catalog()}
        self.assertEqual(got, {'cat_a': 'A2', 'cat_b': 'B'})
        self.store.delete_catalog_item('cat_a')
        self.assertEqual([i['id'] for i in self.store.load_catalog()], ['cat_b'])

    def test_move_from_json_files_happens_once(self):
        folder = Path(tempfile.mkdtemp())
        good = sample('proc-json-1', 'Из файла').model_dump(mode='json')
        broken = {'id': 'proc-json-2', 'name': 'Битая'}  # нет обязательных полей
        (folder / 'process_store.json').write_text(
            json.dumps({'proc-json-1': good, 'proc-json-2': broken}, ensure_ascii=False), encoding='utf-8')
        (folder / 'catalog.json').write_text(json.dumps(
            [{'id': 'cat_1', 'kind': 'space', 'title': 'Банк', 'createdAt': '2026-10-04'}]), encoding='utf-8')
        files = (folder / 'process_store.json', folder / 'catalog.json')
        self.assertEqual(migrate_from_files(self.store, *files), (1, 1, 1))
        moved = self.store.get('proc-json-1')
        self.assertEqual(moved.model_dump(exclude={'revision'}),
                         BusinessProcess.model_validate(good).model_dump(exclude={'revision'}))
        self.assertEqual(self.store.versions('proc-json-1')[0].source, 'migration')
        self.assertEqual(migrate_from_files(self.store, *files), (0, 0, 0))
        self.assertEqual(len(self.store.versions('proc-json-1')), 1)

    def test_counts_in_history(self):
        process = sample()
        process.nodes.append(ProcessNode(id='extra', name='Шаг', type='userTask', geometry=Geometry()))
        self.store.save(process, 'import')
        v = self.store.versions('proc-store-1')[0]
        self.assertEqual((v.nodeCount, v.edgeCount),
                         (len(process.nodes), len(process.edges)))
        self.assertEqual(v.stepCount, sum(1 for n in process.nodes
                                          if n.type in ('task', 'userTask', 'serviceTask', 'subProcess')))

    def test_users_and_sessions(self):
        user = self.store.create_user('ivan@sqb.uz', 'Иван Петров', 'scrypt$aa$bb')
        with self.assertRaises(UserExists):
            self.store.create_user('ivan@sqb.uz', 'Другой', 'scrypt$cc$dd')
        self.assertEqual(self.store.find_user('ivan@sqb.uz'), (user, 'scrypt$aa$bb'))
        self.assertIsNone(self.store.find_user('nobody@sqb.uz'))
        self.store.create_session('token-1', user.id)
        self.assertEqual(self.store.session_user('token-1'), user)
        self.assertIsNone(self.store.session_user('token-2'))
        self.store.delete_session('token-1')
        self.assertIsNone(self.store.session_user('token-1'))

    def test_session_expires_after_idle_time(self):
        user = self.store.create_user('ivan@sqb.uz', 'Иван Петров', 'scrypt$aa$bb')
        self.store.create_session('token-1', user.id)
        self.store.session_seconds = 0
        self.assertIsNone(self.store.session_user('token-1'))

    def test_platform_admin_grant_revocation_and_last_admin(self):
        main = self.store.create_user('main@sqb.uz', 'Главный', 'h')
        backup = self.store.create_user('backup@sqb.uz', 'Резервный', 'h2')
        self.store.create_session('admin-session', main.id)
        self.assertFalse(self.store.session_user('admin-session').isAdmin)
        with self.assertRaises(ValueError):
            self.store.set_platform_admin('unknown@sqb.uz', True, 'Проверка')
        with self.assertRaises(ValueError):
            self.store.set_platform_admin(main.email, True, ' ')
        self.store.set_platform_admin(main.email, True, 'Назначение владельцем платформы')
        self.store.set_platform_admin(main.email, True, 'Повторное назначение')
        self.assertTrue(self.store.session_user('admin-session').isAdmin)
        self.assertTrue(self.store.find_user(main.email)[0].isAdmin)
        self.assertTrue(self.store.rename_user(main.id, 'Новое имя').isAdmin)
        self.assertTrue(self.store.profile(main.id).user.isAdmin)
        self.assertFalse(self.store.find_user(backup.email)[0].isAdmin)
        with self.assertRaises(ValueError):
            self.store.set_platform_admin(main.email, False, 'Последний администратор')
        self.store.set_platform_admin(backup.email, True, 'Резерв')
        self.store.set_platform_admin(main.email, False, 'Отзыв')
        self.assertFalse(self.store.session_user('admin-session').isAdmin)
        self.assertEqual(self.store.find_user(main.email)[1], 'h')
        if hasattr(self.store, '_pool'):
            with self.store._pool.connection() as conn:
                self.assertEqual(conn.execute('select count(*) from platform_access_audit').fetchone()[0], 3)
            import psycopg
            with self.assertRaises(psycopg.Error):
                with self.store._pool.connection() as conn:
                    conn.execute("update platform_access_audit set reason='подмена'")
        else:
            self.assertEqual(len(self.store._access_audit), 3)

    def test_author_of_versions(self):
        self.store.squash_seconds = 600
        ivan = self.store.create_user('ivan@sqb.uz', 'Иван Петров', 'h')
        olga = self.store.create_user('olga@sqb.uz', 'Ольга Ким', 'h')
        base = self.store.save(sample(), 'import', author=ivan.id)
        self.store.save(renamed(base, 'Ольга 1'), 'edit', author=olga.id)
        self.store.save(renamed(base, 'Ольга 2'), 'edit', author=olga.id)
        # Правка другого сотрудника в течение тех же 10 минут — уже его версия.
        self.store.save(renamed(base, 'Иван 1'), 'edit', author=ivan.id)
        # Пересчёт аналитики версии не создаёт и автора не меняет.
        self.store.save(renamed(base, 'Иван 1').model_copy(update={'fileName': 'x'}), 'analytics', author=olga.id)
        restore(self.store, 'proc-store-1', 1, author=olga.id)
        got = [(v.version, v.source, v.author, v.authorEmail) for v in self.store.versions('proc-store-1')]
        self.assertEqual(got, [
            (4, 'restore', 'Ольга Ким', 'olga@sqb.uz'),
            (3, 'edit', 'Иван Петров', 'ivan@sqb.uz'),
            (2, 'edit', 'Ольга Ким', 'olga@sqb.uz'),
            (1, 'import', 'Иван Петров', 'ivan@sqb.uz'),
        ])
        self.assertEqual(self.store.get_version('proc-store-1', 2).name, 'Ольга 2')

    def test_profile_counts_only_own_versions(self):
        self.store.squash_seconds = 0
        ivan = self.store.create_user('ivan@sqb.uz', 'Иван Петров', 'h')
        olga = self.store.create_user('olga@sqb.uz', 'Ольга Ким', 'h')
        empty = self.store.profile(ivan.id)
        self.assertEqual((empty.user, empty.versions, empty.bySource, empty.maps, empty.lastSavedAt, empty.recentMaps),
                         (ivan, 0, {}, 0, None, []))
        a = self.store.save(sample('a', 'Карта А'), 'import', author=ivan.id)
        self.store.save(renamed(a, 'Карта А2'), 'edit', author=ivan.id)
        self.store.save(sample('b', 'Карта Б'), 'import', author=olga.id)
        b = self.store.get('b')
        self.store.save(renamed(b, 'Карта Б2'), 'drawio', author=ivan.id)
        self.store.save(renamed(b, 'Карта Б3'), 'edit', author=olga.id)
        got = self.store.profile(ivan.id)
        self.assertEqual((got.versions, got.bySource, got.maps), (3, {'import': 1, 'edit': 1, 'drawio': 1}, 2))
        # Свежая — Б (его правка в draw.io), имя — текущее имя карты.
        self.assertEqual([(m.id, m.name, m.versions) for m in got.recentMaps], [('b', 'Карта Б3', 1), ('a', 'Карта А2', 2)])
        self.assertEqual(got.lastSavedAt, got.recentMaps[0].lastSavedAt)
        self.assertLessEqual(got.createdAt, got.lastSavedAt)
        self.assertEqual(self.store.profile(olga.id).versions, 2)
        self.assertIsNone(self.store.profile(999))

    def test_rename_and_password_change(self):
        ivan = self.store.create_user('ivan@sqb.uz', 'Иван Петров', 'old-hash')
        olga = self.store.create_user('olga@sqb.uz', 'Ольга Ким', 'h')
        self.store.save(sample(), 'import', author=ivan.id)
        for token, user in (('here', ivan), ('there', ivan), ('olga', olga)):
            self.store.create_session(token, user.id)
        renamed_ivan = self.store.rename_user(ivan.id, 'Иван Сидоров')
        self.assertEqual(renamed_ivan, ivan.model_copy(update={'name': 'Иван Сидоров'}))
        self.assertEqual(self.store.versions('proc-store-1')[0].author, 'Иван Сидоров')
        self.store.set_password(ivan.id, 'new-hash', keep_session='here')
        self.assertEqual(self.store.find_user('ivan@sqb.uz')[1], 'new-hash')
        self.assertEqual(self.store.session_user('here'), renamed_ivan)
        self.assertIsNone(self.store.session_user('there'))
        self.assertEqual(self.store.session_user('olga'), olga)

    def test_versions_saved_before_sign_in_have_no_author(self):
        self.store.save(sample(), 'migration')
        v = self.store.versions('proc-store-1')[0]
        self.assertEqual((v.author, v.authorEmail), (None, None))


class MemoryStoreTest(StoreContract, unittest.TestCase):
    def make_store(self):
        return MemoryStore()


@unittest.skipUnless(TEST_DATABASE_URL, 'нужна SQB_TEST_DATABASE_URL (docker compose up -d db)')
class PostgresStoreTest(StoreContract, unittest.TestCase):
    def make_store(self):
        import psycopg

        from app.services.store import PostgresStore

        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute(f'drop schema if exists {SCHEMA} cascade')
            conn.execute(f'create schema {SCHEMA}')
        separator = '&' if '?' in TEST_DATABASE_URL else '?'
        store = PostgresStore(f'{TEST_DATABASE_URL}{separator}options=-csearch_path%3D{SCHEMA}')
        self.addCleanup(store.close)
        return store


if __name__ == '__main__':
    unittest.main()
