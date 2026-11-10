"""Отдельная PostgreSQL, транзакционная загрузка оригинала и истории.

Для первого пилота ограниченные оригиналы хранятся как bytea: файл,
версия и аудит фиксируются атомарно. Перед расширением хранения — объектное
хранилище банка с тем же контрактом неизменяемой версии (ADR-001).
"""
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .auth import Principal
from .permissions import CAPABILITIES, LEGACY_CAPABILITIES_SQL, effective, ordered

VERSION_COLUMNS = 'id, instruction_id, number, filename, media_type, content_hash, byte_size, effective_from, effective_to, uploaded_by, uploaded_at'


def migrate(url):
    with psycopg.connect(url) as conn:
        # Сериализуем запуск двух контейнеров, применяем миграцию целиком.
        conn.execute('SELECT pg_advisory_xact_lock(73010701)')
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())')
        for path in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
            if conn.execute('SELECT 1 FROM schema_migrations WHERE name=%s', (path.name,)).fetchone():
                continue
            conn.execute(path.read_text())
            conn.execute('INSERT INTO schema_migrations(name) VALUES (%s)', (path.name,))


# Validity periods are bank calendar days: CURRENT_DATE must be the date in
# Tashkent, not in UTC, or a version effective "today" is not current at night.
TIME_ZONE = os.environ.get('KNOWLEDGE_TIME_ZONE', 'Asia/Tashkent')


def _bank_time_zone(conn):
    conn.execute('SELECT set_config(%s,%s,false)', ('TimeZone', TIME_ZONE))
    conn.commit()


class Store:
    def __init__(self, url):
        self.pool = ConnectionPool(url, min_size=1, max_size=5, kwargs={'row_factory': dict_row, 'connect_timeout': 5},
                                   configure=_bank_time_zone, open=False)
        self.pool.open(wait=True, timeout=10)

    def close(self):
        self.pool.close()

    def departments(self, actor: Principal):
        with self.pool.connection() as conn:
            if actor.is_admin:
                rows = conn.execute("SELECT d.*, ARRAY['owner','reader','reviewer']::text[] AS permissions FROM departments d ORDER BY d.catalog_space_id NULLS LAST,d.catalog_position,d.name,d.id").fetchall()
                for row in rows:
                    row['capabilities'] = list(CAPABILITIES)
                return rows
            rows = conn.execute(f'''WITH effective_grants AS (
                    SELECT department_id,permission,{LEGACY_CAPABILITIES_SQL} AS capabilities
                    FROM access_grants WHERE user_id=%s
                    UNION ALL
                    SELECT rd.department_id,r.permission,r.capabilities FROM role_memberships m
                    JOIN access_roles r ON r.id=m.role_id JOIN role_departments rd ON rd.role_id=r.id
                    WHERE m.user_id=%s)
                SELECT d.*,array_agg(DISTINCT g.permission ORDER BY g.permission) AS permissions,
                    ARRAY(SELECT DISTINCT c.value FROM effective_grants eg
                        CROSS JOIN LATERAL unnest(eg.capabilities) AS c(value)
                        WHERE eg.department_id=d.id) AS capabilities
                FROM departments d JOIN effective_grants g ON g.department_id=d.id
                GROUP BY d.id ORDER BY d.catalog_space_id NULLS LAST,d.catalog_position,d.name,d.id''', (actor.id,actor.id)).fetchall()
            for row in rows:
                row['capabilities'] = ordered(row['capabilities'])
            return rows

    @staticmethod
    def authorize(conn, actor: Principal, department_id, write=False, capability=None):
        required = capability or ('instructions.create' if write else 'documents.read')
        if required not in CAPABILITIES:
            raise ValueError('Unknown document capability')
        if department_id is None:
            # Bank library access is deliberately explicit and admin-only.
            # Ownership and applicability never substitute for a grant.
            if not actor.is_admin:
                raise HTTPException(404, 'Документ или департамент недоступен')
            return
        if actor.is_admin:
            if not conn.execute('SELECT 1 FROM departments WHERE id=%s', (department_id,)).fetchone():
                raise HTTPException(404, 'Документ или департамент недоступен')
            return
        capabilities = effective(conn, actor, department_id)
        if 'documents.read' not in capabilities:
            raise HTTPException(404, 'Документ или департамент недоступен')
        if required not in capabilities:
            raise HTTPException(403, 'Нет права на это действие с документами')

    @staticmethod
    def require_admin(actor: Principal):
        if not actor.is_admin:
            raise HTTPException(403, 'Управлять департаментами и ролями может главный администратор')

    def import_departments(self, actor: Principal, data):
        """Односторонний перенос папок Hub; устойчивый ID сохраняет документы и доступ."""
        self.require_admin(actor)
        with self.pool.connection() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(73010703)')
            created = updated = unchanged = 0
            for item in data.departments:
                old = conn.execute('SELECT * FROM departments WHERE catalog_item_id=%s FOR NO KEY UPDATE', (item.catalog_item_id,)).fetchone()
                if old is None:
                    entity_id = uuid4()
                    conn.execute('''INSERT INTO departments(id,name,catalog_item_id,catalog_space_id,catalog_number,catalog_position)
                        VALUES (%s,%s,%s,%s,%s,%s)''', (entity_id, item.name, item.catalog_item_id, data.space_id, item.number, item.position))
                    self.audit(conn, actor.id, entity_id, 'department.catalog.imported', entity_id)
                    created += 1
                elif (old['name'], old['catalog_space_id'], old['catalog_number'], old['catalog_position']) != (item.name, data.space_id, item.number, item.position):
                    conn.execute('UPDATE departments SET name=%s,catalog_space_id=%s,catalog_number=%s,catalog_position=%s WHERE id=%s',
                                 (item.name, data.space_id, item.number, item.position, old['id']))
                    self.audit(conn, actor.id, old['id'], 'department.catalog.updated', old['id'])
                    updated += 1
                else:
                    unchanged += 1
            # Исчезнувшие в исходном каталоге папки не удаляем: здесь уже может быть история.
            return {'space_id': data.space_id, 'total': len(data.departments), 'created': created, 'updated': updated, 'unchanged': unchanged}

    def create_role(self, actor: Principal, department_id, name):
        self.require_admin(actor)
        with self.pool.connection() as conn:
            self.authorize(conn, actor, department_id)
            conn.execute('SELECT id FROM departments WHERE id=%s FOR NO KEY UPDATE', (department_id,))
            if conn.execute('SELECT 1 FROM roles WHERE department_id=%s AND lower(name)=lower(%s)', (department_id, name)).fetchone():
                raise HTTPException(409, 'Такая роль уже есть в департаменте')
            row = conn.execute('INSERT INTO roles(id,department_id,name) VALUES (%s,%s,%s) RETURNING *', (uuid4(), department_id, name)).fetchone()
            self.audit(conn, actor.id, department_id, 'role.created', row['id'])
            return row

    def roles(self, actor: Principal, department_id):
        with self.pool.connection() as conn:
            self.authorize(conn, actor, department_id)
            return conn.execute('SELECT * FROM roles WHERE department_id=%s ORDER BY name', (department_id,)).fetchall()

    def instructions(self, actor: Principal, department_id):
        with self.pool.connection() as conn:
            self.authorize(conn, actor, department_id)
            return conn.execute('''SELECT i.*, r.name AS role_name,
                (SELECT count(*) FROM instruction_versions v WHERE v.instruction_id=i.id) AS version_count
                FROM instructions i LEFT JOIN roles r ON r.id=i.role_id
                WHERE i.department_id=%s AND i.document_type='job_instruction'
                AND i.deleted_at IS NULL ORDER BY i.created_at DESC, i.id''', (department_id,)).fetchall()

    def create_instruction(self, actor: Principal, data):
        with self.pool.connection() as conn:
            self.authorize(conn, actor, data.department_id, write=True)
            conn.execute('SELECT id FROM departments WHERE id=%s FOR NO KEY UPDATE', (data.department_id,))
            self.authorize(conn, actor, data.department_id, write=True)
            if data.role_id is not None and not conn.execute('SELECT 1 FROM roles WHERE id=%s AND department_id=%s', (data.role_id, data.department_id)).fetchone():
                raise HTTPException(422, 'Роль не принадлежит выбранному департаменту')
            duplicate = conn.execute('''SELECT deleted_at FROM instructions WHERE department_id=%s
                AND document_type='job_instruction'
                AND role_id IS NOT DISTINCT FROM %s AND lower(title)=lower(%s)''',
                                    (data.department_id,data.role_id,data.title)).fetchone()
            if duplicate:
                if duplicate['deleted_at'] is not None:
                    raise HTTPException(409, 'Инструкция с таким названием находится в корзине. Восстановите её или выберите другое название')
                raise HTTPException(409, 'Инструкция с таким названием уже есть. Откройте её и добавьте новую версию')
            row = conn.execute('''INSERT INTO instructions(id, department_id, role_id, title, created_by)
                VALUES (%s,%s,%s,%s,%s) RETURNING *''', (uuid4(), data.department_id, data.role_id, data.title, actor.id)).fetchone()
            self.audit(conn, actor.id, row['department_id'], 'instruction.created', row['id'])
            return row

    @staticmethod
    def audit(conn, user_id, department_id, action, entity_id):
        conn.execute('INSERT INTO audit_events(actor_id, department_id, action, entity_id) VALUES (%s,%s,%s,%s)', (user_id, department_id, action, entity_id))

    def instruction(self, conn, actor: Principal, instruction_id, write=False, lock=False, capability=None):
        row = conn.execute('SELECT * FROM instructions WHERE id=%s' + (' FOR UPDATE' if lock else ''), (instruction_id,)).fetchone()
        if not row or row['deleted_at'] is not None:
            raise HTTPException(404, 'Инструкция недоступна')
        self.authorize(conn, actor, row['department_id'], write, capability=capability)
        return row

    def versions(self, actor: Principal, instruction_id):
        with self.pool.connection() as conn:
            self.instruction(conn, actor, instruction_id)
            return conn.execute(f'SELECT {VERSION_COLUMNS} FROM instruction_versions WHERE instruction_id=%s ORDER BY number DESC', (instruction_id,)).fetchall()

    def upload(self, actor: Principal, instruction_id, filename, media_type, content, content_hash, effective_from, effective_to):
        with self.pool.connection() as conn:
            # Блокировка родителя защищает номер и дубликат от двух загрузок.
            initial = self.instruction(conn, actor, instruction_id, capability='versions.create')
            conn.execute('SELECT id FROM departments WHERE id=%s FOR NO KEY UPDATE', (initial['department_id'],))
            instruction = self.instruction(conn, actor, instruction_id, lock=True, capability='versions.create')
            if conn.execute('SELECT 1 FROM instruction_versions WHERE instruction_id=%s AND content_hash=%s', (instruction_id, content_hash)).fetchone():
                raise HTTPException(409, 'Этот файл уже есть в истории инструкции')
            number = conn.execute('SELECT coalesce(max(number),0)+1 AS number FROM instruction_versions WHERE instruction_id=%s', (instruction_id,)).fetchone()['number']
            row = conn.execute(f'''INSERT INTO instruction_versions(id, instruction_id, number, filename, media_type, content, content_hash,
                byte_size, effective_from, effective_to, uploaded_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {VERSION_COLUMNS}''',
                (uuid4(), instruction_id, number, filename, media_type, content, content_hash, len(content), effective_from, effective_to, actor.id)).fetchone()
            self.audit(conn, actor.id, instruction['department_id'], 'version.uploaded', row['id'])
            from .preparation_store import enqueue_version
            enqueue_version(conn, row['id'])
            return row

    def original(self, actor: Principal, version_id):
        with self.pool.connection() as conn:
            row = conn.execute('SELECT v.filename, v.media_type, i.department_id FROM instruction_versions v JOIN instructions i ON i.id=v.instruction_id WHERE v.id=%s AND i.deleted_at IS NULL', (version_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Версия недоступна')
            self.authorize(conn, actor, row['department_id'], capability='documents.download')
            row['content'] = conn.execute('SELECT content FROM instruction_versions WHERE id=%s', (version_id,)).fetchone()['content']
            self.audit(conn, actor.id, row['department_id'], 'version.downloaded', version_id)
            return row
