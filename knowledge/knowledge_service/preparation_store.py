"""Durable preparation jobs. Worker identity never inherits an uploader's ACL."""
import json
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb


JOB_COLUMNS = 'id,version_id,status,attempts,error_code,message,created_at,started_at,finished_at'
SOURCE_COLUMNS = 'id,version_id,parser_version,content_hash,status,blocks,warnings,stats,created_at'
MAX_RESULT_BYTES = 24 * 1024 * 1024


def parser_version():
    from .extraction import PARSER_VERSION
    return PARSER_VERSION


def enqueue_version(conn, version_id, version_parser=None):
    """Called within the original's transaction; no cached human identity."""
    version_parser = version_parser or parser_version()
    conn.execute('''INSERT INTO preparation_jobs(id,version_id,parser_version)
        SELECT %s,v.id,%s FROM instruction_versions v JOIN instructions i ON i.id=v.instruction_id
        WHERE v.id=%s AND i.deleted_at IS NULL
        ON CONFLICT(version_id,parser_version) DO NOTHING''',
                 (uuid4(), version_parser, version_id))
    return conn.execute(f'''SELECT {JOB_COLUMNS} FROM preparation_jobs
        WHERE version_id=%s AND parser_version=%s''', (version_id, version_parser)).fetchone()


def enqueue_missing(db):
    """Файлы инструкций, попавшие в базу не через загрузку (перенос дампом, импорт), без подготовки."""
    version_parser = parser_version()
    with db.pool.connection() as conn:
        return conn.execute('''INSERT INTO preparation_jobs(id,version_id,parser_version)
            SELECT DISTINCT ON (f.file_id) gen_random_uuid(),f.file_id,%s FROM document_edition_files f
            JOIN instructions i ON i.id=f.document_id
            WHERE i.document_type='job_instruction' AND i.deleted_at IS NULL AND f.purpose IN ('main','attachment')
            AND NOT EXISTS (SELECT 1 FROM preparation_jobs j WHERE j.version_id=f.file_id AND j.parser_version=%s)
            AND NOT EXISTS (SELECT 1 FROM prepared_sources s WHERE s.version_id=f.file_id AND s.parser_version=%s)
            ON CONFLICT(version_id,parser_version) DO NOTHING''',
                            (version_parser, version_parser, version_parser)).rowcount


def retry_failed(db):
    """Неудавшиеся подготовки — ещё раз при запуске обработчика (например, после обновления)."""
    version_parser = parser_version()
    with db.pool.connection() as conn:
        return conn.execute('''UPDATE preparation_jobs SET status='queued',attempts=0,error_code=NULL,message=NULL,
            finished_at=NULL,available_at=clock_timestamp() WHERE status='failed' AND parser_version=%s
            AND NOT EXISTS (SELECT 1 FROM prepared_sources s WHERE s.version_id=preparation_jobs.version_id
                AND s.parser_version=%s)''', (version_parser, version_parser)).rowcount


def _version(db, conn, actor, version_id, write=False, lock_department=False):
    row = conn.execute('''SELECT v.id,v.content_hash,i.department_id,i.deleted_at
        FROM instruction_versions v JOIN instructions i ON i.id=v.instruction_id WHERE v.id=%s''',
                       (version_id,)).fetchone()
    if not row or row['deleted_at'] is not None:
        raise HTTPException(404, 'Версия недоступна')
    capability = 'preparation.manage' if write else 'documents.read'
    db.authorize(conn, actor, row['department_id'], capability=capability)
    if lock_department:
        # Access mutations take this lock before changing any derived grant.
        if row['department_id'] is None:
            conn.execute('SELECT pg_advisory_xact_lock(73010708)')
        else:
            conn.execute('SELECT id FROM departments WHERE id=%s FOR NO KEY UPDATE', (row['department_id'],))
        db.authorize(conn, actor, row['department_id'], capability=capability)
        # Deletion shares the department lock; re-read its committed state.
        current = conn.execute('''SELECT i.deleted_at FROM instructions i
            JOIN instruction_versions v ON v.instruction_id=i.id WHERE v.id=%s''', (version_id,)).fetchone()
        if current['deleted_at'] is not None:
            raise HTTPException(404, 'Версия недоступна')
    return row


def _state(conn, version_id, version_parser):
    return {
        'job': conn.execute(f'''SELECT {JOB_COLUMNS} FROM preparation_jobs
            WHERE version_id=%s AND parser_version=%s''', (version_id, version_parser)).fetchone(),
        'source': conn.execute(f'''SELECT {SOURCE_COLUMNS} FROM prepared_sources
            WHERE version_id=%s AND parser_version=%s''', (version_id, version_parser)).fetchone(),
    }


def get_preparation(db, actor, version_id):
    with db.pool.connection() as conn:
        _version(db, conn, actor, version_id)
        return _state(conn, version_id, parser_version())


def request_preparation(db, actor, version_id):
    with db.pool.connection() as conn:
        version = _version(db, conn, actor, version_id, write=True, lock_department=True)
        version_parser = parser_version()
        existing = conn.execute('''SELECT * FROM preparation_jobs
            WHERE version_id=%s AND parser_version=%s FOR UPDATE''', (version_id, version_parser)).fetchone()
        changed = False
        previous_attempts = 0
        if existing is None:
            job = enqueue_version(conn, version_id, version_parser)
            action, changed = 'preparation.requested', True
        elif existing['status'] in ('failed', 'cancelled'):
            previous_attempts = existing['attempts']
            job = conn.execute(f'''UPDATE preparation_jobs SET status='queued',attempts=0,
                error_code=NULL,message=NULL,started_at=NULL,finished_at=NULL,
                available_at=clock_timestamp(),lease_token=NULL,lease_expires_at=NULL
                WHERE id=%s RETURNING {JOB_COLUMNS}''', (existing['id'],)).fetchone()
            action, changed = 'preparation.resumed', True
        else:
            job = existing
        if changed:
            conn.execute('''INSERT INTO audit_events(actor_id,department_id,action,entity_id,details)
                VALUES (%s,%s,%s,%s,%s)''',
                         (actor.id, version['department_id'], action, job['id'], Jsonb({
                             'version_id': str(version_id), 'parser_version': version_parser,
                             'previous_attempts': previous_attempts})))
        return _state(conn, version_id, version_parser)


def cancel_preparation(db, actor, version_id):
    with db.pool.connection() as conn:
        version = _version(db, conn, actor, version_id, write=True, lock_department=True)
        version_parser = parser_version()
        job = conn.execute('''SELECT * FROM preparation_jobs
            WHERE version_id=%s AND parser_version=%s FOR UPDATE''', (version_id, version_parser)).fetchone()
        if job and job['status'] in ('queued', 'running'):
            conn.execute('''UPDATE preparation_jobs SET status='cancelled',finished_at=clock_timestamp(),
                lease_token=NULL,lease_expires_at=NULL,error_code=NULL,message=NULL WHERE id=%s''', (job['id'],))
            conn.execute('''INSERT INTO audit_events(actor_id,department_id,action,entity_id,details)
                VALUES (%s,%s,'preparation.cancelled',%s,%s)''',
                         (actor.id, version['department_id'], job['id'], Jsonb({'version_id': str(version_id)})))
        return _state(conn, version_id, version_parser)


def claim_job(db, lease_seconds=90):
    """Bounded recovery and SKIP LOCKED acquisition, committed before parsing."""
    with db.pool.connection() as conn:
        expired = conn.execute('''SELECT * FROM preparation_jobs WHERE status='running'
            AND lease_expires_at <= clock_timestamp() ORDER BY lease_expires_at,id
            LIMIT 25 FOR UPDATE SKIP LOCKED''').fetchall()
        for old in expired:
            terminal = old['attempts'] >= old['max_attempts']
            conn.execute('''UPDATE preparation_jobs SET status=%s,error_code='lease_expired',
                message=%s,lease_token=NULL,lease_expires_at=NULL,
                finished_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END,
                available_at=clock_timestamp() + (%s * interval '1 second') WHERE id=%s''',
                         ('failed' if terminal else 'queued', 'Обработка прервана; требуется повтор' if terminal
                          else 'Обработка прервана; повтор запланирован', terminal,
                          min(60, 2 ** old['attempts']), old['id']))
        queued = conn.execute('''SELECT * FROM preparation_jobs WHERE status='queued'
            AND available_at <= clock_timestamp() AND attempts < max_attempts
            AND parser_version=%s
            AND EXISTS (SELECT 1 FROM instruction_versions v JOIN instructions i ON i.id=v.instruction_id
                WHERE v.id=preparation_jobs.version_id AND i.deleted_at IS NULL)
            ORDER BY available_at,created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED''', (parser_version(),)).fetchone()
        if not queued:
            return None
        job = conn.execute('''UPDATE preparation_jobs SET status='running',attempts=attempts+1,
            started_at=clock_timestamp(),finished_at=NULL,error_code=NULL,message=NULL,
            lease_token=%s,lease_expires_at=clock_timestamp() + (%s * interval '1 second')
            WHERE id=%s RETURNING *''', (uuid4(), lease_seconds, queued['id'])).fetchone()
        original = conn.execute('''SELECT content,media_type,content_hash FROM instruction_versions
            WHERE id=%s''', (job['version_id'],)).fetchone()
        return {**job, **original}


def heartbeat(db, job_id, lease_token, lease_seconds=90):
    with db.pool.connection() as conn:
        return bool(conn.execute('''UPDATE preparation_jobs
            SET lease_expires_at=clock_timestamp() + (%s * interval '1 second')
            WHERE id=%s AND status='running' AND lease_token=%s
            AND lease_expires_at > clock_timestamp() RETURNING id''',
                                 (lease_seconds, job_id, lease_token)).fetchone())


def validate_result(result, expected_parser):
    """A parser result is data, never arbitrary exception text or executable markup."""
    if not isinstance(result, dict) or len(json.dumps(result, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
        raise ValueError('invalid_result')
    if result.get('parser_version') != expected_parser or result.get('status') not in ('ready', 'partial', 'needs_ocr'):
        raise ValueError('invalid_result')
    blocks, warnings, stats = result.get('blocks'), result.get('warnings'), result.get('stats')
    if not isinstance(blocks, list) or not isinstance(warnings, list) or not isinstance(stats, dict):
        raise ValueError('invalid_result')
    if len(blocks) > 5000 or len(warnings) > 350:
        raise ValueError('invalid_result')
    ids, previous_order = set(), -1
    for block in blocks:
        if (not isinstance(block, dict) or not isinstance(block.get('id'), str) or not block['id']
                or block['id'] in ids or type(block.get('order')) is not int or block['order'] <= previous_order
                or block.get('kind') not in ('heading', 'paragraph', 'table_row')
                or not isinstance(block.get('text'), str) or not block['text'].strip()
                or not isinstance(block.get('locator'), dict)
                or not isinstance(block.get('label'), str)):
            raise ValueError('invalid_result')
        ids.add(block['id'])
        previous_order = block['order']
    for warning in warnings:
        if (not isinstance(warning, dict) or not isinstance(warning.get('code'), str)
                or not isinstance(warning.get('message'), str)
                or ('locator' in warning and not isinstance(warning['locator'], dict))):
            raise ValueError('invalid_result')
    for name in ('units_total', 'units_with_text', 'blocks_total', 'characters'):
        if type(stats.get(name)) is not int or stats[name] < 0:
            raise ValueError('invalid_result')
    if (stats['blocks_total'] != len(blocks) or stats['characters'] != sum(len(b['text']) for b in blocks)
            or stats['characters'] > 2_000_000
            or stats['units_with_text'] > stats['units_total']
            or (result['status'] == 'ready' and not blocks)):
        raise ValueError('invalid_result')
    return result


def complete_job(db, job_id, lease_token, result):
    with db.pool.connection() as conn:
        # Human lifecycle operations lock department -> instruction -> jobs.
        # Publishing locks instruction -> job and never takes a department
        # lock afterwards, so delete cannot race publication or deadlock it.
        instruction = conn.execute('''SELECT i.id,i.deleted_at FROM instructions i
            JOIN instruction_versions v ON v.instruction_id=i.id
            JOIN preparation_jobs j ON j.version_id=v.id WHERE j.id=%s FOR UPDATE OF i''',
                                   (job_id,)).fetchone()
        if not instruction or instruction['deleted_at'] is not None:
            return False
        job = conn.execute('''SELECT * FROM preparation_jobs WHERE id=%s AND status='running'
            AND lease_token=%s AND lease_expires_at > clock_timestamp() FOR UPDATE''',
                           (job_id, lease_token)).fetchone()
        if not job:
            return False
        validate_result(result, job['parser_version'])
        original = conn.execute('SELECT content_hash FROM instruction_versions WHERE id=%s',
                                (job['version_id'],)).fetchone()
        conn.execute('''INSERT INTO prepared_sources(id,version_id,parser_version,content_hash,status,blocks,warnings,stats)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                     (uuid4(), job['version_id'], job['parser_version'], original['content_hash'], result['status'],
                      Jsonb(result['blocks']), Jsonb(result['warnings']), Jsonb(result['stats'])))
        conn.execute('''UPDATE preparation_jobs SET status='completed',finished_at=clock_timestamp(),
            error_code=NULL,message=NULL,lease_token=NULL,lease_expires_at=NULL WHERE id=%s''', (job_id,))
        return True


def fail_job(db, job_id, lease_token, code, message, retryable=False):
    with db.pool.connection() as conn:
        job = conn.execute('''SELECT * FROM preparation_jobs WHERE id=%s AND status='running'
            AND lease_token=%s AND lease_expires_at > clock_timestamp() FOR UPDATE''',
                           (job_id, lease_token)).fetchone()
        if not job:
            return False
        retry = retryable and job['attempts'] < job['max_attempts']
        conn.execute('''UPDATE preparation_jobs SET status=%s,error_code=%s,message=%s,
            finished_at=CASE WHEN %s THEN NULL ELSE clock_timestamp() END,
            available_at=clock_timestamp() + (%s * interval '1 second'),
            lease_token=NULL,lease_expires_at=NULL WHERE id=%s''',
                     ('queued' if retry else 'failed', code, message, retry,
                      min(60, 2 ** job['attempts']), job_id))
        return True
