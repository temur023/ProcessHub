"""Bounded plain-text derivative of Word 97–2003 originals."""
import os
from pathlib import Path
import subprocess
import tempfile

ANTIWORD = Path('/usr/bin/antiword')
CONVERTER_VERSION = 'antiword-0.37-18'
CONVERTER_TIMEOUT_SECONDS = 20
MAX_CONVERTED_BYTES = 8 * 1024 * 1024


def _converter_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_CONVERTED_BYTES, MAX_CONVERTED_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if os.uname().sysname == 'Linux':
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))


def convert_doc(content, error_type):
    if not ANTIWORD.is_file():
        raise error_type('converter_unavailable', 'Подготовка DOC недоступна: нужен локальный конвертер antiword. Оригинал сохранён; можно повторить подготовку после настройки сервиса')
    with tempfile.TemporaryDirectory(prefix='knowledge-doc-') as directory:
        source, output = Path(directory) / 'source.doc', Path(directory) / 'source.txt'
        source.write_bytes(content)
        # Fixed executable/options, no shell, no macros, no external-resource
        # resolver, no inherited DB/Nova/proxy credentials. No filename provided
        # by the user reaches the filesystem or the command line.
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                       'ANTIWORDHOME': '/usr/share/antiword'}
        try:
            with output.open('wb') as stream:
                completed = subprocess.run([str(ANTIWORD), '-m', 'UTF-8.txt', '-w', '0', str(source)],
                    stdout=stream, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                    cwd=directory, env=environment, shell=False, close_fds=True,
                    timeout=CONVERTER_TIMEOUT_SECONDS,
                    preexec_fn=_converter_limits if os.name == 'posix' else None)
        except subprocess.TimeoutExpired:
            raise error_type('conversion_timeout', 'Подготовка DOC превысила допустимое время. Проверьте структуру оригинала') from None
        except (OSError, subprocess.SubprocessError):
            raise error_type('conversion_failed', 'Не удалось подготовить DOC. Проверьте оригинал или добавьте редакцию DOCX') from None
        if output.stat().st_size >= MAX_CONVERTED_BYTES:
            raise error_type('limit_exceeded', 'Текст DOC превышает лимит подготовки. Разделите документ на отдельные документы')
        if completed.returncode != 0:
            raise error_type('conversion_failed', 'Не удалось подготовить DOC. Проверьте оригинал или добавьте редакцию DOCX')
        try:
            return output.read_bytes().decode('utf-8')
        except UnicodeDecodeError:
            raise error_type('conversion_encoding', 'Не удалось достоверно прочитать кодировку DOC. Сохраните новую редакцию в DOCX') from None


def extract_doc(content, result, error_type):
    text = convert_doc(content, error_type)
    result.warn('doc_derivative', 'DOC прочитан через локальный текстовый конвертер. Ссылки указывают на строки производного текста, а не на страницы оригинала; проверьте нумерацию, таблицы, исправления, колонтитулы, изображения и встроенные материалы по оригиналу.')
    lines = text.splitlines()
    start, parts, total = None, [], 0
    # Empty lines delimit converter output units, never asserted original Word
    # paragraphs. There is no fabricated pagination or table reconstruction.
    for number, line in enumerate([*lines, ''], 1):
        if line.strip():
            if start is None:
                start = number
            parts.append(line)
        elif start is not None:
            total += 1
            result.add('\n'.join(parts), {'format': 'doc', 'derivative': 'antiword-text-v1',
                'converter': CONVERTER_VERSION, 'line_start': start, 'line_end': number - 1},
                f'Текст DOC · строки {start}–{number - 1}')
            start, parts = None, []
    if not result.blocks:
        result.warn('no_readable_text', 'Читаемый текст DOC не найден. Проверьте оригинал; для изображений потребуется OCR.')
    return result.finish('partial' if result.blocks else 'needs_ocr', total, len(result.blocks))
