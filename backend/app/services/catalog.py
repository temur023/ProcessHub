"""Дерево пространств и карт (``app.models.catalog``): хранение и правила.

Хранится плоским списком элементов в хранилище (``app.services.store``:
таблица ``catalog_items`` в PostgreSQL; прежний ``app/data/catalog.json``
переносится туда при первом запуске). Номер «3.1», уровень и счётчики не
хранятся, а считаются по дереву при каждом чтении: перенос или удаление
элемента не оставляет устаревших номеров.

Правила дерева:

* карта лежит только в пространстве и сама ничего не содержит;
* пространство без родителя — пространство, с родителем — подпространство;
* процесс из хранилища лежит не больше чем в одной карте; процессы без карты
  показываются отдельно («Без пространства»);
* удалить можно только пустое пространство; удаление карты процесс не
  удаляет — он возвращается в «Без пространства».
"""
from __future__ import annotations

import json
import sys
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from app.models.catalog import Catalog, CatalogItem, CatalogNode, ItemKind, ProcessBrief, UnplacedProcess
from app.services.store import ProcessInfo, get_store

#: С какого уровня PIX элемент — карта процесса: 1 — пространство, 2 — департамент, 3 — процесс.
PIX_PROCESS_LEVEL = 3

#: Дерево на время одной операции — читается из хранилища заново каждый раз.
_items: Dict[str, CatalogItem] = {}
_lock = threading.RLock()


class CatalogError(Exception):
    """Нарушено правило дерева; ``status`` — код ответа API."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def reset() -> None:
    """Пустое дерево (тесты)."""
    with _lock:
        _items.clear()
        get_store().save_catalog([])


def _reload() -> None:
    """Дерево из хранилища — заново на каждую операцию.

    Раньше дерево читалось один раз и жило в памяти процесса. С базой, общей
    для нескольких процессов бэкенда (контейнер и локальный сервер, позже
    несколько рабочих), каждый показывал бы своё дерево, а запись дерева
    целиком стирала бы чужие правки. 560 элементов читаются за миллисекунды.
    """
    _items.clear()
    for raw in get_store().load_catalog():
        item = CatalogItem.model_validate(raw)
        _items[item.id] = item


def _save(*items: CatalogItem) -> None:
    """Записать только изменённые элементы: чужие правки соседних не затираются."""
    for item in items:
        _items[item.id] = item
    get_store().upsert_catalog_items([item.model_dump(mode='json') for item in items])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _children() -> Dict[Optional[str], List[CatalogItem]]:
    children: Dict[Optional[str], List[CatalogItem]] = defaultdict(list)
    for item in _items.values():
        children[item.parentId].append(item)
    for siblings in children.values():
        siblings.sort(key=lambda i: (i.position, i.createdAt, i.title))
    return children


def snapshot(processes: Mapping[str, ProcessInfo]) -> Catalog:
    """Всё дерево сверху вниз (родитель раньше детей) и процессы без места."""
    with _lock:
        _reload()
        children = _children()
        nodes: List[CatalogNode] = []

        def walk(parent_id: Optional[str], prefix: str, level: int) -> Tuple[int, int]:
            spaces = maps = 0
            for index, item in enumerate(children.get(parent_id, []), 1):
                number = '' if parent_id is None else (f'{prefix}.{index}' if prefix else str(index))
                info = processes.get(item.processId) if item.processId else None
                node = CatalogNode(**item.model_dump(), number=number, level=level,
                                   hasProcess=info is not None, process=info and _brief(info))
                nodes.append(node)
                node.spaceCount, node.mapCount = walk(item.id, number, level + 1)
                spaces += node.spaceCount + (item.kind == 'space')
                maps += node.mapCount + (item.kind == 'map')
            return spaces, maps

        walk(None, '', 1)
        placed = {item.processId for item in _items.values() if item.processId}
    unplaced = sorted(
        (UnplacedProcess(id=p.id, name=p.name, nodeCount=p.nodeCount, updatedDate=p.updatedDate,
                         **_brief(p).model_dump())
         for p in processes.values() if p.id not in placed),
        key=lambda p: p.name.lower())
    return Catalog(items=nodes, unplaced=unplaced)


def _brief(info: ProcessInfo) -> ProcessBrief:
    return ProcessBrief(stepCount=info.stepCount, errorCount=info.errorCount, warningCount=info.warningCount,
                        updatedAt=info.updatedAt, updatedBy=info.updatedBy, status=info.passportStatus,
                        stage=info.stage)


def holder(process_id: str) -> Optional[CatalogItem]:
    """Карта каталога, в которой лежит процесс, — или никакая."""
    with _lock:
        _reload()
        return next((item for item in _items.values() if item.processId == process_id), None)


def node(item_id: str, processes: Mapping[str, ProcessInfo]) -> CatalogNode:
    found = next((n for n in snapshot(processes).items if n.id == item_id), None)
    if found is None:
        raise CatalogError(404, f'Элемент «{item_id}» не найден')
    return found


# ── Правила ────────────────────────────────────────────────────────────────

def _get(item_id: str) -> CatalogItem:
    item = _items.get(item_id)
    if item is None:
        raise CatalogError(404, f'Элемент «{item_id}» не найден')
    return item


def _title(value: str) -> str:
    title = ' '.join(value.split())
    if not title:
        raise CatalogError(422, 'Название не может быть пустым')
    return title


def _check_parent(kind: ItemKind, parent_id: Optional[str]) -> None:
    if parent_id is None:
        if kind == 'map':
            raise CatalogError(422, 'Карта лежит в пространстве: укажите его')
        return
    if _get(parent_id).kind != 'space':
        raise CatalogError(422, 'Внутри карты ничего не лежит: выберите пространство')


def _check_process(process_id: str, processes: Mapping[str, ProcessInfo], own_id: Optional[str]) -> None:
    if process_id not in processes:
        raise CatalogError(422, f'Процесса «{process_id}» нет в хранилище')
    holder = next((i for i in _items.values() if i.processId == process_id and i.id != own_id), None)
    if holder is not None:
        raise CatalogError(409, f'Процесс уже лежит в карте «{holder.title}»')


def _next_position(parent_id: Optional[str]) -> int:
    return max((i.position for i in _items.values() if i.parentId == parent_id), default=-1) + 1


def create_item(parent_id: Optional[str], kind: ItemKind, title: str, process_id: Optional[str],
                processes: Mapping[str, ProcessInfo]) -> str:
    with _lock:
        _reload()
        _check_parent(kind, parent_id)
        if process_id is not None:
            if kind != 'map':
                raise CatalogError(422, 'Процесс кладётся в карту, а не в пространство')
            _check_process(process_id, processes, None)
        item = CatalogItem(id=f'cat_{uuid.uuid4().hex[:12]}', parentId=parent_id, kind=kind,
                           title=_title(title), position=_next_position(parent_id),
                           processId=process_id, createdAt=_now())
        _save(item)
        return item.id


def update_item(item_id: str, changes: Dict[str, object], processes: Mapping[str, ProcessInfo]) -> None:
    """``changes`` — только переданные поля (``model_dump(exclude_unset=True)``)."""
    with _lock:
        _reload()
        item = _get(item_id)
        updated = item.model_copy()
        if 'title' in changes:
            if changes['title'] is None:
                raise CatalogError(422, 'Название не может быть пустым')
            updated.title = _title(str(changes['title']))
        if 'parentId' in changes:
            parent_id = changes['parentId']
            _check_parent(item.kind, parent_id)
            ancestor = parent_id
            while ancestor is not None:
                if ancestor == item.id:
                    raise CatalogError(422, 'Нельзя перенести пространство внутрь самого себя')
                ancestor = _items[ancestor].parentId
            if parent_id != item.parentId:
                updated.parentId = parent_id
                updated.position = _next_position(parent_id)
        if 'favorite' in changes and changes['favorite'] is not None:
            updated.favorite = bool(changes['favorite'])
        if 'processId' in changes:
            if item.kind != 'map':
                raise CatalogError(422, 'Процесс кладётся в карту, а не в пространство')
            if changes['processId'] is not None:
                _check_process(str(changes['processId']), processes, item.id)
            updated.processId = changes['processId']
        _save(updated)


def delete_item(item_id: str) -> None:
    with _lock:
        _reload()
        item = _get(item_id)
        if any(i.parentId == item.id for i in _items.values()):
            raise CatalogError(409, f'В «{item.title}» есть подпространства или карты: сначала уберите их')
        del _items[item.id]
        get_store().delete_catalog_item(item.id)


# ── Перенос дерева из PIX ──────────────────────────────────────────────────

def import_pix_tree(rows: Sequence[Mapping[str, object]]) -> Tuple[int, int]:
    """Дерево карт PIX (ответ ``tree/dynamic``) -> пространства и карты; ``(создано, обновлено)``.

    Порядок строк среди соседей — порядок дерева PIX, из него и получаются те
    же номера. Карта процесса — строка уровня ``PIX_PROCESS_LEVEL`` и глубже
    без дочерних карт; остальное — пространства. Повторный перенос не
    плодит копий: элемент находится по карте PIX и обновляется.
    """
    with _lock:
        _reload()
        parents = {row.get('parentId') for row in rows if row.get('parentId')}
        by_pix = {item.pixId: item for item in _items.values() if item.pixId}
        positions: Dict[Optional[str], int] = defaultdict(int)
        created = updated = 0
        touched: List[CatalogItem] = []
        for row in sorted(rows, key=lambda r: int(r.get('depth') or 1)):
            pix_id = str(row['id'])
            parent = by_pix.get(str(row['parentId'])) if row.get('parentId') else None
            if row.get('parentId') and parent is None:
                raise CatalogError(422, f'В дереве PIX нет родителя карты «{row.get("title")}»')
            parent_id = parent.id if parent else None
            kind: ItemKind = ('map' if int(row.get('level') or 1) >= PIX_PROCESS_LEVEL and pix_id not in parents
                              else 'space')
            position = positions[parent_id]
            positions[parent_id] += 1
            title = _title(str(row.get('title') or ''))
            item = by_pix.get(pix_id)
            if item is None:
                item = CatalogItem(id=f'cat_{uuid.uuid4().hex[:12]}', parentId=parent_id, kind=kind, title=title,
                                   position=position, createdAt=str(row.get('createdOn') or _now()), pixId=pix_id)
                created += 1
            else:
                item = item.model_copy(update={'parentId': parent_id, 'title': title, 'position': position})
                updated += 1
            _items[item.id] = item
            by_pix[pix_id] = item
            touched.append(item)
        _save(*touched)
        return created, updated


if __name__ == '__main__':
    # venv\Scripts\python.exe -m app.services.catalog import-pix <дерево PIX .json>
    if len(sys.argv) != 3 or sys.argv[1] != 'import-pix':
        sys.exit('usage: python -m app.services.catalog import-pix <tree.json>')
    made, renewed = import_pix_tree(json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')))
    print(f'создано {made}, обновлено {renewed}; хранилище {type(get_store()).__name__}')
