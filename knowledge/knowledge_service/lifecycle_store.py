"""Reversible instruction deletion, preserving original bytes and audit history."""
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .permissions import effective


def deleted_instructions(db, actor, department_id):
    with db.pool.connection() as conn:
        db.authorize(conn, actor, department_id)
        if not set(effective(conn, actor, department_id)) & {'instructions.delete','instructions.restore'}:
            raise HTTPException(403, 'Нет права на просмотр корзины инструкций')
        return conn.execute('''SELECT i.*,r.name AS role_name,
            (SELECT count(*) FROM instruction_versions v WHERE v.instruction_id=i.id) AS version_count
            FROM instructions i LEFT JOIN roles r ON r.id=i.role_id
            WHERE i.department_id=%s AND i.document_type='job_instruction' AND i.deleted_at IS NOT NULL
            ORDER BY i.deleted_at DESC,i.id''', (department_id,)).fetchall()


def change_state(db, actor, instruction_id, reason, *, restore=False):
    capability = 'instructions.restore' if restore else 'instructions.delete'
    with db.pool.connection() as conn:
        initial = conn.execute('SELECT department_id FROM instructions WHERE id=%s', (instruction_id,)).fetchone()
        if not initial:
            raise HTTPException(404, 'Инструкция недоступна')
        department_id = initial['department_id']
        db.authorize(conn, actor, department_id, capability=capability)
        if department_id is None:
            conn.execute('SELECT pg_advisory_xact_lock(73010708)')
        else:
            conn.execute('SELECT id FROM departments WHERE id=%s FOR NO KEY UPDATE', (department_id,))
        db.authorize(conn, actor, department_id, capability=capability)
        instruction = conn.execute('SELECT * FROM instructions WHERE id=%s FOR UPDATE', (instruction_id,)).fetchone()
        changed = (instruction['deleted_at'] is not None) if restore else (instruction['deleted_at'] is None)
        if changed:
            if restore:
                # Also cover duplicate legacy entries predating title checks.
                if conn.execute('''SELECT 1 FROM instructions WHERE department_id IS NOT DISTINCT FROM %s
                    AND library_scope=%s AND document_type=%s
                    AND role_id IS NOT DISTINCT FROM %s AND lower(title)=lower(%s)
                    AND id<>%s AND deleted_at IS NULL''',
                                (department_id,instruction['library_scope'],instruction['document_type'],
                                 instruction['role_id'],instruction['title'],instruction_id)).fetchone():
                    raise HTTPException(409, 'Активная инструкция с таким названием уже существует. Устраните дубликат перед восстановлением')
                state = conn.execute('''UPDATE instructions SET deleted_at=NULL,deleted_by=NULL WHERE id=%s
                    RETURNING id,deleted_at,deleted_by''', (instruction_id,)).fetchone()
                details = {'reason':reason,'previous_deleted_at':instruction['deleted_at'].isoformat(),
                           'previous_deleted_by':instruction['deleted_by']}
            else:
                state = conn.execute('''UPDATE instructions SET deleted_at=clock_timestamp(),deleted_by=%s WHERE id=%s
                    RETURNING id,deleted_at,deleted_by''', (actor.id,instruction_id)).fetchone()
                # Fencing is permanent for this attempt, including after restore.
                cancelled = conn.execute('''UPDATE preparation_jobs SET status='cancelled',finished_at=clock_timestamp(),
                    lease_token=NULL,lease_expires_at=NULL,error_code='instruction_deleted',
                    message='Инструкция перемещена в корзину'
                    WHERE version_id IN (SELECT id FROM instruction_versions WHERE instruction_id=%s)
                    AND status IN ('queued','running') RETURNING id''', (instruction_id,)).fetchall()
                details = {'reason':reason,'cancelled_job_ids':[str(row['id']) for row in cancelled]}
            conn.execute('''INSERT INTO audit_events(actor_id,department_id,action,entity_id,details)
                VALUES (%s,%s,%s,%s,%s)''',
                         (actor.id,department_id,'instruction.restored' if restore else 'instruction.deleted',
                          instruction_id,Jsonb(details)))
            return state
        return {key:instruction[key] for key in ('id','deleted_at','deleted_by')}
