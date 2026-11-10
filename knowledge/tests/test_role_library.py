import json
from uuid import uuid4
import hashlib
import io
import zipfile
import psycopg

from knowledge_service.document_structure import build_structure, build_functions, docx_functions
from knowledge_service.preparation_store import claim_job, complete_job
from knowledge_service.extraction import extract_document
from test_registry import docx, send
from test_documents import upload


def source(blocks):
    return {'id':'source-1','content_hash':'a'*64,'parser_version':'test','status':'ready','warnings':[],
        'blocks':[{'id':f'b{i}','kind':'paragraph','label':f'Абзац {i+1}',
                   'locator':{'paragraph':i+1},'text':text} for i,text in enumerate(blocks)]}


def test_chapters_keep_exact_quotes_locators_and_do_not_classify_mentions_as_headings():
    raw=source(['Заголовок\n1-bob. Umumiy qoidalar\n1. Menejer tayinlanadi.',
        '2-bob. Menejerning vazifalari\n2. Vazifalarni bajaradi;\n3. Hujjatlarni tekshiradi.',
        '3-bob. Huquqlari\n4. Ma’lumot oladi.', '4-bob. Javobgarligi\n5. Javobgar.'])
    result=build_structure(raw)
    assert [s['kind'] for s in result['sections']]==['source','overview','duties','rights','responsibility']
    quotes=[b for s in result['sections'] for b in s['blocks']]
    for block in raw['blocks']:
        assert ''.join(b['text'] for b in quotes if b['source_block_id']==block['id'])==block['text']
    assert {b['source_block_id'] for b in quotes}=={b['id'] for b in raw['blocks']}
    assert all(b['locator']==raw['blocks'][int(b['source_block_id'][1:])]['locator'] for b in quotes)
    assert result['review_required'] is True


def test_unknown_chapter_and_unstructured_source_are_preserved():
    result=build_structure(source(['Текст без глав: обязанности не определены.', '7-bob. Boshqa qism\nTushuntirish.']))
    assert [s['kind'] for s in result['sections']]==['source','other']
    assert result['chapter_count']==0
    assert result['sections'][1]['blocks'][0]['text'].endswith('Tushuntirish.')


def test_distinct_functions_and_obligations_preserve_whitespace_and_every_item():
    raw=source([' 2-bob. Vazifalari\n 1. Birinchi vazifa;\n\n 2. Ikkinchi vazifa.\n',
                '3-bob. Majburiyatlari\nMajburiyatlar.\n',
                '4-bob. Huquqlari va vazifalarni bajarish vakolatlari\nVakolatlar.'])
    result=build_structure(raw)
    assert [s['kind'] for s in result['sections']]==['duties','obligations','rights']
    for block in raw['blocks']:
        assert ''.join(q['text'] for s in result['sections'] for q in s['blocks']
                       if q['source_block_id']==block['id'])==block['text']


def test_role_library_scope_profile_audit_and_deleted_documents(registry):
    client,settings,dept,role,other=registry
    result=upload(registry,document_type='job_instruction',role_id=str(role))
    assert result.status_code==201
    document_id=result.json()['document']['id']
    url=f'/departments/{dept}/role-library'
    assert send(registry,'GET',url,actor=2).json()['roles'][0]['document_count']==1
    assert send(registry,'GET',f'/departments/{other}/role-library',actor=2).status_code==404
    assert send(registry,'GET',url+'/'+str(uuid4()),actor=2).status_code==404
    patch={'display_name':'Главный менеджер','unit_name':'Служба контроля','category':'management',
           'historical_candidate':False,'classification_evidence':'Заголовок инструкции, п. 1'}
    assert send(registry,'PATCH',url+'/'+str(role),actor=2,json=patch).status_code==403
    assert send(registry,'PATCH',url+'/'+str(role),actor=1,json=patch).status_code==200
    detail=send(registry,'GET',url+'/'+str(role),actor=2).json()
    assert detail['role']['display_name']=='Главный менеджер'
    assert detail['documents'][0]['id']==document_id
    assert detail['documents'][0]['current_edition'] is None
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='business_role.classified'").fetchone()[0]==1
    assert send(registry,'POST',f'/documents/{document_id}/delete',actor=3,admin=True,json={'reason':'Тест восстановления'}).status_code==200
    # The position stays in the structure; only its deleted instruction is no longer counted.
    roles=send(registry,'GET',url,actor=2).json()['roles']
    assert [(item['id'],item['document_count'],item['file_count']) for item in roles]==[(str(role),0,0)]


def test_position_is_created_without_instruction_and_deleted_only_when_unused(registry):
    client,settings,dept,role,other=registry
    url=f'/departments/{dept}/role-library'
    profile={'display_name':'Kichik menejer','unit_name':"To'lovlar xizmati /  SWIFT guruhi ",'category':'specialists',
             'historical_candidate':False,'classification_evidence':'Кадровый реестр HR'}
    assert send(registry,'POST',url,actor=2,json=profile).status_code==403
    assert send(registry,'POST',f'/departments/{other}/role-library',json=profile).status_code==404
    created=send(registry,'POST',url,json=profile)
    assert created.status_code==201,created.text
    position=created.json()
    assert (position['name'],position['document_count'])==("Kichik menejer — To'lovlar xizmati / SWIFT guruhi",0)
    assert position['id'] in [item['id'] for item in send(registry,'GET',url,actor=2).json()['roles']]
    assert send(registry,'POST',url,json={**profile,'display_name':'kichik menejer'}).status_code==409
    # Filing an instruction under the same name reuses the created position.
    filed=upload(registry,document_type='job_instruction',title='Yo‘riqnoma',
                 new_position=json.dumps({key:profile[key] for key in ('display_name','unit_name','category')},ensure_ascii=False))
    assert filed.json()['document']['role_id']==position['id']
    assert send(registry,'DELETE',f"{url}/{position['id']}",actor=2).status_code in (403,404)
    assert send(registry,'DELETE',f"{url}/{position['id']}",admin=True).status_code==409
    document_id=filed.json()['document']['id']
    assert send(registry,'POST',f'/documents/{document_id}/delete',admin=True,json={'reason':'Проверка архива'}).status_code==200
    # An instruction in the archive still holds its position.
    assert send(registry,'DELETE',f"{url}/{position['id']}",admin=True).status_code==409
    empty=send(registry,'POST',url,json={**profile,'display_name':'Menejer'}).json()
    assert send(registry,'DELETE',f"{url}/{empty['id']}",admin=True).status_code==204
    assert send(registry,'GET',f"{url}/{empty['id']}").status_code==404
    assert send(registry,'DELETE',f"{url}/{empty['id']}",admin=True).status_code==404
    with psycopg.connect(settings.database_url) as conn:
        actions=[row[0] for row in conn.execute("SELECT action FROM audit_events WHERE action LIKE 'business_role.%' ORDER BY id")]
    assert actions.count('business_role.created')>=2 and actions.count('business_role.deleted')==1


def test_file_structure_is_version_bound_and_requires_existing_document_access(registry):
    text='1-bob. Umumiy qoidalar\nQoidalar.\n2-bob. Vazifalari\nHujjatlarni tekshirish.'
    result=upload(registry,document_type='job_instruction',role_id=str(registry[3]),
        files=[('files',('Инструкция.docx',docx(text)))])
    original=result.json()['edition']['files'][0]
    url=f"/documents/files/{original['id']}/structure"
    assert send(registry,'GET',url,actor=2).json()['structure'] is None
    db=registry[0].app.state.store
    job=claim_job(db)
    assert complete_job(db,job['id'],job['lease_token'],extract_document(bytes(job['content']),job['media_type']))
    structure=send(registry,'GET',url,actor=2).json()['structure']
    assert structure['content_hash']==original['content_hash']
    assert 'duties' in [section['kind'] for section in structure['sections']]
    assert {section['kind'] for section in structure['sections']} == {'duties'}
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute('DELETE FROM access_grants WHERE user_id=2')
    assert send(registry,'GET',url,actor=2).status_code==404


def test_functions_are_complete_chapter_blocks_with_original_list_numbers():
    text = ' 2-bob. Vazifalari\n6. Vazifalar:\n  6.1. Birinchi vazifa;\n\n  6.2. Ikkinchi vazifa.\n'
    result = build_functions(source(['1-bob. Umumiy qoidalar\nQoidalar.', text,
                                      '3-bob. Majburiyatlari\nBoshqa matn.']))
    assert len(result['sections']) == 1
    assert len(result['sections'][0]['blocks']) == 1
    assert result['sections'][0]['blocks'][0]['text'] == text
    assert 'Boshqa matn' not in str(result['sections'])


def test_unknown_function_boundaries_never_fallback_to_full_document():
    result = build_functions(source(['Текст без глав. 1. Обязанности сотрудника.']))
    assert result['sections'] == []


def test_compact_source_title_without_bob_retains_numbered_clause_and_full_list():
    result = build_functions(source(['1-bob. Umumiy qoidalar', 'Qoidalar.',
        '2. Yetakchi menejerining vazifalari',
        '6. Yetakchi menejerning asosiy vazifa va funksiyalari quyidagilardan iborat:\nBirinchi vazifa;\nIkkinchi vazifa.',
        '3-bob. Majburiyatlari', '8. Boshqa majburiyat.']))
    assert len(result['sections']) == 1
    assert result['sections'][0]['source_heading'] == '2. Yetakchi menejerining vazifalari'
    assert result['sections'][0]['blocks'][1]['text'].endswith('Ikkinchi vazifa.')
    assert '8. Boshqa' not in str(result['sections'])


def revision_docx():
    out = io.BytesIO()
    body = '''<w:p><w:r><w:t>1-bob. Umumiy qoidalar</w:t></w:r></w:p>
      <w:p><w:r><w:t>Do not expose other chapters.</w:t></w:r></w:p>
      <w:p><w:r><w:t>2-bob. Menejerning vazifalari</w:t></w:r></w:p>
      <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
        <w:r><w:t xml:space="preserve">Tekshiradi  </w:t></w:r>
        <w:del><w:r><w:delText>eski matn;</w:delText></w:r></w:del>
        <w:ins><w:r><w:rPr><w:b/></w:rPr><w:t>yangi matn;</w:t></w:r></w:ins>
        <w:r><w:br/><w:t>Davomi.</w:t></w:r></w:p>
      <w:p><w:moveFrom><w:r><w:delText>Ko‘chirilgan eski vazifa.</w:delText></w:r></w:moveFrom>
        <w:moveTo><w:r><w:t>Ko‘chirilgan yangi vazifa.</w:t></w:r></w:moveTo></w:p>
      <w:p><w:r><w:t>3-bob. Huquqlari</w:t></w:r></w:p>
      <w:p><w:r><w:t>Do not expose rights.</w:t></w:r></w:p>'''
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
        archive.writestr('word/numbering.xml', '''<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="6"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>
          <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>''')
    return out.getvalue()


def test_functions_retain_literal_tracked_changes_and_native_numbering():
    content = revision_docx()
    result = docx_functions(content, 'source-revisions')
    assert result['content_hash'] == hashlib.sha256(content).hexdigest()
    assert result['has_revisions'] is True
    assert len(result['sections']) == 1
    blocks = result['sections'][0]['blocks']
    assert blocks[1]['text'] == '6. Tekshiradi  eski matn;yangi matn;\nDavomi.'
    assert 'Ko‘chirilgan eski vazifa.Ko‘chirilgan yangi vazifa.' == blocks[2]['text']
    for block in blocks:
        assert ''.join(run['text'] for run in block['runs']) == block['text']
    assert [run['text'] for run in blocks[1]['runs'] if run.get('change') == 'deleted'] == ['eski matn;']
    assert [run['text'] for run in blocks[1]['runs'] if run.get('change') == 'inserted'] == ['yangi matn;']
    assert 'Do not expose' not in str(result['sections'])


def test_revision_navigation_does_not_change_the_original_or_preparation_ledger(registry):
    content = revision_docx()
    result = upload(registry, document_type='job_instruction', role_id=str(registry[3]),
                    files=[('files', ('Исправления.docx', content))])
    original = result.json()['edition']['files'][0]
    db = registry[0].app.state.store
    job = claim_job(db)
    assert complete_job(db, job['id'], job['lease_token'], extract_document(bytes(job['content']), job['media_type']))
    path = f"/documents/files/{original['id']}"
    before = send(registry, 'GET', path + '/preparation', actor=2).json()
    functions = send(registry, 'GET', path + '/structure', actor=2).json()['structure']
    assert functions['has_revisions']
    assert 'eski matn;' in str(functions['sections'])
    assert send(registry, 'GET', path + '/original', actor=2).content == content
    assert send(registry, 'GET', path + '/preparation', actor=2).json() == before
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute('DELETE FROM access_grants WHERE user_id=2')
    assert send(registry, 'GET', path + '/structure', actor=2).status_code == 404


def test_doc_functions_have_source_revisions_and_original_doc_hash(registry, monkeypatch):
    from test_office_formats import legacy_doc
    content, converted = legacy_doc(), revision_docx()
    result = upload(registry, document_type='job_instruction', role_id=str(registry[3]),
                    files=[('files', ('Исходный.doc', content))])
    original = result.json()['edition']['files'][0]
    db = registry[0].app.state.store
    job = claim_job(db)
    prepared = extract_document(converted, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    assert complete_job(db, job['id'], job['lease_token'], prepared)
    calls = []
    def transient_source(row):
        calls.append(bytes(row['content']))
        return converted
    monkeypatch.setattr('knowledge_service.role_library_routes.word_functions_source', transient_source)
    path = f"/documents/files/{original['id']}"
    structure = send(registry, 'GET', path + '/structure', actor=2).json()['structure']
    assert structure['content_hash'] == original['content_hash'] == hashlib.sha256(content).hexdigest()
    assert structure['has_revisions'] is True
    assert structure['parser_version'] == 'functions-doc-v1'
    assert all(block['locator']['derived_format'] == 'docx' and block['locator']['format'] == 'doc'
               for section in structure['sections'] for block in section['blocks'])
    assert calls == [content]
    assert send(registry, 'GET', path + '/original', actor=2).content == content
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute('DELETE FROM access_grants WHERE user_id=2')
    assert send(registry, 'GET', path + '/structure', actor=2).status_code == 404
    assert calls == [content]
