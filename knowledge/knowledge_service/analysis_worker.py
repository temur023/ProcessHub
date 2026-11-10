"""Analysis worker: the only process that holds the Nova key.

Runs one analysis at a time (the Gateway key has a concurrency limit),
renews its lease on every progress step and stops when a run is cancelled.
Never logs document text, prompts or answers.
"""
import argparse
import asyncio
import signal
import tempfile
import time
from pathlib import Path

from fastapi import HTTPException

from .analysis_engine import AnalysisFailed, Cancelled, NovaCaller, analyze_instruction
from .analysis_store import claim_run, complete_run, fail_run, renew, run_inputs
from .config import Settings
from .nova import NovaClient, NovaError
from .role_library_routes import functions_structure
from .store import Store, migrate

LEASE_SECONDS = 900
# Временная папка системы: /tmp в контейнере, %TEMP% в Windows без Docker.
HEALTH_FILE = Path(tempfile.gettempdir()) / 'knowledge-analysis-health'
NOVA_MESSAGES = {
    'not_configured': 'Nova не подключена: укажите адрес, ключ и базу знаний',
    'timeout': 'Nova не ответила за 300 секунд. Повторите анализ позже',
    'unreachable': 'Нет сетевого доступа к Nova. Проверьте доступ сервера к Gateway',
    'http_401': 'Nova отклонила ключ доступа (401). Проверьте NOVA_API_KEY',
    'http_403': 'У ключа нет доступа к базе знаний (403). Запросите доступ в ДИИ',
    'http_429': 'Nova перегружена запросами. Повторите анализ позже',
    'http_502': 'Поиск или модель Nova временно недоступны (502). Повторите позже',
}


def instruction_functions(file, source):
    """Function items as currently in force: proposed Word insertions are left
    out and proposed deletions stay, because unaccepted edits are not yet the
    instruction's text. Without a function chapter, duties are used instead."""
    if source is None or source['status'] not in ('ready', 'partial'):
        raise AnalysisFailed('not_prepared', 'Текст инструкции ещё не подготовлен. Дождитесь подготовки файла '
                             'или запустите её во вкладке «Оригинал»')
    notes = []
    try:
        structure = functions_structure(file, source)
        if not structure['sections']:
            structure = functions_structure(file, source, kinds=('obligations',))
            if structure['sections']:
                notes.append('Раздел функций не найден; проанализирован раздел обязанностей.')
    except HTTPException as error:
        raise AnalysisFailed('functions_failed', str(error.detail)) from None
    functions, revised = [], 0
    for section in structure['sections']:
        for block in section['blocks']:
            runs = block.get('runs')
            text = ''.join(run['text'] for run in runs if run.get('change') != 'inserted') if runs else block['text']
            revised += bool(runs and any(run.get('change') for run in runs))
            text = ' '.join(text.split())
            if len(text) >= 15 and not _is_heading(text, section.get('source_heading')):
                functions.append({'text': text, 'label': block.get('label'), 'locator': block.get('locator'),
                                  'source_block_id': block.get('source_block_id')})
    if revised:
        notes.append(f'В {revised} пунктах есть непринятые исправления Word; анализ выполнен по действующему '
                     'тексту без предложенных правок.')
    return functions, notes


def _is_heading(text, heading):
    return bool(heading) and ' '.join(heading.split()) == text


async def execute(db, settings, run, nova=None, sleep=asyncio.sleep):
    document, role, file, source = run_inputs(db, run)
    if document['deleted_at'] is not None:
        return fail_run(db, run, 'document_archived', 'Документ перемещён в архив')
    functions, notes = instruction_functions(file, source)
    context = {'functions': functions, 'title': document['title'],
               'department_name': document['department_name'], 'position_name': role['name'] if role else None,
               'binding_document': document['nova_regulation_document']}
    own_client = nova is None
    nova = nova or NovaClient(settings)
    caller = NovaCaller(nova, 'ph-' + run['id'].hex[:12], sleep=sleep)

    def progress(stage, done, total):
        if not renew(db, run, stage, done, total):
            raise Cancelled()
        _health()

    try:
        result = await analyze_instruction(context, caller, progress)
    finally:
        if own_client:
            await nova.close()
    result['warnings'] = notes + result['warnings']
    result['instruction'] = {'title': document['title'], 'filename': file['filename'],
                             'content_hash': file['content_hash']}
    return complete_run(db, run, result)


def process(db, settings, run, nova=None, sleep=asyncio.sleep):
    try:
        return asyncio.run(execute(db, settings, run, nova, sleep))
    except Cancelled:
        return False
    except AnalysisFailed as error:
        return fail_run(db, run, error.code, error.message)
    except NovaError as error:
        message = NOVA_MESSAGES.get(error.code, 'Nova вернула ошибку. Повторите анализ или обратитесь в ДИИ')
        if error.request_id:
            message += f' (X-Request-ID: {error.request_id})'
        return fail_run(db, run, 'nova_' + error.code, message)
    except Exception:
        # Unexpected failures end the run visibly instead of leaving it running.
        return fail_run(db, run, 'internal_error', 'Анализ прерван внутренней ошибкой. Повторите запуск')


def _health():
    HEALTH_FILE.write_text(str(time.time()))


def main():
    parser = argparse.ArgumentParser(description='Анализ инструкций через Nova')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--healthcheck', action='store_true')
    args = parser.parse_args()
    if args.healthcheck:
        try:
            ok = time.time() - float(HEALTH_FILE.read_text()) < 60
        except (OSError, ValueError):
            ok = False
        raise SystemExit(0 if ok else 1)
    settings = Settings.from_env()
    migrate(settings.database_url)
    db = Store(settings.database_url)
    shutdown = [False]
    signal.signal(signal.SIGTERM, lambda *_: shutdown.__setitem__(0, True))
    signal.signal(signal.SIGINT, lambda *_: shutdown.__setitem__(0, True))
    try:
        while not shutdown[0]:
            _health()
            try:
                run = claim_run(db, LEASE_SECONDS)
                if run:
                    process(db, settings, run)
            except Exception:
                print('Анализ временно недоступен; задания сохранены', flush=True)
                run = None
                time.sleep(5)
            if args.once:
                return
            if not run:
                time.sleep(2)
    finally:
        db.close()


if __name__ == '__main__':
    main()
