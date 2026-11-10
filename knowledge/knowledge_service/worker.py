"""Separate, bounded worker. Parser children never receive DB or Nova keys."""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import signal
import tempfile
import time

import httpx

from .config import Settings
from .office_formats import DOC
from .preparation_store import claim_job, complete_job, enqueue_missing, fail_job, heartbeat, retry_failed
from .store import Store, migrate

TIMEOUT_SECONDS = 45
LEASE_SECONDS = 90
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MAX_DOCX_BYTES = 30 * 1024 * 1024
# Ошибки antiword, после которых DOC читается через копию DOCX от LibreOffice.
DOC_FALLBACK_CODES = {'converter_unavailable', 'conversion_failed', 'conversion_timeout', 'conversion_encoding',
                      'invalid_document'}
# Как часто искать файлы инструкций без подготовки, пока заданий нет.
BACKFILL_SECONDS = 300
# Временная папка системы: /tmp в контейнере, %TEMP% в Windows без Docker.
HEALTH_FILE = Path(tempfile.gettempdir()) / 'knowledge-worker-health'


def _parse_child(channel, content, media_type):
    # A private group lets timeout/cancellation also terminate a converter.
    if os.name == 'posix':
        os.setsid()
    # Local parsing needs no inherited credentials, including unrelated cloud
    # and proxy variables. Keep only locale/path/temp configuration.
    environment = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR',
                                                    'SYSTEMROOT', 'TEMP', 'TMP')
                   if key in os.environ}
    os.environ.clear()
    os.environ.update(environment)
    import logging
    logging.disable(logging.CRITICAL)
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (35, 35))
        if os.uname().sysname == 'Linux':
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    except (ImportError, OSError, ValueError):
        pass
    try:
        from .extraction import extract_document, ExtractionError
        result = {'result': extract_document(content, media_type)}
    except ExtractionError as error:
        result = {'error': {'code': error.code, 'message': error.message}}
    except BaseException:
        result = {'error': {'code': 'parser_failed', 'message': 'Подготовка не завершилась. Повторите обработку или проверьте оригинал'}}
    try:
        channel.send_bytes(json.dumps(result, ensure_ascii=False).encode())
    finally:
        channel.close()


def _doc_as_docx(content):
    """Копия DOCX от LibreOffice (document-preview), когда antiword не прочитал DOC."""
    url = os.environ.get('DOCUMENT_PREVIEW_URL', '').rstrip('/')
    deadline = time.monotonic() + 40
    while url and time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
                response = client.post(url + '/functions-source/doc', content=content,
                                       headers={'Content-Type': 'application/octet-stream'})
        except httpx.HTTPError:
            return None
        if response.status_code == 429:
            time.sleep(2)
            continue
        if response.status_code == 200 and response.content.startswith(b'PK') and len(response.content) <= MAX_DOCX_BYTES:
            return response.content
        return None
    return None


def run_job(db, job, timeout=TIMEOUT_SECONDS, stopped=lambda: False):
    content, media_type = bytes(job['content']), job['media_type']
    data = _parse(db, job, content, media_type, timeout, stopped)
    if media_type == DOC and isinstance(data, dict) and data.get('error', {}).get('code') in DOC_FALLBACK_CODES:
        converted = _doc_as_docx(content)
        if converted and heartbeat(db, job['id'], job['lease_token'], LEASE_SECONDS):
            retry = _parse(db, job, converted, DOCX, timeout, stopped)
            if not isinstance(retry, dict):
                return retry
            if 'result' in retry:
                retry['result']['warnings'].append({'code': 'doc_converted', 'message':
                    'DOC прочитан через копию DOCX, созданную LibreOffice. Проверьте нумерацию и таблицы по оригиналу.'})
                data = retry
    if not isinstance(data, dict):
        return data
    if 'error' in data:
        error = data['error']
        return fail_job(db, job['id'], job['lease_token'], error['code'], error['message'],
                        retryable=error['code'] == 'parser_failed')
    try:
        return complete_job(db, job['id'], job['lease_token'], data['result'])
    except ValueError:
        return fail_job(db, job['id'], job['lease_token'], 'invalid_result',
                        'Не удалось проверить результат подготовки. Повторите обработку')


def _parse(db, job, content, media_type, timeout, stopped):
    """Разбор в отдельном процессе: ответ разборщика, либо итог fail_job/False при сбое."""
    context = multiprocessing.get_context('spawn')
    reader, writer = context.Pipe(duplex=False)
    child = context.Process(target=_parse_child, args=(writer, content, media_type))
    child.start()
    writer.close()
    start, last_heartbeat = time.monotonic(), time.monotonic()
    try:
        while not stopped():
            now = time.monotonic()
            if now - start > timeout:
                return fail_job(db, job['id'], job['lease_token'], 'processing_timeout',
                                'Обработка превысила допустимое время. Проверьте размер и структуру документа', retryable=True)
            if now - last_heartbeat >= 5:
                if not heartbeat(db, job['id'], job['lease_token'], LEASE_SECONDS):
                    return False
                last_heartbeat = now
                _health()
            if reader.poll(0.1):
                try:
                    data = json.loads(reader.recv_bytes(maxlength=25 * 1024 * 1024))
                except (EOFError, OSError, ValueError):
                    return fail_job(db, job['id'], job['lease_token'], 'parser_failed',
                                    'Подготовка прервана. Повторите обработку', retryable=True)
                return data
            if not child.is_alive():
                return fail_job(db, job['id'], job['lease_token'], 'parser_failed',
                                'Процесс подготовки прерван. Повторите обработку', retryable=True)
        # A shutdown leaves a durable lease; another worker recovers it.
        return False
    finally:
        if os.name == 'posix':
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                if child.is_alive():
                    child.terminate()
        elif child.is_alive():
            child.terminate()
        child.join(timeout=2)
        if child.is_alive():
            if os.name == 'posix':
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    child.kill()
            else:
                child.kill()
            child.join(timeout=2)
        reader.close()


def _health():
    HEALTH_FILE.write_text(str(time.time()))


def main():
    parser = argparse.ArgumentParser(description='Фоновая подготовка документов Process Hub')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--healthcheck', action='store_true')
    args = parser.parse_args()
    if args.healthcheck:
        try:
            ok = time.time() - float(HEALTH_FILE.read_text()) < 30
        except (OSError, ValueError):
            ok = False
        raise SystemExit(0 if ok else 1)
    settings = Settings.from_env()
    migrate(settings.database_url)
    db = Store(settings.database_url)
    shutdown = [False]
    def stop(*_):
        shutdown[0] = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    backfill_at = 0.0
    try:
        retried = retry_failed(db)
        if retried:
            print(f'Повтор неудавшихся подготовок: {retried}', flush=True)
    except Exception:
        print('Повтор неудавшихся подготовок отложен', flush=True)
    try:
        while not shutdown[0]:
            try:
                if time.monotonic() >= backfill_at:
                    backfill_at = time.monotonic() + BACKFILL_SECONDS
                    queued = enqueue_missing(db)
                    if queued:
                        print(f'Поставлено на подготовку файлов без неё: {queued}', flush=True)
                job = claim_job(db, LEASE_SECONDS)
                _health()
                if job:
                    run_job(db, job, stopped=lambda: shutdown[0])
            except Exception:
                # Never log exception strings, connection URLs or document text.
                print('Сервис подготовки временно недоступен; задания сохранены', flush=True)
                if args.once:
                    raise SystemExit(1) from None
                time.sleep(2)
                continue
            if args.once:
                return
            if not job:
                time.sleep(1)
    finally:
        db.close()


if __name__ == '__main__':
    main()
