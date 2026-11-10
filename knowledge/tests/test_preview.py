import io
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi import HTTPException
from pypdf import PdfWriter
import psycopg
import pytest

from knowledge_service import preview
from test_documents import upload
from test_registry import send, docx
from test_role_library import revision_docx


def pdf():
    result = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(result)
    return result.getvalue()


def test_pdf_preview_is_exact_original_and_access_is_checked_on_each_request(registry):
    content = pdf()
    result = upload(registry, files=[('files', ('Оригинал.pdf', content))])
    file_id = result.json()['edition']['files'][0]['id']
    path = f'/documents/files/{file_id}/preview'
    response = send(registry, 'GET', path, actor=2)
    assert response.status_code == 200 and response.content == content
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == 'inline'
    assert response.headers['cache-control'] == 'no-store'
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute('DELETE FROM access_grants WHERE user_id=2')
    assert send(registry, 'GET', path, actor=2).status_code == 404
    assert send(registry, 'GET', path, actor=3, admin=True).content == content


def test_word_preview_does_not_use_prepared_text_or_replace_original(registry, monkeypatch):
    content = docx('Оригинальные данные')
    result = upload(registry, files=[('files', ('Исходный.docx', content))])
    file_id = result.json()['edition']['files'][0]['id']
    calls = []
    def render(row, clean=True):
        calls.append((bytes(row['content']), clean))
        return pdf()
    monkeypatch.setattr('knowledge_service.document_routes.preview_pdf', render)
    assert send(registry, 'GET', f'/documents/files/{file_id}/preview', actor=2).status_code == 200
    assert calls == [(content, True)]
    assert send(registry, 'GET', f'/documents/files/{file_id}/preview?view=markup', actor=2).status_code == 200
    assert calls[-1] == (content, False)
    assert send(registry, 'GET', f'/documents/files/{file_id}/preview?view=raw', actor=2).status_code == 422
    calls.clear()
    assert send(registry, 'GET', f'/documents/files/{file_id}/original', actor=2).content == content
    with psycopg.connect(registry[1].database_url) as conn:
        conn.execute('DELETE FROM access_grants WHERE user_id=2')
    assert send(registry, 'GET', f'/documents/files/{file_id}/preview', actor=2).status_code == 404
    assert calls == []


def test_preview_failures_never_return_parser_text(monkeypatch):
    monkeypatch.delenv('DOCUMENT_PREVIEW_URL', raising=False)
    with pytest.raises(HTTPException) as caught:
        preview.preview_pdf({'filename':'Недоступный.docx','media_type':'word','content':docx('Текст')})
    assert caught.value.status_code == 503
    with pytest.raises(HTTPException) as caught:
        preview.preview_pdf({'filename':'Книга.xlsx','media_type':'excel','content':b'data'})
    assert caught.value.status_code == 415


def test_word_cache_is_bound_to_source_hash_and_validated_pdf(monkeypatch):
    monkeypatch.setenv('DOCUMENT_PREVIEW_URL', 'http://private-preview:8000')
    preview._cache.clear()
    calls = []
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_bytes(self): yield pdf()
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def stream(self, method, url, **kwargs):
            calls.append((url, kwargs['content']))
            return Response()
    monkeypatch.setattr(preview.httpx, 'Client', Client)
    row = {'filename':'Инструкция.docx','media_type':'word','content':docx('Первый оригинал')}
    assert preview.preview_pdf(row) == preview.preview_pdf(row) == pdf()
    assert len(calls) == 1
    preview.preview_pdf({**row,'content':docx('Другая редакция')})
    assert len(calls) == 2
    preview._cache.clear()


def test_doc_function_source_preserves_the_converted_package_and_is_original_bound(monkeypatch):
    monkeypatch.setenv('DOCUMENT_PREVIEW_URL', 'http://private-preview:8000')
    preview._functions_cache.clear()
    converted = revision_docx()
    calls = []
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_bytes(self): yield converted
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def stream(self, method, url, **kwargs):
            calls.append((url, kwargs['content']))
            return Response()
    monkeypatch.setattr(preview.httpx, 'Client', Client)
    row = {'content': b'first immutable DOC'}
    assert preview.word_functions_source(row) == preview.word_functions_source(row) == converted
    assert calls == [('http://private-preview:8000/functions-source/doc', row['content'])]
    preview.word_functions_source({'content': b'other DOC'})
    assert len(calls) == 2
    converted = b'not a Word source'
    with pytest.raises(HTTPException) as error:
        preview.word_functions_source({'content': b'third DOC'})
    assert error.value.status_code == 422
    preview._functions_cache.clear()
    monkeypatch.delenv('DOCUMENT_PREVIEW_URL')
    with pytest.raises(HTTPException) as error:
        preview.word_functions_source({'content': b'no renderer'})
    assert error.value.status_code == 503


@pytest.mark.parametrize('kind', ['pdf', 'functions'])
def test_concurrent_views_of_the_same_word_share_conversion(monkeypatch, kind):
    cache = preview._cache if kind == 'pdf' else preview._functions_cache
    cache.clear()
    started, release = Event(), Event()
    calls = []
    result = pdf() if kind == 'pdf' else revision_docx()
    def convert(path, content):
        calls.append((path, content))
        started.set()
        assert release.wait(3)
        return result
    monkeypatch.setattr(preview, '_renderer', convert)
    row = {'filename': 'same.doc', 'media_type': 'application/msword', 'content': b'same original'}
    view = preview.preview_pdf if kind == 'pdf' else preview.word_functions_source
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(view, row)
        assert started.wait(3)
        second = executor.submit(view, row)
        release.set()
        assert first.result() == second.result() == result
    assert len(calls) == 1
    cache.clear()
