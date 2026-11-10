"""Department positions and original-backed chapter navigation."""
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, field_validator
from psycopg.types.json import Jsonb

from .document_models import StrictInput, clean_text
from .document_store import _dto, _lock_scope
from .document_structure import build_functions, docx_functions, doc_functions
from .preview import word_functions_source
from .preparation_store import get_preparation, _version
from .extraction import ExtractionError

router = APIRouter()


class RoleProfileInput(StrictInput):
    display_name: str = Field(min_length=1, max_length=300)
    unit_name: str = Field(min_length=1, max_length=300)
    category: Literal['management','specialists','support','other']
    historical_candidate: bool = False
    classification_evidence: str = Field(min_length=1, max_length=2000)
    _text = field_validator('display_name','unit_name','classification_evidence')(clean_text)

    @field_validator('unit_name')
    @classmethod
    def unit_path(cls, value):
        return ' / '.join(part.strip() for part in value.split('/') if part.strip())


@router.get('/departments/{department_id}/role-library')
def role_library(department_id: UUID, request: Request):
    db, actor = request.app.state.store, request.state.actor
    with db.pool.connection() as conn:
        db.authorize(conn, actor, department_id)
        roles = conn.execute('''SELECT r.*,count(DISTINCT i.id) AS document_count,
            count(DISTINCT f.file_id) AS file_count FROM roles r
            LEFT JOIN instructions i ON i.role_id=r.id AND i.department_id=r.department_id
                AND i.document_type='job_instruction' AND i.deleted_at IS NULL
            LEFT JOIN document_editions e ON e.document_id=i.id
            LEFT JOIN document_edition_files f ON f.edition_id=e.id
            WHERE r.department_id=%s GROUP BY r.id
            ORDER BY r.historical_candidate,r.unit_name,coalesce(r.display_name,r.name),r.id''', (department_id,)).fetchall()
        unassigned = conn.execute('''SELECT count(*) AS count FROM instructions
            WHERE department_id=%s AND document_type='job_instruction' AND role_id IS NULL
            AND deleted_at IS NULL''', (department_id,)).fetchone()['count']
        return {'roles': roles, 'unassigned_documents': unassigned}


@router.get('/departments/{department_id}/role-library/{role_id}')
def role_detail(department_id: UUID, role_id: UUID, request: Request):
    db, actor = request.app.state.store, request.state.actor
    with db.pool.connection() as conn:
        db.authorize(conn, actor, department_id)
        role = conn.execute('SELECT * FROM roles WHERE id=%s AND department_id=%s', (role_id,department_id)).fetchone()
        if role is None:
            raise HTTPException(404, 'Должность недоступна')
        rows = conn.execute('''SELECT * FROM instructions WHERE role_id=%s AND department_id=%s
            AND document_type='job_instruction' AND deleted_at IS NULL
            ORDER BY document_date DESC NULLS LAST,title,id''', (role_id,department_id)).fetchall()
        return {'role': role, 'documents': [_dto(conn,actor,row,detail=True) for row in rows]}


@router.patch('/departments/{department_id}/role-library/{role_id}')
def update_role_profile(department_id: UUID, role_id: UUID, data: RoleProfileInput, request: Request):
    db, actor = request.app.state.store, request.state.actor
    with db.pool.connection() as conn:
        _lock_scope(db,conn,actor,department_id,'instructions.create')
        previous = conn.execute('SELECT * FROM roles WHERE id=%s AND department_id=%s FOR UPDATE', (role_id,department_id)).fetchone()
        if previous is None:
            raise HTTPException(404, 'Должность недоступна')
        # The stored name identifies the position when instructions are filed;
        # renaming or moving keeps it in step and never merges two positions.
        name = f'{data.display_name} — {data.unit_name}'
        if conn.execute('SELECT 1 FROM roles WHERE department_id=%s AND lower(name)=lower(%s) AND id<>%s',
                        (department_id,name,role_id)).fetchone():
            raise HTTPException(409, 'В этой службе уже есть должность с таким названием. Выберите другое название или службу')
        row = conn.execute('''UPDATE roles SET name=%s,display_name=%s,unit_name=%s,category=%s,
            historical_candidate=%s,classification_evidence=%s WHERE id=%s RETURNING *''',
            (name,data.display_name,data.unit_name,data.category,data.historical_candidate,data.classification_evidence,role_id)).fetchone()
        conn.execute('''INSERT INTO audit_events(actor_id,department_id,action,entity_id,details)
            VALUES(%s,%s,'business_role.classified',%s,%s)''',
            (actor.id,department_id,role_id,Jsonb({'before':{key:previous[key] for key in RoleProfileInput.model_fields},'after':data.model_dump()})))
        return row


@router.post('/departments/{department_id}/role-library', status_code=201)
def create_role_profile(department_id: UUID, data: RoleProfileInput, request: Request):
    db, actor = request.app.state.store, request.state.actor
    with db.pool.connection() as conn:
        _lock_scope(db,conn,actor,department_id,'instructions.create')
        name = f'{data.display_name} — {data.unit_name}'
        if conn.execute('SELECT 1 FROM roles WHERE department_id=%s AND lower(name)=lower(%s)', (department_id,name)).fetchone():
            raise HTTPException(409, 'В этой службе уже есть должность с таким названием. Выберите другое название или службу')
        row = conn.execute('''INSERT INTO roles(id,department_id,name,display_name,unit_name,category,historical_candidate,classification_evidence)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
            (uuid4(),department_id,name,data.display_name,data.unit_name,data.category,data.historical_candidate,data.classification_evidence)).fetchone()
        conn.execute('''INSERT INTO audit_events(actor_id,department_id,action,entity_id,details)
            VALUES(%s,%s,'business_role.created',%s,%s)''',(actor.id,department_id,row['id'],Jsonb(data.model_dump())))
        return {**row, 'document_count': 0, 'file_count': 0}


@router.delete('/departments/{department_id}/role-library/{role_id}', status_code=204)
def delete_role_profile(department_id: UUID, role_id: UUID, request: Request):
    db, actor = request.app.state.store, request.state.actor
    with db.pool.connection() as conn:
        _lock_scope(db,conn,actor,department_id,'instructions.delete')
        role = conn.execute('SELECT * FROM roles WHERE id=%s AND department_id=%s FOR UPDATE', (role_id,department_id)).fetchone()
        if role is None:
            raise HTTPException(404, 'Должность недоступна')
        # Instructions keep their position, including those in the archive: the position goes only when empty.
        if conn.execute('SELECT 1 FROM instructions WHERE role_id=%s LIMIT 1', (role_id,)).fetchone():
            raise HTTPException(409, 'У должности есть инструкции, в том числе в архиве. Перенесите их в другую должность или удалите, затем повторите')
        conn.execute('DELETE FROM roles WHERE id=%s', (role_id,))
        conn.execute('''INSERT INTO audit_events(actor_id,department_id,action,entity_id,details)
            VALUES(%s,%s,'business_role.deleted',%s,%s)''',
            (actor.id,department_id,role_id,Jsonb({key:role[key] for key in ('name',*RoleProfileInput.model_fields)})))


@router.get('/documents/files/{file_id}/structure')
def file_structure(file_id: UUID, request: Request):
    db, actor = request.app.state.store, request.state.actor
    state = get_preparation(db, actor, file_id)
    if not state['source']:
        return {'job': state['job'], 'structure': None}
    with db.pool.connection() as conn:
        # Reading functions uses document read access, independently of download.
        _version(db, conn, actor, file_id)
        row = conn.execute('''SELECT v.content,v.media_type,i.document_type FROM instruction_versions v
            JOIN instructions i ON i.id=v.instruction_id WHERE v.id=%s''', (file_id,)).fetchone()
    if row['document_type'] != 'job_instruction':
        raise HTTPException(422, 'Функции выделяются только из должностных инструкций')
    return {'job': state['job'], 'structure': functions_structure(row, state['source'])}


def functions_structure(row, source, kinds=('duties',)):
    """Function chapters of one original; shared by the viewer and the analysis."""
    try:
        if row['media_type'] == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
            return docx_functions(bytes(row['content']), str(source['id']), kinds)
        if row['media_type'] == 'application/msword':
            return doc_functions(bytes(row['content']), word_functions_source(row), str(source['id']), kinds)
    except ExtractionError as error:
        raise HTTPException(422, error.message) from None
    return build_functions(source, kinds)
