"""Durable analysis runs: request, queue, progress, result and expert review.

A run is bound to one confirmed, currently valid instruction edition and its
main file. The worker owns a run through a lease; anything finished is
immutable (database trigger), so a report always shows what was analysed.
"""
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .analysis_engine import STAGES
from .analysis_prompts import METHOD_VERSION
from .document_store import _audit, _document, _lock_scope
from .permissions import effective

ACTIVE = ['queued', 'running']
FINDING_STATUSES = ('partial', 'conflict', 'not_found', 'unresolved', 'missing')
SUMMARY_COLUMNS = '''r.id,r.kind,r.document_id,r.edition_id,r.file_id,r.department_id,r.status,r.stage,r.progress,
    r.method_version,r.collection,r.error_code,r.error_message,r.requested_by,r.created_at,r.started_at,r.finished_at,
    e.number AS edition_number,e.label AS edition_label'''
ELIGIBILITY_MESSAGES = {
    'missing_edition': 'У инструкции нет загруженной версии',
    'unverified': 'Сначала подтвердите актуальность версии: дату и период действия',
    'not_current': 'Анализ выполняется только по действующей подтверждённой версии',
}


EDITION_SQL = '''SELECT e.id AS edition_id,e.document_id,e.number AS edition_number,
    e.label AS edition_label,v.approved_on,v.valid_from,coalesce(ending.valid_to,v.valid_to) AS valid_to,
    CASE WHEN v.edition_id IS NULL THEN 'unverified' ELSE 'verified' END AS verification_status,
    coalesce(v.valid_from<=CURRENT_DATE AND (coalesce(ending.valid_to,v.valid_to) IS NULL
        OR coalesce(ending.valid_to,v.valid_to)>=CURRENT_DATE),false) AS is_current
    FROM document_editions e LEFT JOIN document_edition_verifications v ON v.edition_id=e.id
    LEFT JOIN document_edition_endings ending ON ending.edition_id=e.id'''


def _source(db, conn, actor, document_id, *, lock=False, capability='documents.read'):
    document = _document(db, conn, actor, document_id, capability, lock=lock)
    if document['document_type'] != 'job_instruction' or document['library_scope'] != 'department':
        raise HTTPException(422, 'Анализ выполняется для должностных инструкций департамента')
    return document


def _editions(conn, document_id):
    """Current confirmed edition first, then the newest."""
    return conn.execute(EDITION_SQL + ' WHERE e.document_id=%s ORDER BY is_current DESC,e.number DESC',
                        (document_id,)).fetchall()


def _eligibility(edition):
    if not edition:
        return 'missing_edition'
    if edition['verification_status'] != 'verified':
        return 'unverified'
    return 'ready' if edition['is_current'] else 'not_current'


def _latest_analysis(conn, document_id, edition_id):
    """Latest automatic analysis of this exact edition, with expert progress."""
    run = conn.execute('''SELECT r.id,r.status,r.created_at,r.finished_at,r.error_message,r.result->'counts' AS counts,
        (SELECT count(DISTINCT finding_id) FROM analysis_finding_reviews f WHERE f.run_id=r.id) AS reviewed
        FROM analysis_runs r WHERE r.document_id=%s AND r.edition_id=%s ORDER BY r.created_at DESC,r.id LIMIT 1''',
        (document_id, edition_id)).fetchone()
    if run and run['counts']:
        run['findings'] = sum(run['counts'].get(key, 0) for key in FINDING_STATUSES)
    return run


def department_registry(db, actor, department_id):
    """Every active instruction of the department: version readiness and its analysis."""
    with db.pool.connection() as conn:
        db.authorize(conn, actor, department_id)
        capabilities = effective(conn, actor, department_id)
        documents = conn.execute("""SELECT i.*,r.name AS role_name,r.unit_name AS role_unit FROM instructions i
            LEFT JOIN LATERAL (SELECT coalesce(display_name,name) AS name,unit_name FROM roles WHERE id=i.role_id) r ON true
            WHERE i.department_id=%s AND i.library_scope='department' AND i.document_type='job_instruction'
            AND i.deleted_at IS NULL ORDER BY i.title,i.id""", (department_id,)).fetchall()
        items = []
        for document in documents:
            edition = next(iter(_editions(conn, document['id'])), None)
            item = {key: document[key] for key in ('title', 'department_id', 'role_id', 'role_name', 'role_unit')}
            item.update({key: edition[key] if edition else None for key in
                         ('edition_id', 'edition_number', 'edition_label', 'approved_on', 'valid_from', 'valid_to',
                          'verification_status')})
            item.update(document_id=document['id'], is_current=bool(edition and edition['is_current']),
                        eligibility=_eligibility(edition), capabilities=capabilities,
                        analysis=_latest_analysis(conn, document['id'], edition['edition_id']) if edition else None)
            items.append(item)
        return {'items': items}


def _summary(row):
    run = dict(row)
    run['counts'] = row.get('counts')
    run['stages'] = [{'id': stage, 'label': label} for stage, label in STAGES]
    return run


def nova_status(settings):
    return {'configured': settings.nova_configured, 'collection': settings.nova_collection or None}


def start_run(db, settings, actor, document_id, edition_id):
    if not settings.nova_configured:
        raise HTTPException(503, 'Nova не подключена: администратор должен указать адрес, ключ и базу знаний')
    with db.pool.connection() as conn:
        initial = _source(db, conn, actor, document_id, capability='comparisons.manage')
        _lock_scope(db, conn, actor, initial['department_id'], 'comparisons.manage')
        document = _source(db, conn, actor, document_id, lock=True, capability='comparisons.manage')
        edition = next((item for item in _editions(conn, document_id) if item['edition_id'] == edition_id), None)
        eligibility = _eligibility(edition)
        if eligibility != 'ready':
            raise HTTPException(409, ELIGIBILITY_MESSAGES[eligibility])
        active = conn.execute('SELECT id FROM analysis_runs WHERE document_id=%s AND status=ANY(%s)',
                              (document_id, ACTIVE)).fetchone()
        if active:
            return get_run(db, actor, active['id'], conn=conn)
        main = conn.execute('''SELECT file_id FROM document_edition_files WHERE edition_id=%s AND purpose='main'
            ORDER BY position LIMIT 1''', (edition_id,)).fetchone()
        if not main:
            raise HTTPException(409, 'В версии нет основного файла')
        run_id = uuid4()
        conn.execute('''INSERT INTO analysis_runs(id,kind,document_id,edition_id,file_id,department_id,
            method_version,collection,requested_by,progress) VALUES(%s,'instruction_regulation',%s,%s,%s,%s,%s,%s,%s,%s)''',
            (run_id, document_id, edition_id, main['file_id'], document['department_id'], METHOD_VERSION,
             settings.nova_collection, actor.id, Jsonb({})))
        _audit(conn, actor, document, 'analysis.requested', run_id,
               {'edition_id': str(edition_id), 'method_version': METHOD_VERSION})
        return get_run(db, actor, run_id, conn=conn)


def _run_row(db, conn, actor, run_id):
    row = conn.execute('SELECT document_id FROM analysis_runs WHERE id=%s', (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Анализ не найден')
    _document(db, conn, actor, row['document_id'])
    return conn.execute(f'''SELECT {SUMMARY_COLUMNS},r.result FROM analysis_runs r
        JOIN document_editions e ON e.id=r.edition_id WHERE r.id=%s''', (run_id,)).fetchone()


def _reviews(conn, run_id):
    rows = conn.execute('''SELECT DISTINCT ON (finding_id) finding_id,decision,comment,reviewed_by,reviewed_at
        FROM analysis_finding_reviews WHERE run_id=%s ORDER BY finding_id,id DESC''', (run_id,)).fetchall()
    return {row['finding_id']: row for row in rows}


def get_run(db, actor, run_id, conn=None):
    if conn is None:
        with db.pool.connection() as conn:
            return get_run(db, actor, run_id, conn)
    row = _run_row(db, conn, actor, run_id)
    run = _summary(row)
    run['reviews'] = _reviews(conn, run_id) if row['status'] == 'completed' else {}
    run['capabilities'] = effective(conn, actor, row['department_id'])
    return run


def list_runs(db, actor, document_id):
    with db.pool.connection() as conn:
        _document(db, conn, actor, document_id)
        rows = conn.execute(f'''SELECT {SUMMARY_COLUMNS},r.result->'counts' AS counts FROM analysis_runs r
            JOIN document_editions e ON e.id=r.edition_id WHERE r.document_id=%s
            ORDER BY r.created_at DESC,r.id LIMIT 50''', (document_id,)).fetchall()
        department = conn.execute('SELECT department_id FROM instructions WHERE id=%s', (document_id,)).fetchone()
        return {'items': [_summary(row) for row in rows],
                'capabilities': effective(conn, actor, department['department_id'])}


def cancel_run(db, actor, run_id):
    with db.pool.connection() as conn:
        row = _run_row(db, conn, actor, run_id)
        document = _document(db, conn, actor, row['document_id'], 'comparisons.manage')
        updated = conn.execute('''UPDATE analysis_runs SET status='cancelled',finished_at=now(),lease_token=NULL,
            lease_expires_at=NULL WHERE id=%s AND status=ANY(%s) RETURNING id''', (run_id, ACTIVE)).fetchone()
        if updated:
            _audit(conn, actor, document, 'analysis.cancelled', run_id, {})
        return get_run(db, actor, run_id, conn)


def review_finding(db, actor, run_id, finding_id, decision, comment):
    with db.pool.connection() as conn:
        row = _run_row(db, conn, actor, run_id)
        document = _document(db, conn, actor, row['document_id'], 'comparisons.manage')
        if row['status'] != 'completed':
            raise HTTPException(409, 'Решение по замечанию принимается после завершения анализа')
        findings = {item['id'] for item in row['result'].get('functions', [])} | {
            item['id'] for item in row['result'].get('missing', [])}
        if finding_id not in findings:
            raise HTTPException(404, 'Замечание не найдено в этом анализе')
        conn.execute('''INSERT INTO analysis_finding_reviews(run_id,finding_id,decision,comment,reviewed_by)
            VALUES(%s,%s,%s,%s,%s)''', (run_id, finding_id, decision, comment, actor.id))
        _audit(conn, actor, document, 'analysis.finding.reviewed', run_id,
               {'finding_id': finding_id, 'decision': decision})
        return _reviews(conn, run_id)[finding_id]


def bind_regulation(db, actor, department_id, document_name):
    db.require_admin(actor)
    with db.pool.connection() as conn:
        row = conn.execute('''UPDATE departments SET nova_regulation_document=%s WHERE id=%s
            RETURNING id,nova_regulation_document''', (document_name, department_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Департамент не найден')
        conn.execute('''INSERT INTO audit_events(actor_id,department_id,action,entity_id,details)
            VALUES(%s,%s,'department.nova_regulation.bound',%s,%s)''',
            (actor.id, department_id, department_id, Jsonb({'document': document_name})))
        return row


# ── Worker side ──────────────────────────────────────────────────────────────

def claim_run(db, lease_seconds):
    """Next queued run, or a running one whose worker stopped renewing it."""
    with db.pool.connection() as conn:
        row = conn.execute('''SELECT id FROM analysis_runs WHERE status='queued'
            OR (status='running' AND lease_expires_at<now()) ORDER BY created_at,id
            LIMIT 1 FOR UPDATE SKIP LOCKED''').fetchone()
        if not row:
            return None
        if conn.execute('''UPDATE analysis_runs SET status='failed',error_code='abandoned',finished_at=now(),
                error_message='Анализ прерывался несколько раз и остановлен. Запустите его заново',
                lease_token=NULL,lease_expires_at=NULL WHERE id=%s AND attempts>=3 RETURNING id''',
                        (row['id'],)).fetchone():
            return None
        token = uuid4()
        return conn.execute('''UPDATE analysis_runs SET status='running',attempts=attempts+1,lease_token=%s,
            lease_expires_at=now()+make_interval(secs=>%s),started_at=coalesce(started_at,now()),
            stage=coalesce(stage,'instruction') WHERE id=%s RETURNING *''', (token, lease_seconds, row['id'])).fetchone()


def run_inputs(db, run):
    """Everything the engine needs, read under the run's lease."""
    with db.pool.connection() as conn:
        document = conn.execute('''SELECT i.title,i.role_id,i.deleted_at,d.name AS department_name,
            d.nova_regulation_document FROM instructions i JOIN departments d ON d.id=i.department_id
            WHERE i.id=%s''', (run['document_id'],)).fetchone()
        role = conn.execute('SELECT coalesce(display_name,name) AS name FROM roles WHERE id=%s',
                            (document['role_id'],)).fetchone() if document['role_id'] else None
        file = conn.execute('''SELECT v.id,v.content,v.media_type,v.filename,v.content_hash FROM instruction_versions v
            WHERE v.id=%s''', (run['file_id'],)).fetchone()
        from .preparation_store import SOURCE_COLUMNS, parser_version
        source = conn.execute(f'''SELECT {SOURCE_COLUMNS} FROM prepared_sources WHERE version_id=%s AND parser_version=%s''',
                              (run['file_id'], parser_version())).fetchone()
    return document, role, file, source


def renew(db, run, stage, done, total):
    """Store progress and extend the lease; False once the run is no longer ours."""
    with db.pool.connection() as conn:
        row = conn.execute('''UPDATE analysis_runs SET stage=%s,progress=progress||%s,
            lease_expires_at=now()+interval '15 minutes' WHERE id=%s AND lease_token=%s AND status='running'
            RETURNING id''', (stage, Jsonb({stage: {'done': done, 'total': total}}), run['id'],
                             run['lease_token'])).fetchone()
        return row is not None


def complete_run(db, run, result):
    with db.pool.connection() as conn:
        document = conn.execute('SELECT deleted_at FROM instructions WHERE id=%s', (run['document_id'],)).fetchone()
        if document['deleted_at'] is not None:
            return fail_run(db, run, 'document_archived', 'Документ перемещён в архив во время анализа', conn=conn)
        row = conn.execute('''UPDATE analysis_runs SET status='completed',stage='summary',result=%s,finished_at=now(),
            lease_token=NULL,lease_expires_at=NULL WHERE id=%s AND lease_token=%s AND status='running' RETURNING id''',
            (Jsonb(result), run['id'], run['lease_token'])).fetchone()
        return row is not None


def fail_run(db, run, code, message, conn=None):
    if conn is None:
        with db.pool.connection() as conn:
            return fail_run(db, run, code, message, conn)
    row = conn.execute('''UPDATE analysis_runs SET status='failed',error_code=%s,error_message=%s,finished_at=now(),
        lease_token=NULL,lease_expires_at=NULL WHERE id=%s AND lease_token=%s AND status='running' RETURNING id''',
        (code, message[:500], run['id'], run['lease_token'])).fetchone()
    return row is not None


def library_overview(db, actor):
    """Per readable department: where instructions wait for confirmation or review."""
    departments = [row['id'] for row in db.departments(actor)]
    with db.pool.connection() as conn:
        rows = conn.execute('''WITH docs AS (
                SELECT i.id,i.department_id FROM instructions i WHERE i.department_id=ANY(%s)
                AND i.deleted_at IS NULL AND i.library_scope='department' AND i.document_type='job_instruction'),
            state AS (
                SELECT d.department_id,
                    EXISTS(SELECT 1 FROM document_editions e JOIN document_edition_verifications v ON v.edition_id=e.id
                        LEFT JOIN document_edition_endings n ON n.edition_id=e.id WHERE e.document_id=d.id
                        AND v.valid_from<=CURRENT_DATE AND coalesce(n.valid_to,v.valid_to,CURRENT_DATE)>=CURRENT_DATE) AS confirmed,
                    EXISTS(SELECT 1 FROM document_editions e LEFT JOIN document_edition_verifications v ON v.edition_id=e.id
                        WHERE e.document_id=d.id AND v.edition_id IS NULL) AS awaiting,
                    latest.id AS run_id, latest.result->'counts' AS counts
                FROM docs d LEFT JOIN LATERAL (SELECT r.id,r.result FROM analysis_runs r WHERE r.document_id=d.id
                    AND r.status='completed' ORDER BY r.finished_at DESC LIMIT 1) latest ON true)
            SELECT s.department_id,count(*) AS instructions,count(*) FILTER (WHERE confirmed) AS confirmed,
                count(*) FILTER (WHERE awaiting) AS awaiting,count(run_id) AS analyzed,
                coalesce(sum(greatest(0,(SELECT coalesce(sum(value::int),0) FROM jsonb_each_text(s.counts)
                    WHERE key IN ('partial','conflict','not_found','unresolved','missing'))
                    - (SELECT count(DISTINCT finding_id) FROM analysis_finding_reviews f WHERE f.run_id=s.run_id))),0) AS pending
            FROM state s GROUP BY s.department_id''', (departments,)).fetchall()
    by_id = {row['department_id']: row for row in rows}
    empty = {'instructions': 0, 'confirmed': 0, 'awaiting': 0, 'analyzed': 0, 'pending': 0}
    return [{'department_id': department_id, **{key: int(by_id[department_id][key]) if department_id in by_id else value
                                                for key, value in empty.items()}} for department_id in departments]
