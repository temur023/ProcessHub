"""Reviewed local corpus intake. Originals and legal verification stay separate.

Run inside the trusted knowledge service after checking the Hub administrator.
The manifest is data; no file or embedded instruction is executed.
"""
import argparse
import asyncio
import io
import json
from collections import defaultdict
from pathlib import Path
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from psycopg.types.json import Jsonb
from starlette.datastructures import UploadFile

from .auth import Principal
from .config import Settings
from .document_models import DocumentInput, EditionInput, FileMetadata
from .document_store import create_document, _lock_scope
from .files import read_original
from .store import Store


async def prepare(manifest, directory, department_id):
    entries = manifest['files']
    if len(entries) != 136 or len({item['relative'] for item in entries}) != len(entries):
        raise ValueError('Ожидается проверенный комплект из 136 уникальных путей')
    prepared, titles = [], defaultdict(int)
    for item in entries:
        path = directory / Path(item['path']).name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item['bytes']:
            raise ValueError(f"Несовпадение оригинала №{item['id']}")
        content = path.read_bytes()
        original = await read_original(UploadFile(io.BytesIO(content), filename=Path(item['relative']).name), 10*1024*1024)
        role = item.get('role') or ''
        key = (item['unit'], role) if role else None
        title = original['filename']
        titles[(key,title)] += 1
        prepared.append((item,key,title,original))
    return [(item,key,title + (f" · экземпляр {item['id']}" if titles[(key,title)]>1 else ''),original)
            for item,key,title,original in prepared]


def run_import(db, actor, manifest, directory, department_id):
    prepared = asyncio.run(prepare(manifest,directory,department_id))
    pdf_dates = {item['id']:item for item in json.loads((directory/'pdf-edition-review.json').read_text())['documents']}
    grouped = defaultdict(list)
    for item,key,_,_ in prepared:
        if key:
            grouped[key].append(item)
    role_ids = {}
    for (unit,name), items in grouped.items():
        category = ('other' if 'в тексте' in name else 'management' if name in ('Директор','Заместитель директора','Главный менеджер')
                    else 'support' if name == 'Делопроизводитель' else 'specialists')
        evidence = 'Структура переданного архива и заголовки оригиналов. Полные пути каждого источника сохранены в реквизитах документов. Служба: ' + unit
        full_name = f'{name} — {unit}'
        with db.pool.connection() as conn:
            _lock_scope(db,conn,actor,department_id,'instructions.create')
            row = conn.execute('SELECT * FROM roles WHERE department_id=%s AND name=%s FOR UPDATE',(department_id,full_name)).fetchone()
            if row is None:
                row = conn.execute('INSERT INTO roles(id,department_id,name) VALUES(%s,%s,%s) RETURNING *',(uuid4(),department_id,full_name)).fetchone()
            profile = (name,unit,category,all(item['historical'] for item in items),evidence)
            if profile != tuple(row[key] for key in ('display_name','unit_name','category','historical_candidate','classification_evidence')):
                conn.execute('''UPDATE roles SET display_name=%s,unit_name=%s,category=%s,
                    historical_candidate=%s,classification_evidence=%s WHERE id=%s''',(*profile,row['id']))
                conn.execute('''INSERT INTO audit_events(actor_id,department_id,action,entity_id,details)
                    VALUES(%s,%s,'business_role.classified',%s,%s)''',
                    (actor.id,department_id,row['id'],Jsonb({'source':'operations-corpus-2026','source_count':len(items),'category':category})))
            role_ids[(unit,name)] = row['id']
    count, replayed = 0, 0
    for item,key,title,original in prepared:
        dated = pdf_dates.get(item['id'],{})
        data = DocumentInput(title=title,document_type='job_instruction' if key else 'order' if item['id']==26 else 'analytics',
            department_id=department_id,owner_department_id=department_id if key or item['id'] in (96,97) else None,role_id=role_ids.get(key),
            bank_number=dated.get('document_number'),document_date=dated.get('date_iso'),
            metadata={'source_name':'Комплект документов операционного департамента',
                'source_reference':item['relative'], 'archive_name':Path(manifest['archive']).name,
                'corpus':'operations-corpus-2026','corpus_item':item['id'],
                'historical_candidate':item['historical'],'classification_note':item.get('note',''),
                'original_title':item.get('title',''), 'employee_in_filename':item.get('employee_in_filename') or '',
                'date_qualification':dated.get('qualification','Дата в реквизитах не установлена')})
        edition = EditionInput(label='Исторический экземпляр · требует проверки' if item['historical'] else 'Переданный экземпляр · требует проверки')
        upload_id = uuid5(NAMESPACE_URL,'operations-corpus-2026:'+str(department_id)+':'+item['relative'])
        result = create_document(db,actor,data,edition,[original],
            [FileMetadata(source_metadata={'source_reference':item['relative'],'archive_name':Path(manifest['archive']).name})],upload_id)
        count += 1; replayed += int(result['replayed'])
    return {'documents':count,'business_positions':len(role_ids),'replayed':replayed,
            'instruction_files':sum(bool(key) for _,key,_,_ in prepared),
            'historical_candidates':sum(item['historical'] for item,_,_,_ in prepared),
            'verification':'unverified; no legal or personnel approval inferred'}


def main():
    parser = argparse.ArgumentParser(description='Приём проверенного комплекта документов Ops')
    parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--department-id',type=UUID,required=True)
    parser.add_argument('--actor-id',type=int,required=True)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    if args.actor_id <= 0:
        parser.error('Нужен действующий администратор Hub')
    manifest = json.loads((args.source_dir/'report-data.json').read_text())
    if not args.apply:
        ready = asyncio.run(prepare(manifest,args.source_dir,args.department_id))
        print(json.dumps({'files':len(ready),'bytes':sum(item[0]['bytes'] for item in ready),'mode':'preflight'},ensure_ascii=False))
        return
    settings = Settings.from_env(); db = Store(settings.database_url)
    try:
        print(json.dumps(run_import(db,Principal(args.actor_id,True),manifest,args.source_dir,args.department_id),ensure_ascii=False))
    finally:
        db.close()


if __name__ == '__main__':
    main()
