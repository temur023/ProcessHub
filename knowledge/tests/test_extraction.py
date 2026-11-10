import io
import zipfile
from xml.sax.saxutils import escape

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject, NumberObject

from knowledge_service.extraction import extract_document, ExtractionError, W

DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def word(body, **parts):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('[Content_Types].xml',date_time=(2026,10,1,0,0,0)), '<Types/>')
        archive.writestr(zipfile.ZipInfo('word/document.xml',date_time=(2026,10,1,0,0,0)), f'<w:document xmlns:w="{W[1:-1]}"><w:body>{body}</w:body></w:document>')
        for name, data in parts.items():
            archive.writestr(name, data)
    return out.getvalue()


def paragraph(text):
    return '<w:p><w:r><w:t>' + escape(text) + '</w:t></w:r></w:p>'


def pdf(*texts):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=595, height=842)
        if text:
            font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(f'BT /F1 12 Tf 40 800 Td ({text}) Tj ET'.encode())
            page[NameObject('/Contents')] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_docx_preserves_order_tables_and_stable_anchors():
    data = word(paragraph('1. Проверять документы') + '<w:tbl><w:tr><w:tc>' + paragraph('Роль') + '</w:tc><w:tc>' + paragraph('Обязанность') + '</w:tc></w:tr></w:tbl>' + paragraph('2. Формировать отчёт'))
    result = extract_document(data, DOCX)
    assert result['status'] == 'ready'
    assert [b['text'] for b in result['blocks']] == ['1. Проверять документы','Роль | Обязанность','2. Формировать отчёт']
    assert result['blocks'][1]['locator']['row'] == 1
    assert result['blocks'][1]['locator']['part'] == 'word/document.xml'
    assert 'page' not in result['blocks'][1]['locator']
    assert result == extract_document(data, DOCX)
    changed = extract_document(word(paragraph('Обновлённый текст')), DOCX)
    assert changed['blocks'][0]['id'] != result['blocks'][0]['id']


def test_word_numbering_and_inline_breaks():
    numbering = f'<w:numbering xmlns:w="{W[1:-1]}"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
    item = '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:hyperlink><w:r><w:t>Проверка</w:t><w:br/><w:t>документа</w:t></w:r></w:hyperlink></w:p>'
    result = extract_document(word(item + item, **{'word/numbering.xml':numbering}), DOCX)
    assert [b['text'] for b in result['blocks']] == ['1. Проверка\nдокумента','2. Проверка\nдокумента']


def test_partial_docx_warns_about_footnotes_and_tracked_changes():
    result = extract_document(word('<w:p><w:ins><w:r><w:t>Новая функция ИИ</w:t></w:r></w:ins><w:del><w:r><w:delText>Удалённое</w:delText></w:r></w:del></w:p>', **{'word/footnotes.xml':'<footnotes/>'}), DOCX)
    assert result['status'] == 'partial'
    assert result['blocks'][0]['text'] == 'Новая функция ИИ'
    assert {'additional_parts','tracked_changes'} <= {w['code'] for w in result['warnings']}


def test_pdf_text_anchors_and_blank_page_coverage():
    result = extract_document(pdf('First clause','', 'Third clause'), 'application/pdf')
    assert result['status'] == 'partial'
    assert [b['locator']['page'] for b in result['blocks']] == [1, 3]
    assert result['stats']['units_total'] == 3 and result['stats']['units_with_text'] == 2
    assert extract_document(pdf(''), 'application/pdf')['status'] == 'needs_ocr'
    assert extract_document(pdf('Readable clause'), 'application/pdf')['status'] == 'ready'


def test_limits_fail_without_publishing_truncated_text(monkeypatch):
    import knowledge_service.extraction as module
    monkeypatch.setattr(module, 'MAX_BLOCKS', 1)
    with pytest.raises(ExtractionError) as error:
        extract_document(word(paragraph('one') + paragraph('two')), DOCX)
    assert error.value.code == 'limit_exceeded'
    monkeypatch.setattr(module, 'MAX_CHARACTERS', 2)
    with pytest.raises(ExtractionError):
        extract_document(word(paragraph('long')), DOCX)


def test_missing_numbering_is_partial_not_fabricated():
    result = extract_document(word('<w:p><w:pPr><w:numPr><w:numId w:val="5"/></w:numPr></w:pPr><w:r><w:t>Обязанность</w:t></w:r></w:p>'), DOCX)
    assert result['status'] == 'partial'
    assert result['blocks'][0]['text'] == 'Обязанность'
    assert result['warnings'][0]['code'] == 'unresolved_numbering'


@pytest.mark.parametrize('data,media', [(b'private text','text/plain'),(b'%PDF-damaged','application/pdf'),(b'bad archive',DOCX)])
def test_invalid_sources_are_safe_errors(data, media):
    with pytest.raises(ExtractionError) as error:
        extract_document(data, media)
    assert 'private' not in str(error.value)


def test_docx_entities_are_never_expanded():
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('[Content_Types].xml','<Types/>')
        archive.writestr('word/document.xml','<!DOCTYPE document [<!ENTITY secret "private">]><document>&secret;</document>')
    with pytest.raises(ExtractionError):
        extract_document(out.getvalue(), DOCX)


def test_strict_ooxml_docx_is_readable_with_original_anchors():
    out = io.BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        archive.writestr('[Content_Types].xml','<Types/>')
        archive.writestr('word/document.xml','<w:document xmlns:w="http://purl.oclc.org/ooxml/wordprocessingml/main"><w:body>'+paragraph('Функция в строгом DOCX')+'</w:body></w:document>')
    result = extract_document(out.getvalue(),DOCX)
    assert result['status'] == 'ready'
    assert result['blocks'][0]['text'] == 'Функция в строгом DOCX'
    assert result['blocks'][0]['locator']['part'] == 'word/document.xml'


def test_image_page_with_text_footer_is_partial_not_complete():
    writer=PdfWriter()
    reader=__import__('pypdf').PdfReader(io.BytesIO(pdf('Page footer')))
    writer.add_page(reader.pages[0])
    image=DecodedStreamObject()
    image.set_data(b'\xff\xff\xff')
    image.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Image'),
        NameObject('/Width'):NumberObject(1),NameObject('/Height'):NumberObject(1),
        NameObject('/ColorSpace'):NameObject('/DeviceRGB'),NameObject('/BitsPerComponent'):NumberObject(8)})
    writer.pages[0]['/Resources'][NameObject('/XObject')]=DictionaryObject({NameObject('/Im1'):writer._add_object(image)})
    out=io.BytesIO()
    writer.write(out)
    result=extract_document(out.getvalue(),'application/pdf')
    assert result['status']=='partial'
    assert 'pdf_graphics' in {warning['code'] for warning in result['warnings']}
