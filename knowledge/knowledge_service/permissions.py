"""Explicit document actions. Legacy presets never include destructive rights."""
CAPABILITIES = (
    'documents.read', 'documents.download', 'instructions.create', 'versions.create',
    'preparation.manage', 'instructions.delete', 'instructions.restore', 'comparisons.manage',
    'editions.verify',
)
PRESETS = {'reader': CAPABILITIES[:2], 'reviewer': CAPABILITIES[:2], 'owner': CAPABILITIES[:5]}

# Used only for old direct grants; named roles always use their stored array.
LEGACY_CAPABILITIES_SQL = """CASE permission WHEN 'owner' THEN
    ARRAY['documents.read','documents.download','instructions.create','versions.create','preparation.manage']::text[]
    ELSE ARRAY['documents.read','documents.download']::text[] END"""


def ordered(values):
    selected = set(values)
    return [value for value in CAPABILITIES if value in selected]


def normalize(values, permission):
    if values is None:
        return list(PRESETS[permission])
    if not values or len(values) != len(set(values)) or any(value not in CAPABILITIES for value in values):
        raise ValueError('Выберите известные права без повторов')
    if 'documents.read' not in values:
        raise ValueError('Роль доступа должна включать чтение документов')
    return ordered(values)


def effective(conn, actor, department_id):
    if actor.is_admin:
        return list(CAPABILITIES)
    rows = conn.execute(f'''SELECT {LEGACY_CAPABILITIES_SQL} AS capabilities
        FROM access_grants WHERE user_id=%s AND department_id=%s
        UNION ALL SELECT r.capabilities FROM role_memberships m
        JOIN access_roles r ON r.id=m.role_id JOIN role_departments rd ON rd.role_id=r.id
        WHERE m.user_id=%s AND rd.department_id=%s''',
                        (actor.id,department_id,actor.id,department_id)).fetchall()
    return ordered(value for row in rows for value in row['capabilities'])
