"""Atomic creation of a registry entry and its first immutable original."""
import hashlib
import json
from uuid import uuid4

from fastapi import HTTPException

from .store import VERSION_COLUMNS
from .preparation_store import enqueue_version


def _hash_request(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _receipt(conn, actor, upload_id, request_hash):
    if not upload_id:
        return None
    # The same receipt key can otherwise race across two departments.
    lock_key = int.from_bytes(hashlib.sha256(f'{actor.id}:{upload_id}'.encode()).digest()[:8],
                              byteorder='big', signed=True)
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key,))
    receipt = conn.execute('SELECT * FROM upload_receipts WHERE actor_id=%s AND upload_id=%s',
                           (actor.id, upload_id)).fetchone()
    if receipt and receipt['request_hash'] != request_hash:
        raise HTTPException(409, 'Запрос загрузки изменился. Отправьте изменённый документ как новую загрузку')
    return receipt


def _save_receipt(conn, actor, upload_id, request_hash, instruction_id, version_id):
    if upload_id:
        conn.execute('''INSERT INTO upload_receipts(actor_id,upload_id,request_hash,instruction_id,version_id)
                VALUES (%s,%s,%s,%s,%s)''',
                     (actor.id, upload_id, request_hash, instruction_id, version_id))


def create_instruction_with_version(db, actor, department_id, role_id, title,
                                    filename, media_type, content, content_hash,
                                    effective_from=None, effective_to=None, upload_id=None):
    request_hash = _hash_request({
        'operation': 'instruction.create',
        'department_id': str(department_id), 'role_id': str(role_id) if role_id else None, 'title': title,
        'filename': filename, 'content_hash': content_hash, 'media_type': media_type,
        'effective_from': str(effective_from) if effective_from else None,
        'effective_to': str(effective_to) if effective_to else None,
    })
    with db.pool.connection() as conn:
        # Serialize creates in this department, including a retry arriving while
        # the first request is still committing. No second empty entry is made.
        db.authorize(conn, actor, department_id, capability='instructions.create')
        receipt = _receipt(conn, actor, upload_id, request_hash)
        conn.execute('SELECT id FROM departments WHERE id=%s FOR NO KEY UPDATE', (department_id,))
        # The access manager takes this same department lock. A completed
        # revocation must be visible after waiting, including on a retry.
        db.authorize(conn, actor, department_id, capability='instructions.create')
        if receipt:
            instruction = conn.execute('''SELECT i.*,r.name AS role_name,
                        (SELECT count(*) FROM instruction_versions v WHERE v.instruction_id=i.id) AS version_count
                        FROM instructions i LEFT JOIN roles r ON r.id=i.role_id WHERE i.id=%s''',
                                       (receipt['instruction_id'],)).fetchone()
            if not instruction or instruction['deleted_at'] is not None:
                raise HTTPException(404, 'Инструкция недоступна')
            db.authorize(conn, actor, instruction['department_id'], capability='instructions.create')
            version = conn.execute(f'SELECT {VERSION_COLUMNS} FROM instruction_versions WHERE id=%s',
                                   (receipt['version_id'],)).fetchone()
            return {'instruction': instruction, 'version': version, 'replayed': True}
        role = conn.execute('SELECT name FROM roles WHERE id=%s AND department_id=%s',
                            (role_id, department_id)).fetchone() if role_id else None
        if role_id is not None and not role:
            raise HTTPException(422, 'Роль не принадлежит выбранному департаменту')
        duplicate = conn.execute('''SELECT deleted_at FROM instructions
                WHERE department_id=%s AND document_type='job_instruction'
                AND role_id IS NOT DISTINCT FROM %s AND lower(title)=lower(%s)''',
                        (department_id, role_id, title)).fetchone()
        if duplicate:
            if duplicate['deleted_at'] is not None:
                raise HTTPException(409, 'Инструкция с таким названием находится в корзине. Восстановите её или выберите другое название')
            scope = 'у этой должностной роли' if role_id else 'в этом департаменте без должностной роли'
            raise HTTPException(409, f'Инструкция с таким названием уже есть {scope}. Откройте её и добавьте новую версию')
        instruction = conn.execute('''INSERT INTO instructions(id,department_id,role_id,title,created_by)
                VALUES (%s,%s,%s,%s,%s) RETURNING *''',
                                   (uuid4(), department_id, role_id, title, actor.id)).fetchone()
        version = conn.execute(f'''INSERT INTO instruction_versions(
                id,instruction_id,number,filename,media_type,content,content_hash,byte_size,
                effective_from,effective_to,uploaded_by)
                VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {VERSION_COLUMNS}''',
                               (uuid4(), instruction['id'], filename, media_type, content,
                                content_hash, len(content), effective_from, effective_to, actor.id)).fetchone()
        db.audit(conn, actor.id, department_id, 'instruction.created', instruction['id'])
        db.audit(conn, actor.id, department_id, 'version.uploaded', version['id'])
        enqueue_version(conn, version['id'])
        _save_receipt(conn, actor, upload_id, request_hash, instruction['id'], version['id'])
        instruction['role_name'] = role['name'] if role else None
        instruction['version_count'] = 1
        return {'instruction': instruction, 'version': version, 'replayed': False}


def upload_instruction_version(db, actor, instruction_id, filename, media_type, content, content_hash,
                               effective_from=None, effective_to=None, upload_id=None):
    request_hash = _hash_request({
        'operation': 'version.create', 'instruction_id': str(instruction_id),
        'filename': filename, 'content_hash': content_hash, 'media_type': media_type,
        'effective_from': str(effective_from) if effective_from else None,
        'effective_to': str(effective_to) if effective_to else None,
    })
    with db.pool.connection() as conn:
        # Access is never cached in a receipt: revoked writers cannot replay.
        initial = db.instruction(conn, actor, instruction_id, capability='versions.create')
        receipt = _receipt(conn, actor, upload_id, request_hash)
        conn.execute('SELECT id FROM departments WHERE id=%s FOR NO KEY UPDATE', (initial['department_id'],))
        instruction = db.instruction(conn, actor, instruction_id, lock=True, capability='versions.create')
        if receipt:
            row = conn.execute(f'SELECT {VERSION_COLUMNS} FROM instruction_versions WHERE id=%s',
                               (receipt['version_id'],)).fetchone()
            return {**row, 'replayed': True}
        if conn.execute('SELECT 1 FROM instruction_versions WHERE instruction_id=%s AND content_hash=%s',
                        (instruction_id, content_hash)).fetchone():
            raise HTTPException(409, 'Этот файл уже есть в истории инструкции. Откройте список версий')
        number = conn.execute('SELECT coalesce(max(number),0)+1 AS number FROM instruction_versions WHERE instruction_id=%s',
                              (instruction_id,)).fetchone()['number']
        row = conn.execute(f'''INSERT INTO instruction_versions(
                id,instruction_id,number,filename,media_type,content,content_hash,byte_size,
                effective_from,effective_to,uploaded_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {VERSION_COLUMNS}''',
                           (uuid4(), instruction_id, number, filename, media_type, content,
                            content_hash, len(content), effective_from, effective_to, actor.id)).fetchone()
        db.audit(conn, actor.id, instruction['department_id'], 'version.uploaded', row['id'])
        enqueue_version(conn, row['id'])
        _save_receipt(conn, actor, upload_id, request_hash, instruction_id, row['id'])
        return {**row, 'replayed': False}
