"""Службы Process Hub без root (тестовый стенд, processhub-user.sh): то же, что юниты
systemd из systemd/, но под учётной записью пользователя.

    python supervise.py run ПОРТ              все службы; упавшая перезапускается через 5 с,
                                              SIGTERM останавливает все
    python supervise.py exec api|documents …  одна команда с настройками службы
                                              (проверка базы, импорт данных, назначение администратора)
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent
LOGS = HOME / 'logs'
PY = sys.executable
# Как у служб systemd: только своё окружение, без чужих переменных и прокси.
BASE = {'LANG': 'C.UTF-8', **{key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'HOME', 'TMPDIR', 'SYSTEMROOT')
                              if key in os.environ}, 'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1'}


def read_env(name):
    """Файл настроек в формате EnvironmentFile: КЛЮЧ=значение, # — комментарий."""
    values = {}
    for line in (HOME / 'etc' / f'{name}.env').read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        values[key.strip()] = value
    return values


def settings(kind):
    if kind == 'api':
        return HOME / 'backend', {**BASE, 'SQB_KNOWLEDGE_URL': 'http://127.0.0.1:8010',
                                  'SQB_CANVAS_CACHE': str(HOME / 'state' / 'canvas_cache'), **read_env('api')}
    return HOME / 'knowledge', {**BASE, **read_env('documents')}


def services(port):
    uvicorn = [PY, '-m', 'uvicorn']
    api_dir, api_env = settings('api')
    docs_dir, docs_env = settings('documents')
    worker_env = {key: value for key, value in docs_env.items() if key not in ('NOVA_API_KEY', 'NOVA_BASE_URL')}
    return {
        'api': (uvicorn + ['app.main:app', '--host', '127.0.0.1', '--port', '8000',
                           '--proxy-headers', '--forwarded-allow-ips', '127.0.0.1'], api_dir, api_env),
        'documents': (uvicorn + ['knowledge_service.main:app', '--host', '127.0.0.1', '--port', '8010'], docs_dir, docs_env),
        'documents-worker': ([PY, '-m', 'knowledge_service.worker'], docs_dir, worker_env),
        'documents-analysis': ([PY, '-m', 'knowledge_service.analysis_worker'], docs_dir, docs_env),
        # Единственная служба, видимая из сети: страницы, draw.io и /api/.
        'web': (uvicorn + ['front:app', '--host', '0.0.0.0', '--port', port, '--no-access-log'],
                HOME, {**BASE, 'PROCESSHUB_HOME': str(HOME)}),
    }


def say(text):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), text, flush=True)


def run(port):
    LOGS.mkdir(exist_ok=True)
    (HOME / 'state' / 'canvas_cache').mkdir(parents=True, exist_ok=True)
    (HOME / 'state' / 'supervisor.pid').write_text(str(os.getpid()))
    table = services(port)
    children, start_at, stopping = {}, dict.fromkeys(table, 0.0), []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))
    signal.signal(signal.SIGINT, lambda *_: stopping.append(True))
    say(f'запуск служб, веб-интерфейс на порту {port}')
    while not stopping:
        now = time.monotonic()
        for name, (argv, cwd, env) in table.items():
            child = children.get(name)
            if child is not None and child.poll() is None:
                continue
            if child is not None:
                # Без базы API не стартует — пробуем снова, как Restart=on-failure у systemd.
                say(f'{name} завершилась (код {child.returncode}), перезапуск через 5 с — см. logs/{name}.log')
                children[name] = None
                start_at[name] = now + 5
            if now >= start_at[name]:
                with (LOGS / f'{name}.log').open('ab') as log:
                    children[name] = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                                      stdout=log, stderr=subprocess.STDOUT)
        time.sleep(1)
    say('остановка служб')
    running = [child for child in children.values() if child is not None and child.poll() is None]
    for child in running:
        child.terminate()
    deadline = time.monotonic() + 15
    for child in running:
        try:
            child.wait(max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
    (HOME / 'state' / 'supervisor.pid').unlink(missing_ok=True)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == 'run':
        run(sys.argv[2])
    elif len(sys.argv) > 3 and sys.argv[1] == 'exec' and sys.argv[2] in ('api', 'documents'):
        cwd, env = settings(sys.argv[2])
        sys.exit(subprocess.run(sys.argv[3:], cwd=cwd, env=env).returncode)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
