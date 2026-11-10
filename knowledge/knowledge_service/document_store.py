"""Universal documents over the existing immutable original/job ledgers."""
import json
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .document_models import DocumentInput
from .permissions import effective
from .preparation_store import enqueue_version, parser_version
from .registry_store import _hash_request, _receipt, _save_receipt


def _audit(conn, actor, document, action, entity_id, details=None):
    conn.execute('''INSERT INTO audit_events(actor_id,department_id,action,entity_id,details)
        VALUES(%s,%s,%s,%s,%s)''',
                 (actor.id,document['department_id'],action,entity_id,Jsonb(details or {})))


def _lock_scope(db, conn, actor, department_id, capability):
    db.authorize(conn,actor,department_id,capability=capability)
    if department_id is None:
        conn.execute('SELECT pg_advisory_xact_lock(73010708)')
    else:
        # FK references to owners/applicability take KEY SHARE locks. A scope
        # lock must coexist with those cross-department references, while
        # still mutually excluding the access manager's coordination lock.
        conn.execute('SELECT id FROM departments WHERE id=%s FOR NO KEY UPDATE',(department_id,))
    db.authorize(conn,actor,department_id,capability=capability)


def _document(db,conn,actor,document_id,capability='documents.read',*,lock=False,deleted=False):
    row = conn.execute('SELECT * FROM instructions WHERE id=%s' + (' FOR UPDATE' if lock else ''),
                       (document_id,)).fetchone()
    if row is None or (row['deleted_at'] is not None and not deleted):
        raise HTTPException(404,'Документ недоступен')
    db.authorize(conn,actor,row['department_id'],capability=capability)
    return row


def _references(conn,data):
    ids = set(item.department_id for item in data.applicability)
    if data.owner_department_id:
        ids.add(data.owner_department_id)
    if ids and len(conn.execute('SELECT id FROM departments WHERE id=ANY(%s)',(list(ids),)).fetchall()) != len(ids):
        raise HTTPException(422,'Владелец или область применимости ссылается на неизвестный департамент')
    if data.role_id is not None and not conn.execute('SELECT 1 FROM roles WHERE id=%s AND department_id=%s',
                                                   (data.role_id,data.department_id)).fetchone():
        raise HTTPException(422,'Должность не принадлежит выбранному департаменту')


def _position(conn,actor,department_id,position):
    """Id of the department position, created on first use with the same name."""
    name=f'{position.display_name} — {position.unit_name}'
    row=conn.execute('SELECT id FROM roles WHERE department_id=%s AND lower(name)=lower(%s)',
                     (department_id,name)).fetchone()
    if row:
        return row['id']
    row=conn.execute('''INSERT INTO roles(id,department_id,name,display_name,unit_name,category,classification_evidence)
        VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id''',(uuid4(),department_id,name,position.display_name,
        position.unit_name,position.category,'Создана при загрузке или уточнении инструкции')).fetchone()
    conn.execute('''INSERT INTO audit_events(actor_id,department_id,action,entity_id,details)
        VALUES(%s,%s,'business_role.created',%s,%s)''',(actor.id,department_id,row['id'],Jsonb(position.model_dump())))
    return row['id']


def _with_position(conn,actor,data):
    if data.new_position is None:
        return data
    return data.model_copy(update={'role_id':_position(conn,actor,data.department_id,data.new_position),'new_position':None})


def _duplicate(conn,data,exclude=None):
    duplicate = conn.execute('''SELECT id,deleted_at FROM instructions
        WHERE library_scope=%s AND department_id IS NOT DISTINCT FROM %s
        AND document_type=%s AND role_id IS NOT DISTINCT FROM %s AND lower(title)=lower(%s)
        AND (%s::uuid IS NULL OR id<>%s::uuid) LIMIT 1''',
        (data.library_scope,data.department_id,data.document_type,data.role_id,data.title,exclude,exclude)).fetchone()
    if duplicate:
        message = ('Документ с таким названием находится в корзине. Восстановите его или измените название'
                   if duplicate['deleted_at'] else 'Документ уже есть. Откройте его и добавьте редакцию')
        raise HTTPException(409,message)


def _write_applicability(conn,document_id,items):
    conn.execute('DELETE FROM document_applicability WHERE document_id=%s',(document_id,))
    for item in items:
        conn.execute('''INSERT INTO document_applicability(document_id,department_id,status,evidence)
            VALUES(%s,%s,%s,%s)''',(document_id,item.department_id,item.status,item.evidence))


def _files(conn,edition_id,with_preparation=False):
    rows = conn.execute('''SELECT v.id,f.edition_id,v.filename,v.media_type,v.content_hash,v.byte_size,
        v.uploaded_by,v.uploaded_at,f.purpose,f.source_metadata
        FROM document_edition_files f JOIN instruction_versions v ON v.id=f.file_id
        WHERE f.edition_id=%s ORDER BY f.position''',(edition_id,)).fetchall()
    if with_preparation:
        version_parser=parser_version()
        for row in rows:
            row['preparation'] = {
                'job':conn.execute('''SELECT id,version_id,status,attempts,error_code,message,created_at,
                    started_at,finished_at FROM preparation_jobs WHERE version_id=%s AND parser_version=%s''',
                    (row['id'],version_parser)).fetchone(),
                'source':conn.execute('''SELECT id,version_id,parser_version,content_hash,status,warnings,stats,created_at
                    FROM prepared_sources WHERE version_id=%s AND parser_version=%s''',
                    (row['id'],version_parser)).fetchone(),
            }
    return rows


def _editions(conn,document_id,with_preparation=False):
    rows=conn.execute('''SELECT e.*,CASE WHEN v.edition_id IS NULL THEN 'unverified' ELSE 'verified' END
        AS verification_status,v.approved_on,v.valid_from,coalesce(ending.valid_to,v.valid_to) AS valid_to,
        v.valid_to AS originally_verified_to,v.evidence AS verification_evidence,v.verified_by,v.verified_at,
        ending.evidence AS end_validity_evidence,ending.ended_by,ending.ended_at,
        coalesce(v.valid_from<=CURRENT_DATE AND (coalesce(ending.valid_to,v.valid_to) IS NULL
            OR coalesce(ending.valid_to,v.valid_to)>=CURRENT_DATE),false) AS is_current
        FROM document_editions e LEFT JOIN document_edition_verifications v ON v.edition_id=e.id
        LEFT JOIN document_edition_endings ending ON ending.edition_id=e.id
        WHERE e.document_id=%s ORDER BY e.number DESC''',(document_id,)).fetchall()
    for edition in rows:
        edition['files']=_files(conn,edition['id'],with_preparation)
    return rows


def _dto(conn,actor,row,*,detail=False):
    role=conn.execute('SELECT name FROM roles WHERE id=%s',(row['role_id'],)).fetchone() if row['role_id'] else None
    owner=(conn.execute('SELECT name FROM departments WHERE id=%s',(row['owner_department_id'],)).fetchone()
           if row['owner_department_id'] else None)
    applicability=conn.execute('''SELECT a.department_id,d.name AS department_name,a.status,a.evidence
        FROM document_applicability a JOIN departments d ON d.id=a.department_id
        WHERE a.document_id=%s ORDER BY d.name,d.id''',(row['id'],)).fetchall()
    editions=_editions(conn,row['id'],detail)
    result={key:row[key] for key in ('id','title','document_type','library_scope','department_id',
        'owner_department_id','role_id','bank_number','document_date','metadata','created_at','deleted_at')}
    result.update(owner_department_name=owner['name'] if owner else None,role_name=role['name'] if role else None,
                  applicability=applicability,edition_count=len(editions),
                  current_edition=next((e for e in editions if e['is_current']),None),
                  capabilities=effective(conn,actor,row['department_id']))
    if detail:
        result['editions']=editions
    return result


def list_documents(db,actor,library_scope,department_id=None,document_type=None,view='all'):
    if library_scope=='department' and department_id is None:
        raise HTTPException(422,'Выберите департамент')
    if library_scope=='bank' and department_id is not None:
        raise HTTPException(422,'Общая библиотека не имеет папки департамента')
    with db.pool.connection() as conn:
        db.authorize(conn,actor,department_id)
        if view=='trash' and not set(effective(conn,actor,department_id)) & {'instructions.delete','instructions.restore'}:
            raise HTTPException(403,'Нет права на просмотр корзины')
        rows=conn.execute('''SELECT i.* FROM instructions i
            WHERE ((i.library_scope=%s AND i.department_id IS NOT DISTINCT FROM %s)
                OR (%s AND i.library_scope='bank' AND EXISTS(SELECT 1 FROM document_applicability a
                    WHERE a.document_id=i.id AND a.department_id=%s
                    AND (%s<>'current' OR a.status='confirmed'))))
            AND (%s::text IS NULL OR i.document_type=%s)
            AND ((%s='trash' AND i.deleted_at IS NOT NULL) OR (%s<>'trash' AND i.deleted_at IS NULL))
            ORDER BY i.created_at DESC,i.id''',
            (library_scope,department_id,actor.is_admin and library_scope=='department',department_id,view,
             document_type,document_type,view,view)).fetchall()
        documents=[]
        for row in rows:
            dto=_dto(conn,actor,row)
            if view=='analytics' and row['document_type']!='analytics':
                continue
            if view=='current' and (dto['current_edition'] is None or row['document_type'] in ('analytics','sop','other','correspondence')):
                continue
            if view=='unverified' and not conn.execute('''SELECT 1 FROM document_editions e
                LEFT JOIN document_edition_verifications v ON v.edition_id=e.id
                WHERE e.document_id=%s AND v.edition_id IS NULL''',(row['id'],)).fetchone():
                continue
            if view=='historical' and not conn.execute('''SELECT 1 FROM document_editions e
                JOIN document_edition_verifications v ON v.edition_id=e.id
                LEFT JOIN document_edition_endings ending ON ending.edition_id=e.id
                WHERE e.document_id=%s AND coalesce(ending.valid_to,v.valid_to)<CURRENT_DATE''',(row['id'],)).fetchone():
                continue
            documents.append(dto)
        return documents


def get_document(db,actor,document_id):
    with db.pool.connection() as conn:
        return _dto(conn,actor,_document(db,conn,actor,document_id),detail=True)


def _request_hash(operation,data,edition,originals,file_metadata,document_id=None):
    return _hash_request({'operation':operation,'document_id':str(document_id) if document_id else None,
        'document':data.model_dump(mode='json') if data else None,'edition':edition.model_dump(mode='json'),
        'files':[{key:file[key] for key in ('filename','media_type','content_hash')} for file in originals],
        'file_metadata':[item.model_dump(mode='json') for item in file_metadata]})


def _save_edition(conn,db,actor,document,edition,originals,file_metadata):
    number=conn.execute('SELECT coalesce(max(number),0)+1 AS number FROM document_editions WHERE document_id=%s',
                        (document['id'],)).fetchone()['number']
    edition_id=uuid4()
    conn.execute('''INSERT INTO document_editions(id,document_id,number,label,metadata,uploaded_by)
        VALUES(%s,%s,%s,%s,%s,%s)''',(edition_id,document['id'],number,edition.label,Jsonb(edition.metadata),actor.id))
    next_file_number=conn.execute('SELECT coalesce(max(number),0)+1 AS number FROM instruction_versions WHERE instruction_id=%s',
                                  (document['id'],)).fetchone()['number']
    first_file_id=None
    for position,(original,meta) in enumerate(zip(originals,file_metadata,strict=True)):
        file_id=uuid4()
        first_file_id=first_file_id or file_id
        # The FK is deferred so the legacy insert trigger can see that this
        # original already belongs to the complete edition being committed.
        conn.execute('''INSERT INTO document_edition_files(edition_id,document_id,file_id,position,purpose,source_metadata)
            VALUES(%s,%s,%s,%s,%s,%s)''',
            (edition_id,document['id'],file_id,position,meta.purpose,Jsonb(meta.source_metadata)))
        conn.execute('''INSERT INTO instruction_versions(id,instruction_id,number,filename,media_type,content,
            content_hash,byte_size,uploaded_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (file_id,document['id'],next_file_number+position,original['filename'],original['media_type'],
             original['content'],original['content_hash'],len(original['content']),actor.id))
        _audit(conn,actor,document,'document.file.uploaded',file_id,{'edition_id':str(edition_id),'purpose':meta.purpose})
        if document['document_type'] == 'job_instruction' and meta.purpose in ('main', 'attachment'):
            enqueue_version(conn,file_id)
    _audit(conn,actor,document,'document.edition.uploaded',edition_id,{'file_count':len(originals)})
    return edition_id,first_file_id


def _upload_response(conn,actor,document,edition_id,replayed):
    dto=_dto(conn,actor,document,detail=True)
    return {'document':dto,'edition':next(e for e in dto['editions'] if e['id']==edition_id),'replayed':replayed}


def create_document(db,actor,data,edition,originals,file_metadata,upload_id):
    request_hash=_request_hash('document.create',data,edition,originals,file_metadata)
    with db.pool.connection() as conn:
        db.authorize(conn,actor,data.department_id,capability='instructions.create')
        receipt=_receipt(conn,actor,upload_id,request_hash)
        _lock_scope(db,conn,actor,data.department_id,'instructions.create')
        if receipt:
            document=_document(db,conn,actor,receipt['instruction_id'],'instructions.create')
            linked=conn.execute('SELECT edition_id FROM document_edition_files WHERE file_id=%s',
                                (receipt['version_id'],)).fetchone()
            return _upload_response(conn,actor,document,linked['edition_id'],True)
        data=_with_position(conn,actor,data)
        _references(conn,data)
        _duplicate(conn,data)
        document=conn.execute('''INSERT INTO instructions(id,department_id,role_id,title,created_by,library_scope,
            document_type,owner_department_id,bank_number,document_date,metadata)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (uuid4(),data.department_id,data.role_id,data.title,actor.id,data.library_scope,data.document_type,
             data.owner_department_id,data.bank_number,data.document_date,Jsonb(data.metadata))).fetchone()
        _write_applicability(conn,document['id'],data.applicability)
        _audit(conn,actor,document,'document.created',document['id'],data.model_dump(mode='json'))
        edition_id,first_file_id=_save_edition(conn,db,actor,document,edition,originals,file_metadata)
        _save_receipt(conn,actor,upload_id,request_hash,document['id'],first_file_id)
        return _upload_response(conn,actor,document,edition_id,False)


def add_edition(db,actor,document_id,edition,originals,file_metadata,upload_id):
    request_hash=_request_hash('document.edition.create',None,edition,originals,file_metadata,document_id)
    with db.pool.connection() as conn:
        initial=_document(db,conn,actor,document_id,'versions.create')
        receipt=_receipt(conn,actor,upload_id,request_hash)
        _lock_scope(db,conn,actor,initial['department_id'],'versions.create')
        document=_document(db,conn,actor,document_id,'versions.create',lock=True)
        if receipt:
            linked=conn.execute('SELECT edition_id FROM document_edition_files WHERE file_id=%s',
                                (receipt['version_id'],)).fetchone()
            return _upload_response(conn,actor,document,linked['edition_id'],True)
        edition_id,first_file_id=_save_edition(conn,db,actor,document,edition,originals,file_metadata)
        _save_receipt(conn,actor,upload_id,request_hash,document_id,first_file_id)
        return _upload_response(conn,actor,document,edition_id,False)


def update_document(db,actor,document_id,patch):
    with db.pool.connection() as conn:
        initial=_document(db,conn,actor,document_id,'instructions.create')
        _lock_scope(db,conn,actor,initial['department_id'],'instructions.create')
        document=_document(db,conn,actor,document_id,'instructions.create',lock=True)
        current={key:document[key] for key in DocumentInput.model_fields if key not in ('applicability','new_position')}
        current['applicability']=conn.execute('SELECT department_id,status,evidence FROM document_applicability WHERE document_id=%s',
                                             (document_id,)).fetchall()
        try:
            merged={**current,**patch.model_dump(exclude_unset=True)}
            if merged.get('new_position'):
                merged['role_id']=None  # the new position replaces the current one
            data=DocumentInput.model_validate(merged)
        except ValueError as error:
            raise HTTPException(422,'Проверьте реквизиты, должность и применимость документа') from error
        data=_with_position(conn,actor,data)
        _references(conn,data)
        _duplicate(conn,data,document_id)
        row=conn.execute('''UPDATE instructions SET title=%s,document_type=%s,owner_department_id=%s,role_id=%s,
            bank_number=%s,document_date=%s,metadata=%s WHERE id=%s RETURNING *''',
            (data.title,data.document_type,data.owner_department_id,data.role_id,data.bank_number,
             data.document_date,Jsonb(data.metadata),document_id)).fetchone()
        _write_applicability(conn,document_id,data.applicability)
        _audit(conn,actor,row,'document.metadata.updated',document_id,
               {'before':json.loads(json.dumps(current,default=str)),'after':data.model_dump(mode='json')})
        return _dto(conn,actor,row,detail=True)


def _close_previous(conn,actor,document,edition_id,data):
    """End the open period that the new edition replaces; nothing else."""
    previous=conn.execute('''SELECT v.edition_id FROM document_edition_verifications v
        JOIN document_editions e ON e.id=v.edition_id
        LEFT JOIN document_edition_endings ending ON ending.edition_id=v.edition_id
        WHERE e.document_id=%s AND v.edition_id<>%s AND ending.edition_id IS NULL
        AND v.valid_from<%s AND (v.valid_to IS NULL OR v.valid_to>=%s)''',
        (document['id'],edition_id,data.valid_from,data.valid_from)).fetchone()
    if not previous:
        return
    valid_to=data.valid_from-timedelta(days=1)
    conn.execute('''INSERT INTO document_edition_endings(edition_id,valid_to,evidence,ended_by)
        VALUES(%s,%s,%s,%s)''',(previous['edition_id'],valid_to,data.evidence,actor.id))
    _audit(conn,actor,document,'document.edition.validity.ended',previous['edition_id'],
           {'valid_to':valid_to.isoformat(),'evidence':data.evidence,'superseded_by':str(edition_id)})


def verify_edition(db,actor,document_id,edition_id,data):
    # Capability to upload does not imply authority to confirm an edition.
    with db.pool.connection() as conn:
        initial=_document(db,conn,actor,document_id,'editions.verify')
        _lock_scope(db,conn,actor,initial['department_id'],'editions.verify')
        document=_document(db,conn,actor,document_id,'editions.verify',lock=True)
        if not conn.execute('SELECT 1 FROM document_editions WHERE id=%s AND document_id=%s',
                            (edition_id,document_id)).fetchone():
            raise HTTPException(404,'Редакция недоступна')
        existing=conn.execute('SELECT * FROM document_edition_verifications WHERE edition_id=%s',
                              (edition_id,)).fetchone()
        if existing:
            if (existing['valid_from'],existing['valid_to'],existing['approved_on'],existing['evidence'])==(
                    data.valid_from,data.valid_to,data.approved_on,data.evidence):
                return next(e for e in _editions(conn,document_id,True) if e['id']==edition_id)
            raise HTTPException(409,'Подтверждение редакции уже зафиксировано; существующее основание неизменяемо')
        if data.close_previous:
            _close_previous(conn,actor,document,edition_id,data)
        if conn.execute('''SELECT 1 FROM document_edition_verifications v
            JOIN document_editions e ON e.id=v.edition_id
            LEFT JOIN document_edition_endings ending ON ending.edition_id=v.edition_id WHERE e.document_id=%s
            AND daterange(v.valid_from,coalesce(ending.valid_to,v.valid_to),'[]') && daterange(%s::date,%s::date,'[]')''',
                        (document_id,data.valid_from,data.valid_to)).fetchone():
            raise HTTPException(409,'Период действия пересекается с другой подтверждённой редакцией. '
                                'Завершите действие прежней редакции или выберите другую дату начала')
        conn.execute('''INSERT INTO document_edition_verifications(edition_id,valid_from,valid_to,approved_on,evidence,verified_by)
            VALUES(%s,%s,%s,%s,%s,%s)''',
            (edition_id,data.valid_from,data.valid_to,data.approved_on,data.evidence,actor.id))
        _audit(conn,actor,document,'document.edition.verified',edition_id,data.model_dump(mode='json',exclude={'close_previous'}))
        return next(e for e in _editions(conn,document_id,True) if e['id']==edition_id)


def end_validity(db,actor,document_id,edition_id,data):
    with db.pool.connection() as conn:
        initial=_document(db,conn,actor,document_id,'editions.verify')
        _lock_scope(db,conn,actor,initial['department_id'],'editions.verify')
        document=_document(db,conn,actor,document_id,'editions.verify',lock=True)
        verification=conn.execute('''SELECT v.* FROM document_edition_verifications v
            JOIN document_editions e ON e.id=v.edition_id WHERE e.id=%s AND e.document_id=%s''',
            (edition_id,document_id)).fetchone()
        if not verification:
            raise HTTPException(409,'Период можно завершить только у подтверждённой редакции этого документа')
        ending=conn.execute('SELECT * FROM document_edition_endings WHERE edition_id=%s',(edition_id,)).fetchone()
        if ending:
            if (ending['valid_to'],ending['evidence'])==(data.valid_to,data.evidence):
                return next(e for e in _editions(conn,document_id,True) if e['id']==edition_id)
            raise HTTPException(409,'Завершение периода уже зафиксировано и не может быть изменено')
        if (data.valid_to<verification['valid_from'] or
            (verification['valid_to'] is not None and data.valid_to>verification['valid_to'])):
            raise HTTPException(422,'Дата завершения должна входить в подтверждённый период действия')
        conn.execute('''INSERT INTO document_edition_endings(edition_id,valid_to,evidence,ended_by)
            VALUES(%s,%s,%s,%s)''',(edition_id,data.valid_to,data.evidence,actor.id))
        _audit(conn,actor,document,'document.edition.validity.ended',edition_id,data.model_dump(mode='json'))
        return next(e for e in _editions(conn,document_id,True) if e['id']==edition_id)
