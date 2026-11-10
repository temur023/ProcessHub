"""Комплект Process Hub для сервера Linux без Docker и без интернета.

Собирается на компьютере разработчика (нужны интернет через прокси, Node.js и Python):
    python deploy/linux/build_bundle.py [--skip-web]

Результат — transfer/processhub-<версия>.tar.gz: код служб, собранный фронтенд,
draw.io, Python 3.13 для Linux и пакеты Python. На сервере его распаковывают и
запускают install.sh (см. deploy/linux/README.md).
"""
import argparse
import hashlib
import io
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = ROOT / 'transfer'
CACHE = OUT / 'build-cache'

# Python для Linux целиком (python-build-standalone): сервер не зависит от своей версии Python.
PYTHON_URL = ('https://github.com/astral-sh/python-build-standalone/releases/download/20261026/'
              'cpython-3.13.18%2B20261026-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz')
PYTHON_SHA256 = 'df15fe60d3ff55bf9dcf25824b5572756bb8816ff93865a273d3301d7066a8b7'
WHEEL_TARGET = ['--platform', 'manylinux2014_x86_64', '--platform', 'manylinux_2_28_x86_64',
                '--python-version', '3.13', '--implementation', 'cp', '--only-binary=:all:']

# Как в .dockerignore бэкенда: карты живут в базе, локальные копии хранилища не нужны.
SKIP_NAMES = {'__pycache__', 'canvas_cache'}
SKIP_PREFIXES = ('process_store.json',)
DEPLOY_FILES = ['install.sh', 'README.md', 'processhub.nginx.conf', 'processhub-api.nginx.conf',
                'api.env.example', 'documents.env.example',
                'data_import.py', 'processhub-user.sh', 'supervise.py', 'front.py']


def run(command, cwd=ROOT):
    print('>', ' '.join(map(str, command)), flush=True)
    subprocess.run(command, cwd=cwd, check=True, shell=sys.platform == 'win32' and command[0] == 'npm')


def version():
    sha = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return time.strftime('%Y%m%d-%H%M') + '-' + (sha or 'nogit') + ('-dirty' if dirty else '')


def requirements():
    """Одно окружение на все службы: версии в двух requirements.txt должны совпадать."""
    pins = {}
    for path in (ROOT / 'backend' / 'requirements.txt', ROOT / 'knowledge' / 'requirements.txt'):
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            name = line.split('==')[0].split('[')[0].strip().lower()
            if pins.setdefault(name, line) != line:
                sys.exit(f'Разные версии {name} в backend и knowledge: {pins[name]} и {line}')
    return '\n'.join(pins.values()) + '\n'


def python_archive():
    target = CACHE / PYTHON_URL.rsplit('/', 1)[1].replace('%2B', '+')
    if not target.is_file():
        CACHE.mkdir(parents=True, exist_ok=True)
        print('> скачиваю', PYTHON_URL, flush=True)
        # Прокси банка подменяет сертификаты своим, без Authority Key Identifier: строгую
        # проверку X.509 из Python 3.13 он не проходит. Цепочка всё равно проверяется,
        # а файл — по контрольной сумме ниже.
        context = ssl.create_default_context()
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        with urllib.request.urlopen(PYTHON_URL, context=context) as response:
            target.with_suffix('.part').write_bytes(response.read())
        target.with_suffix('.part').replace(target)
    if hashlib.sha256(target.read_bytes()).hexdigest() != PYTHON_SHA256:
        target.unlink()
        sys.exit('Контрольная сумма Python не совпала: файл удалён, запустите сборку снова')
    return target


def wheels(reqs_file, folder):
    run([sys.executable, '-m', 'pip', 'download', '--quiet', '--dest', folder, *WHEEL_TARGET, '-r', reqs_file])
    # Та же установка, что выполнит install.sh на сервере: без интернета, только из папки.
    with tempfile.TemporaryDirectory() as target:
        run([sys.executable, '-m', 'pip', 'install', '--quiet', '--dry-run', '--ignore-installed', '--no-index',
             '--find-links', folder, '--target', target, *WHEEL_TARGET, '-r', reqs_file])


class Bundle:
    def __init__(self, path, prefix):
        self.tar = tarfile.open(path, 'w:gz', compresslevel=6)
        self.prefix = prefix
        self.now = int(time.time())

    def _info(self, name, size, mode):
        info = tarfile.TarInfo(f'{self.prefix}/{name}')
        info.size, info.mode, info.mtime = size, mode, self.now
        info.uid = info.gid = 0
        info.uname = info.gname = 'root'
        return info

    def data(self, name, content, mode=0o644):
        self.tar.addfile(self._info(name, len(content), mode), io.BytesIO(content))

    def text(self, name, source, mode=0o644):
        # Рабочее дерево на Windows хранит CRLF; на сервере bash и systemd ждут LF.
        self.data(name, source.read_bytes().replace(b'\r\n', b'\n'), mode)

    def tree(self, name, source):
        for path in sorted(source.rglob('*')):
            relative = path.relative_to(source)
            if path.is_dir() or SKIP_NAMES.intersection(relative.parts) or path.suffix in ('.pyc', '.tmp') \
                    or path.name.startswith(SKIP_PREFIXES):
                continue
            info = self._info(f'{name}/{relative.as_posix()}', path.stat().st_size, 0o644)
            with path.open('rb') as stream:
                self.tar.addfile(info, stream)

    def close(self):
        self.tar.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--skip-web', action='store_true', help='не пересобирать фронтенд, взять готовую app/dist')
    args = parser.parse_args()

    drawio = ROOT / 'drawio-webapp'
    if not (drawio / 'index.html').is_file():
        sys.exit('Нет drawio-webapp/index.html: распакуйте редактор draw.io (scripts/start-drawio.ps1 делает это сам)')
    if not args.skip_web:
        run(['npm', 'run', 'build'], cwd=ROOT / 'app')
    web = ROOT / 'app' / 'dist'
    if not (web / 'index.html').is_file():
        sys.exit('Нет app/dist/index.html: фронтенд не собран')

    name = 'processhub-' + version()
    OUT.mkdir(exist_ok=True)
    archive = OUT / (name + '.tar.gz')
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        reqs = requirements()
        (temp / 'requirements.txt').write_text(reqs, encoding='utf-8')
        wheels(temp / 'requirements.txt', temp / 'wheels')
        python = python_archive()

        bundle = Bundle(archive, name)
        bundle.data('VERSION', (name.removeprefix('processhub-') + '\n').encode())
        bundle.data('requirements.txt', reqs.encode())
        for file in DEPLOY_FILES:
            bundle.text(file, HERE / file, 0o755 if file.endswith('.sh') else 0o644)
        for unit in sorted((HERE / 'systemd').iterdir()):
            bundle.text(f'systemd/{unit.name}', unit)
        bundle.tree('backend/app', ROOT / 'backend' / 'app')
        bundle.tree('knowledge/knowledge_service', ROOT / 'knowledge' / 'knowledge_service')
        for file in ('service.py', 'convert.py'):
            bundle.text(f'document-preview/{file}', ROOT / 'document-preview' / file)
        bundle.tree('web', web)
        bundle.tree('drawio', drawio)
        bundle.tree('wheels', temp / 'wheels')
        bundle.data('python.tar.gz', python.read_bytes())
        bundle.close()

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(f'\nГотово: {archive}\n  размер {archive.stat().st_size / 1e6:.0f} МБ, SHA-256 {digest}')


if __name__ == '__main__':
    main()
