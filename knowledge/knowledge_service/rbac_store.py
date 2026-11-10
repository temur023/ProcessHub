"""Named access roles and their scopes, separate from professional roles."""
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from .permissions import normalize


ROLE_COLUMNS = '''r.id,r.name,r.permission,r.capabilities,r.created_at,r.updated_at,
    coalesce((SELECT array_agg(rd.department_id ORDER BY rd.department_id)
              FROM role_departments rd WHERE rd.role_id=r.id),'{}'::uuid[]) AS department_ids,
    (SELECT count(*) FROM role_memberships m WHERE m.role_id=r.id) AS member_count'''


def _role(conn, role_id):
    return conn.execute(f'SELECT {ROLE_COLUMNS} FROM access_roles r WHERE r.id=%s', (role_id,)).fetchone()


def _lock_departments(conn, department_ids):
    # Uploads and direct grants take these same locks. Sorting also prevents
    # opposite-order deadlocks when a role covers several folders.
    ids = sorted(set(department_ids), key=str)
    # NO KEY UPDATE still serializes grants/uploads but permits foreign-key
    # KEY SHARE references from documents owned/applicable across folders.
    found = conn.execute('SELECT id FROM departments WHERE id=ANY(%s::uuid[]) ORDER BY id FOR NO KEY UPDATE', (ids,)).fetchall()
    if len(found) != len(ids):
        raise HTTPException(404, 'Одна из папок департаментов недоступна')
    return ids


def _event(conn, current, role_id, action, details, department_ids, user_id=None):
    conn.execute('''INSERT INTO access_role_audit_events(actor_id,role_id,user_id,action,details)
        VALUES (%s,%s,%s,%s,%s)''', (current.id,role_id,user_id,action,Jsonb(details)))
    # Keep each department's existing audit useful without duplicating events
    # in the global role journal. Empty scopes still have a global event.
    for department_id in sorted(set(department_ids), key=str):
        conn.execute('''INSERT INTO audit_events(actor_id,department_id,action,entity_id,details)
            VALUES (%s,%s,%s,%s,%s)''',
                     (current.id,department_id,action,role_id,Jsonb({**details,'user_id':user_id})))


def roles(db, current):
    db.require_admin(current)
    with db.pool.connection() as conn:
        return conn.execute(f'SELECT {ROLE_COLUMNS} FROM access_roles r ORDER BY r.name_key,r.id').fetchall()


def create_role(db, current, data):
    db.require_admin(current)
    with db.pool.connection() as conn:
        # Scope edits and member assignments are infrequent admin actions.
        # One short transaction lock gives them a consistent scope snapshot.
        conn.execute('SELECT pg_advisory_xact_lock(73010704)')
        department_ids = _lock_departments(conn, data.department_ids)
        name_key = data.name.casefold()
        if conn.execute('SELECT 1 FROM access_roles WHERE name_key=%s', (name_key,)).fetchone():
            raise HTTPException(409, 'Роль доступа с таким названием уже существует')
        role_id = uuid4()
        capabilities = normalize(data.capabilities, data.permission)
        conn.execute('INSERT INTO access_roles(id,name,name_key,permission,capabilities) VALUES (%s,%s,%s,%s,%s)',
                     (role_id,data.name,name_key,data.permission,capabilities))
        for department_id in department_ids:
            conn.execute('INSERT INTO role_departments(role_id,department_id) VALUES (%s,%s)', (role_id,department_id))
        snapshot = {'name':data.name,'permission':data.permission,'capabilities':capabilities,
                    'department_ids':[str(value) for value in department_ids],'reason':data.reason}
        _event(conn,current,role_id,'access_role.created',snapshot,department_ids)
        if department_ids:
            _event(conn,current,role_id,'access_role.scope.changed',
                   {**snapshot,'added':snapshot['department_ids'],'removed':[]},department_ids)
        return _role(conn, role_id)


def update_role(db, current, role_id, data):
    db.require_admin(current)
    with db.pool.connection() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(73010704)')
        old = _role(conn, role_id)
        if not old:
            raise HTTPException(404, 'Роль доступа недоступна')
        if old['updated_at'] != data.expected_updated_at:
            raise HTTPException(409, 'Роль доступа уже изменена другим администратором. Обновите список ролей и повторите изменения')
        department_ids = sorted(set(data.department_ids), key=str)
        affected = _lock_departments(conn, set(old['department_ids']) | set(department_ids))
        name_key = data.name.casefold()
        if conn.execute('SELECT 1 FROM access_roles WHERE name_key=%s AND id<>%s', (name_key,role_id)).fetchone():
            raise HTTPException(409, 'Роль доступа с таким названием уже существует')
        added = set(department_ids) - set(old['department_ids'])
        removed = set(old['department_ids']) - set(department_ids)
        # An old client renaming a role must not restore actions that another
        # administrator deliberately removed. Presets apply to creation only.
        capabilities = old['capabilities'] if data.capabilities is None else normalize(data.capabilities, data.permission)
        metadata_changed = (old['name'],old['permission'],old['capabilities']) != (data.name,data.permission,capabilities)
        snapshot = {'name':data.name,'permission':data.permission,'capabilities':capabilities,
                    'department_ids':[str(value) for value in department_ids],'reason':data.reason}
        if metadata_changed or added or removed:
            conn.execute("UPDATE access_roles SET name=%s,name_key=%s,permission=%s,capabilities=%s,updated_at=greatest(clock_timestamp(),updated_at+interval '1 microsecond') WHERE id=%s",
                         (data.name,name_key,data.permission,capabilities,role_id))
        if metadata_changed:
            _event(conn,current,role_id,'access_role.updated',
                   {**snapshot,'previous_name':old['name'],'previous_permission':old['permission'],
                    'previous_capabilities':old['capabilities']},affected)
        if added or removed:
            conn.execute('DELETE FROM role_departments WHERE role_id=%s AND department_id=ANY(%s::uuid[])',
                         (role_id,sorted(removed,key=str)))
            for department_id in sorted(added,key=str):
                conn.execute('INSERT INTO role_departments(role_id,department_id) VALUES (%s,%s)', (role_id,department_id))
            _event(conn,current,role_id,'access_role.scope.changed',
                   {**snapshot,'added':[str(v) for v in sorted(added,key=str)],
                    'removed':[str(v) for v in sorted(removed,key=str)]},affected)
        return _role(conn, role_id)


def memberships(db, current):
    db.require_admin(current)
    with db.pool.connection() as conn:
        return conn.execute('''SELECT m.role_id,m.user_id,r.name AS role_name,r.permission,r.capabilities,m.assigned_at,
            coalesce((SELECT array_agg(rd.department_id ORDER BY rd.department_id)
                      FROM role_departments rd WHERE rd.role_id=r.id),'{}'::uuid[]) AS department_ids
            FROM role_memberships m JOIN access_roles r ON r.id=m.role_id
            ORDER BY m.user_id,r.name_key,r.id''').fetchall()


def change_membership(db, current, data):
    db.require_admin(current)
    with db.pool.connection() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(73010704)')
        role = _role(conn, data.role_id)
        if not role:
            raise HTTPException(404, 'Роль доступа недоступна')
        department_ids = _lock_departments(conn, role['department_ids'])
        if data.enabled:
            changed = conn.execute('''INSERT INTO role_memberships(role_id,user_id) VALUES (%s,%s)
                ON CONFLICT DO NOTHING RETURNING user_id''', (data.role_id,data.user_id)).fetchone()
        else:
            changed = conn.execute('DELETE FROM role_memberships WHERE role_id=%s AND user_id=%s RETURNING user_id',
                                   (data.role_id,data.user_id)).fetchone()
        if changed:
            snapshot = {'name':role['name'],'permission':role['permission'],'capabilities':role['capabilities'],
                        'department_ids':[str(value) for value in department_ids],'reason':data.reason}
            _event(conn,current,data.role_id,
                   'access_role.member.assigned' if data.enabled else 'access_role.member.revoked',
                   snapshot,department_ids,data.user_id)
        return {'role_id':data.role_id,'user_id':data.user_id,'assigned':data.enabled,'changed':bool(changed),
                'capabilities':role['capabilities']}


def audit(db, current):
    db.require_admin(current)
    with db.pool.connection() as conn:
        return conn.execute('''SELECT e.id,e.actor_id,e.role_id,e.user_id,r.name AS role_name,
                e.action,e.details,e.created_at
            FROM access_role_audit_events e JOIN access_roles r ON r.id=e.role_id
            ORDER BY e.id DESC LIMIT 100''').fetchall()
