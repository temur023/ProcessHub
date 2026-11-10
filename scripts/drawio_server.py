"""Раздаёт редактор draw.io без Docker (scripts/start-drawio.ps1): папка и порт — аргументы.

Таблицу типов файлов Python на Windows берёт из реестра, и там .js бывает
text/plain — тогда браузер не запускает скрипты редактора. Поэтому типы заданы явно.
"""
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        '.html': 'text/html',
        '.js': 'text/javascript',
        '.mjs': 'text/javascript',
        '.css': 'text/css',
        '.svg': 'image/svg+xml',
        '.json': 'application/json',
        '.xml': 'application/xml',
        '.wasm': 'application/wasm',
    }


directory, port = sys.argv[1], int(sys.argv[2])
server = ThreadingHTTPServer(('127.0.0.1', port), partial(Handler, directory=directory))
print(f'draw.io: http://127.0.0.1:{port}/ ({directory})', flush=True)
server.serve_forever()
