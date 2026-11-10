"""Navigation over exact source fragments; no summarization or legal approval."""
import hashlib
import re
import unicodedata

STRUCTURE_VERSION = 'chapters-v1'
LABELS = {'overview': 'Общие положения', 'duties': 'Функции и задачи', 'obligations': 'Обязанности',
          'rights': 'Полномочия и права', 'responsibility': 'Ответственность',
          'requirements': 'Требования к должности', 'final': 'Заключительные положения',
          'source': 'Заголовок и реквизиты', 'other': 'Другие разделы'}
CHAPTER = re.compile(r'^\s*(?:\d+\s*[-–—]\s*(?:bob|боб)\b|(?:глава|раздел)\s+\d+\b)', re.I)
# A source can use "2. Menejerning vazifalari" as a standalone title.
# Exact title endings exclude ordinary numbered sentences about duties.
COMPACT_TITLE = re.compile(r'^\s*\d+[.)]\s+[^.;:!?\n]{1,120}\b(?:vazifalari|funksiyalari|majburiyatlari|huquqlari|javobgarligi|вазифалари|функциялари|мажбуриятлари|ҳуқуқлари|жавобгарлиги|общие положения|функции и задачи|обязанности|ответственность)\s*$', re.I)
# Russian titles usually start with the section word itself: "2. Функции и
# задачи", "II. Должностные обязанности", or an unnumbered capitalised title.
# A numbered list item ("5. Выполняет иные функции") starts with a verb.
_RU_SECTION = (r'(?:основные\s+|должностные\s+)?(?:функции|задачи|обязанности|права|полномочия|ответственность|'
               r'общие\s+положения)(?:\s*(?:,|и)\s*(?:функции|задачи|обязанности|права|полномочия|ответственность))*')
RU_TITLE = re.compile(r'^\s*(?:\d{1,2}|[ivx]{1,4})\.\s*' + _RU_SECTION + r'(?:\s+[а-яё]+){0,3}\s*$')
RU_CAPS_TITLE = re.compile(r'^\s*' + _RU_SECTION + r'(?:\s+[а-яё]+){0,3}\s*$')
TOKENS = (
    ('overview', ('umumiy qoid', 'умумий қоид', 'умумий коид', 'общие положения')),
    ('rights', ('huquq', 'ҳуқуқ', 'хукук', 'полномоч', 'права')),
    ('responsibility', ('javobgarl', 'жавобгар', 'ответственност')),
    ('obligations', ('majburiyat', 'мажбурият', 'обязанност')),
    ('duties', ('vazifa', 'funksiya', 'вазифа', 'функция', 'функци')),
    ('requirements', ('malaka', 'talab', 'малака', 'талаб', 'требован')),
    ('final', ('yakuniy', 'якуний', 'заключитель')),
)


def _normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).lower().split())


def _heading(text, kind):
    normalized = _normalized(text)
    russian = len(normalized) <= 80 and (RU_TITLE.match(normalized) or (
        text.strip().isupper() and RU_CAPS_TITLE.match(normalized)))
    if len(normalized) > 240 or not (CHAPTER.match(normalized) or COMPACT_TITLE.fullmatch(normalized)
                                     or russian or kind == 'heading'):
        return None
    for key, words in TOKENS:
        if any(word in normalized for word in words):
            return key
    return 'other' if CHAPTER.match(normalized) else None


def _fragments(text):
    """Split source list items, never rewrite them or infer new duties.

    Wrapped PDF lines stay together. Semicolon-terminated lines and new
    numbered/bulleted paragraphs provide conservative item boundaries.
    """
    chunks, parts, offset, start = [], [], 0, 0
    for line in text.splitlines(keepends=True):
        numbered = re.match(r'^\s*(?:\d+[.)]|[•●]|[-–]\s)\s*\S', line)
        if parts and numbered and ''.join(parts).strip():
            chunks.append((start,''.join(parts))); parts=[]; start=offset
        parts.append(line); offset += len(line)
        if line.rstrip().endswith(';'):
            chunks.append((start,''.join(parts))); parts=[]; start=offset
    if parts:
        tail = ''.join(parts)
        if chunks and not tail.strip():
            previous_start, previous_text = chunks[-1]
            chunks[-1] = (previous_start, previous_text + tail)
        else:
            chunks.append((start,tail))
    return [(start,chunk) for start,chunk in chunks if chunk]


def build_structure(source):
    """Every nonempty source line is retained once, with its original locator.

    A page may contain several chapters; paragraph/page IDs remain stable.
    Numbered sentences mentioning 'duties' do not become chapter headings.
    """
    sections, current = [], None
    def section(key, heading=None):
        nonlocal current
        current = {'id': f'{key}-{len(sections)}', 'kind': key, 'title': LABELS[key],
                   'source_heading': heading, 'blocks': []}
        sections.append(current)
    section('source')
    for block in source['blocks']:
        lines = block['text'].splitlines(keepends=True)
        start, parts = 0, []
        def flush():
            if not parts:
                return
            text = ''.join(parts)
            for fragment_offset, fragment in _fragments(text):
                current['blocks'].append({'id': f"{block['id']}:{start}:{fragment_offset}",
                    'source_block_id': block['id'], 'text': fragment,
                    'label': block['label'], 'locator': block['locator'],
                    'line_offset': start, 'fragment_offset': fragment_offset})
        for offset, line in enumerate(lines):
            heading = _heading(line, block['kind'] if len(lines) == 1 else 'paragraph')
            if heading:
                flush(); parts = []; start = offset
                section(heading, line.strip())
            # Retain headings in the citation text too; none are invented.
            parts.append(line)
        flush()
    sections = [item for item in sections if item['blocks']]
    return {'structure_version': STRUCTURE_VERSION, 'source_id': source['id'],
            'content_hash': source['content_hash'], 'parser_version': source['parser_version'],
            'status': source['status'], 'warnings': source['warnings'], 'sections': sections,
            'chapter_count': sum(item['kind'] not in ('source', 'other') for item in sections),
            'review_required': True}


def build_functions(source, kinds=('duties',)):
    """Only function chapters are exposed in the repository's navigation.

    Keep source paragraphs together, including their original list markers.
    The source text ledger remains internal; it is never an original viewer.
    """
    result = build_structure(source)
    originals = {block['id']: block for block in source['blocks']}
    chapters = []
    for section in result['sections']:
        if section['kind'] not in kinds:
            continue
        paragraphs = []
        for fragment in section['blocks']:
            if paragraphs and paragraphs[-1]['source_block_id'] == fragment['source_block_id']:
                paragraphs[-1]['text'] += fragment['text']
            else:
                paragraphs.append({**fragment})
        for paragraph in paragraphs:
            original = originals[paragraph['source_block_id']]
            if 'runs' in original:
                start = sum(len(line) for line in original['text'].splitlines(keepends=True)[:paragraph['line_offset']]) + paragraph['fragment_offset']
                end, offset, runs = start + len(paragraph['text']), 0, []
                for run in original['runs']:
                    right = offset + len(run['text'])
                    if right > start and offset < end:
                        runs.append({**run, 'text': run['text'][max(0, start - offset):min(len(run['text']), end - offset)]})
                    offset = right
                paragraph['runs'] = runs
        chapters.append({**section, 'blocks': paragraphs})
    return {**result, 'structure_version': 'functions-v2', 'sections': chapters,
            'chapter_count': len(chapters)}


def docx_functions(content, source_id, kinds=('duties',)):
    """Read function clauses from the immutable file, preserving tracked text.

    No prepared-source history is replaced and no Word edit is resolved here.
    """
    from .extraction import _docx, _Result
    source = _docx(content, _Result(content), preserve_revisions=True)
    source['id'] = source_id
    source['content_hash'] = hashlib.sha256(content).hexdigest()
    source['parser_version'] = 'functions-docx-v1'
    result = build_functions(source, kinds)
    result['has_revisions'] = any(run.get('change') for section in result['sections']
                                 for block in section['blocks'] for run in block.get('runs', []))
    return result


def doc_functions(content, converted, source_id, kinds=('duties',)):
    result = docx_functions(converted, source_id, kinds)
    result['content_hash'] = hashlib.sha256(content).hexdigest()
    result['parser_version'] = 'functions-doc-v1'
    for section in result['sections']:
        for block in section['blocks']:
            block['locator'] = {**block['locator'], 'format': 'doc', 'derived_format': 'docx'}
    return result
