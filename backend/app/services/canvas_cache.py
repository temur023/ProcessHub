"""Раскладка холста, сохранённая между открытиями карты.

Линии и подписи большой страницы считаются секунды (у «1.53» — 1600 фигур).
Раньше их считали заново при каждом открытии карты, а в режиме разработки —
дважды: React запускает эффект два раза. Здесь готовая раскладка хранится
под ключом от того, от чего она зависит:

* фигуры, связи и дорожки страницы — любая правка карты даёт новый ключ;
* версия кода раскладки — отпечаток исходников ``app/services`` и модели:
  поправили трассировщик — старые раскладки больше не находятся.

Хранится в памяти (последние страницы) и файлами в ``app/data/canvas_cache``
(или ``SQB_CANVAS_CACHE``) — переживает перезапуск бэкенда. Одновременные
запросы одной страницы ждут один расчёт, а не считают её каждый сам. После
сохранения карты её страницы считаются заранее, в фоне (``warm_up``).
"""
from __future__ import annotations

import hashlib
import os
import queue
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from pydantic import TypeAdapter

from app.models.process import BusinessProcess, CanvasPage, ProcessEdge, ProcessNode

#: Страница: сохранённая карта или то, что прислал холст.
Page = Union[BusinessProcess, CanvasPage]

_APP = Path(__file__).resolve().parent.parent
#: Сколько раскладок держать файлами; лишние — самые давние — удаляются.
MAX_FILES = 400
#: Сколько последних раскладок держать в памяти готовым JSON.
MAX_MEMORY = 16

_memory: 'OrderedDict[str, str]' = OrderedDict()
_lock = threading.Lock()
_inflight: Dict[str, threading.Lock] = {}


def _code_version() -> str:
    """Отпечаток кода, от которого зависит раскладка: сервисы и модель процесса."""
    digest = hashlib.sha256()
    for path in sorted([*(_APP / 'services').glob('*.py'), _APP / 'models' / 'process.py']):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


CODE_VERSION = _code_version()


def cache_dir() -> Path:
    return Path(os.environ.get('SQB_CANVAS_CACHE') or _APP / 'data' / 'canvas_cache')


_NODES = TypeAdapter(List[ProcessNode])
_EDGES = TypeAdapter(List[ProcessEdge])


def layout_key(page: Page, code_version: Optional[str] = None) -> str:
    """Ключ раскладки: фигуры, связи и дорожки страницы плюс версия кода.

    Одинаков для полной карты (прогрев после сохранения) и для страницы,
    присланной холстом (``CanvasPage``), — иначе прогрев был бы напрасным.
    """
    digest = hashlib.sha256((code_version or CODE_VERSION).encode())
    for part in (_NODES.dump_json(page.nodes), _EDGES.dump_json(page.edges), _NODES.dump_json(page.lanes)):
        digest.update(b'\n')
        digest.update(part)
    return digest.hexdigest()


def _remember(key: str, text: str) -> None:
    _memory[key] = text
    _memory.move_to_end(key)
    while len(_memory) > MAX_MEMORY:
        _memory.popitem(last=False)


def _read(key: str) -> Optional[str]:
    with _lock:
        if key in _memory:
            _memory.move_to_end(key)
            return _memory[key]
    path = cache_dir() / f'{key}.json'
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    with _lock:
        _remember(key, text)
    return text


def _mtime(path: Path) -> float:
    """Время файла кэша; файл, который уже вычистил параллельный запрос, — самый старый."""
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def _write(key: str, text: str) -> None:
    folder = cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    # Свой временный файл у каждого потока: две одновременные записи одного
    # ключа иначе делили один .tmp, и второй os.replace падал.
    tmp = folder / f'{key}.{threading.get_ident()}.tmp'
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, folder / f'{key}.json')
    files = sorted(folder.glob('*.json'), key=_mtime)
    for old in files[:max(len(files) - MAX_FILES, 0)]:
        old.unlink(missing_ok=True)


def cached_layout_json(page: Page, compute: Callable[[Page], str]) -> str:
    """JSON раскладки страницы: из памяти, с диска или посчитанный ``compute`` один раз."""
    key = layout_key(page)
    text = _read(key)
    if text is not None:
        return text
    with _lock:
        gate = _inflight.setdefault(key, threading.Lock())
    with gate:
        # Пока ждали, страницу мог посчитать соседний запрос.
        text = _read(key)
        if text is None:
            text = compute(page)
            with _lock:
                _remember(key, text)
            _write(key, text)
    with _lock:
        _inflight.pop(key, None)
    return text


#: Очередь прогрева: страницы считаются по одной, в одном фоновом потоке.
_jobs: 'queue.Queue[Tuple[List[BusinessProcess], threading.Event]]' = queue.Queue()
_worker: Optional[threading.Thread] = None


def as_process(page: Page) -> BusinessProcess:
    """Страница холста как карта для расчёта: ему нужны только фигуры, связи и дорожки."""
    if isinstance(page, BusinessProcess):
        return page
    return BusinessProcess.model_construct(nodes=page.nodes, edges=page.edges, lanes=page.lanes,
                                           subProcesses=[], variants=[])


def layout_json(page: Page) -> str:
    from app.services.bpmn_exporter import canvas_layout
    return canvas_layout(as_process(page)).model_dump_json()


def _work() -> None:
    while True:
        pages, done = _jobs.get()
        try:
            for page in pages:
                cached_layout_json(page, layout_json)
        except Exception:   # прогрев — подсказка, а не обязанность: откроют — посчитается
            pass
        finally:
            done.set()


def warm_up(process: BusinessProcess) -> threading.Event:
    """Посчитать заранее раскладки всех страниц сохранённой карты — в фоне.

    Сохраняют карту при импорте и после правки шага, а открывают сразу
    следом: пусть к этому моменту линии уже лежат готовыми. Посчитанное
    раньше не пересчитывается; страницу, которую уже считает открытая
    карта, прогрев дождётся, а не посчитает второй раз. Поток фоновый:
    остановке бэкенда он не мешает. Событие — когда страницы готовы.
    """
    global _worker
    pages = [p.model_copy(update={'subProcesses': [], 'variants': []})
             for p in (process, *process.subProcesses, *process.variants)]
    done = threading.Event()
    with _lock:
        if _worker is None:
            _worker = threading.Thread(target=_work, name='canvas-warm', daemon=True)
            _worker.start()
    _jobs.put((pages, done))
    return done


def warm_up_saved(store) -> None:
    """Посчитать раскладки всех сохранённых карт — после запуска бэкенда.

    Ключ раскладки включает версию кода, поэтому после обновления бэкенда
    прежние раскладки не находятся, и каждая большая карта при первом
    открытии ждала линии 2–3 с. Карты идут по одной, недавно изменённые —
    первыми; посчитанные раньше только читаются с диска.
    """
    infos = sorted(store.infos(), key=lambda i: i.updatedAt.timestamp() if i.updatedAt else 0, reverse=True)
    for info in infos:
        process = store.get(info.id)
        if process is not None:
            warm_up(process).wait()


def clear_memory() -> None:
    """Забыть раскладки в памяти (как после перезапуска); файлы остаются."""
    with _lock:
        _memory.clear()
