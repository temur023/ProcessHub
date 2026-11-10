"""Материалы карты: прикрепить, перечислить, открыть PDF, удалить — с проверкой формата и прав."""
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services.store import get_store
from tests.test_auth import SignedOutTest, email
from tests.test_pmm_tree import _wide_map

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF'
XLSX = b'PK\x03\x04' + b'\x00' * 40


class AttachmentsTest(SignedOutTest):
    def signed_in(self, name='Сотрудник', mail=None):
        client = TestClient(app)
        self.assertEqual(self.register(client, name=name, mail=mail or email()).status_code, 200)
        return client

    def setUp(self):
        super().setUp()
        self.author = self.signed_in('Автор')
        res = self.author.post('/api/v1/import/file', params={'asNew': 'true'},
                               files={'file': (f'Материалы {uuid.uuid4().hex[:6]}.drawio', _wide_map(2).encode(), 'application/xml')})
        self.assertEqual(res.status_code, 200, res.text)
        self.pid = res.json()['id']
        self.base = f'/api/v1/processes/{self.pid}/attachments'

    def attach(self, client, name, content, kind='approved'):
        return client.post(self.base, data={'kind': kind}, files={'file': (name, content, 'application/octet-stream')})

    def test_attach_list_open_and_keep_history(self):
        first = self.attach(self.author, 'Утверждённый регламент.pdf', PDF)
        self.assertEqual(first.status_code, 201, first.text)
        second = self.attach(self.author, 'Регламент.xlsx', XLSX, kind='regulation')
        self.assertEqual(second.status_code, 201, second.text)

        listed = self.author.get(self.base).json()
        self.assertEqual([a['fileName'] for a in listed], ['Регламент.xlsx', 'Утверждённый регламент.pdf'])
        self.assertEqual(listed[1]['kind'], 'approved')
        self.assertEqual(listed[1]['uploadedBy'], 'Автор')
        self.assertNotIn('content', listed[0])

        opened = self.author.get(f"{self.base}/{first.json()['id']}", params={'inline': 'true'})
        self.assertEqual((opened.status_code, opened.content), (200, PDF))
        self.assertTrue(opened.headers['content-disposition'].startswith('inline;'))
        # Excel в браузере не открывается — только скачивание, даже если попросили inline.
        sheet = self.author.get(f"{self.base}/{second.json()['id']}", params={'inline': 'true'})
        self.assertTrue(sheet.headers['content-disposition'].startswith('attachment;'))

    def test_wrong_format_and_disguised_file_are_refused(self):
        self.assertEqual(self.attach(self.author, 'script.exe', b'MZ').status_code, 415)
        self.assertEqual(self.attach(self.author, 'fake.pdf', b'<html>not a pdf</html>').status_code, 422)
        self.assertEqual(self.attach(self.author, 'x.pdf', PDF, kind='secret').status_code, 422)
        self.assertEqual(self.author.get(self.base).json(), [])

    def test_only_uploader_or_admin_removes(self):
        att = self.attach(self.author, 'Презентация.pdf', PDF, kind='presentation').json()
        stranger = self.signed_in('Чужой')
        self.assertEqual(stranger.delete(f"{self.base}/{att['id']}").status_code, 403)
        admin_mail = email('admin')
        admin = self.signed_in('Админ', admin_mail)
        get_store().set_platform_admin(admin_mail, True, 'Тест материалов карты')
        self.assertEqual(admin.delete(f"{self.base}/{att['id']}").status_code, 204)
        self.assertEqual(self.author.get(self.base).json(), [])

    def test_unknown_map_and_attachment(self):
        self.assertEqual(self.author.get('/api/v1/processes/nope/attachments').status_code, 404)
        self.assertEqual(self.author.get(f'{self.base}/att_missing').status_code, 404)

    def test_attachments_go_away_with_the_map(self):
        self.attach(self.author, 'Утверждённый.pdf', PDF)
        self.assertEqual(self.author.delete(f'/api/v1/processes/{self.pid}').status_code, 204)
        self.assertEqual(get_store().attachments(self.pid), [])


class AttachmentPreviewTest(AttachmentsTest):
    """Excel и PowerPoint открываются PDF-копией от сервиса просмотра; PDF — как есть."""

    def setUp(self):
        super().setUp()
        import httpx
        from app.routers import attachments as module
        self.module = module
        self.calls = []
        real_client = httpx.AsyncClient

        def handler(request):
            self.calls.append(request.url.path)
            return httpx.Response(200, content=b'%PDF-1.7 converted')

        def client(**kwargs):
            kwargs.pop('trust_env', None)
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        self.patch(module.httpx, 'AsyncClient', client)
        module._previews.clear()

    def patch(self, target, name, value):
        original = getattr(target, name)
        setattr(target, name, value)
        self.addCleanup(setattr, target, name, original)

    def env(self, value):
        import os
        old = os.environ.get('DOCUMENT_PREVIEW_URL')
        os.environ['DOCUMENT_PREVIEW_URL'] = value
        self.addCleanup(lambda: os.environ.__setitem__('DOCUMENT_PREVIEW_URL', old) if old is not None else os.environ.pop('DOCUMENT_PREVIEW_URL', None))

    def test_pdf_preview_is_the_file_itself(self):
        att = self.attach(self.author, 'Утверждённый.pdf', PDF).json()
        res = self.author.get(f"{self.base}/{att['id']}/preview")
        self.assertEqual((res.status_code, res.content), (200, PDF))
        self.assertEqual(self.calls, [])

    def test_excel_preview_is_converted_once_and_cached(self):
        self.env('http://preview.test')
        att = self.attach(self.author, 'Регламент.xlsx', XLSX, kind='regulation').json()
        for _ in range(2):
            res = self.author.get(f"{self.base}/{att['id']}/preview")
            self.assertEqual((res.status_code, res.content), (200, b'%PDF-1.7 converted'))
            self.assertTrue(res.headers['content-disposition'].startswith('inline;'))
        self.assertEqual(self.calls, ['/render-office/xlsx'])

    def test_without_preview_service_the_user_is_told_to_download(self):
        self.env('')
        att = self.attach(self.author, 'Защита.pptx', XLSX, kind='presentation').json()
        res = self.author.get(f"{self.base}/{att['id']}/preview")
        self.assertEqual(res.status_code, 503)
        self.assertIn('Скачайте файл', res.json()['detail'])

    def test_word_has_no_browser_preview(self):
        att = self.attach(self.author, 'Записка.docx', XLSX, kind='other').json()
        self.assertEqual(self.author.get(f"{self.base}/{att['id']}/preview").status_code, 415)
