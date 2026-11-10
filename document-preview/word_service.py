"""Тот же сервис просмотра (service.py), но Word строит PDF вместо LibreOffice.

Только для компьютера без Docker, где установлен Microsoft Word. Маршруты,
проверки входа и очередь из одного слота берутся из service.py без изменений.
Запуск: python -m uvicorn word_service:app --app-dir document-preview --host 127.0.0.1 --port 8020
"""
from pathlib import Path
import subprocess
import tempfile

from fastapi import HTTPException

import service
from service import MAX_OUTPUT

SCRIPT = Path(__file__).with_name('word_convert.ps1')
TIMEOUT = 20


def render(content, suffix, target='pdf', mode='markup'):
    with tempfile.TemporaryDirectory(prefix='document-') as directory:
        root = Path(directory)
        original, output, pid_file = root / ('source' + suffix), root / ('preview.' + target), root / 'word.pid'
        original.write_bytes(content)
        try:
            completed = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
                 '-Source', str(original), '-Output', str(output), '-Target', target, '-Mode', mode, '-PidFile', str(pid_file)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            raise HTTPException(422, 'Просмотр не подготовлен за отведённое время. Скачайте исходный файл.') from None
        finally:
            # Word — отдельный COM-процесс: при зависании или ошибке он не
            # завершится вместе с PowerShell. Фильтр по имени не даст задеть
            # чужой процесс, получивший тот же номер.
            if pid_file.is_file():
                subprocess.run(['taskkill', '/F', '/FI', 'IMAGENAME eq WINWORD.EXE', '/PID', pid_file.read_text().strip()],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if completed.returncode != 0 or not output.is_file():
            raise HTTPException(422, 'Не удалось построить просмотр. Откройте исходный файл в Word.')
        size = output.stat().st_size
        if not 5 < size <= MAX_OUTPUT:
            raise HTTPException(422, 'Документ слишком большой для просмотра. Скачайте исходный файл.')
        result = output.read_bytes()
        if not result.startswith(b'%PDF-' if target == 'pdf' else b'PK'):
            raise HTTPException(422, 'Не удалось построить просмотр документа.')
        return result


service.render = render
app = service.app
