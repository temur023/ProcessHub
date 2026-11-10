"""Рисунок, сохранённый во встроенном draw.io, — в существующую карту платформы.

Встроенный редактор открывает выгрузку карты (``drawio_exporter``) и
возвращает исправленный файл. Из него берётся всё, что нарисовано: фигуры,
связи, дорожки, страницы, — тем же разбором, что и при импорте файла. Всё,
чего на рисунке нет, остаётся от карты: её id и имя, паспорт, реестр PIX,
метрики, а у страниц — их id, паспорт и реестр (страница узнаётся по имени).
Ставка часа дорожки — свойство платформы, в чужом файле её нет: у дорожки
без ставки она берётся у одноимённой дорожки прежней карты.

id страниц сохраняются намеренно: на них ссылаются фигуры-подпроцессы, по
ним холст держит открытую вкладку, а разбор нумерует страницы по их месту в
файле — и выгрузка ставит основную карту первой, так что номера поехали бы.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from app.models.process import BusinessProcess, ProcessNode
from app.services.drawio_exporter import page_keys


def apply_drawio(current: BusinessProcess, parsed: BusinessProcess) -> BusinessProcess:
    """Карта ``current`` с рисунком из ``parsed`` (разобранного файла draw.io)."""
    # Страница узнаётся по имени и номеру среди одноимённых: в файлах банка
    # бывают страницы с одинаковыми именами, и по одному имени они слипались.
    olds = [*current.subProcesses, *current.variants]
    old_pages = dict(zip(page_keys([(p.name, p) for p in olds]), olds))
    taken = {page.id for page in olds}
    rename: Dict[str, str] = {parsed.id: current.id}
    pages: List[BusinessProcess] = [*parsed.subProcesses, *parsed.variants]
    for page, key in zip(pages, page_keys([(p.name, p) for p in pages])):
        old = old_pages.get(key)
        if old is not None:
            new_id = old.id
            _keep_rates(page.lanes, old.lanes)
            page.passport = old.passport
            page.registry = old.registry
            page.miningMetrics = old.miningMetrics
        else:
            # Новая страница, добавленная в draw.io: номер — следующий свободный.
            index = 1
            while f'{current.id}_p{index}' in taken:
                index += 1
            new_id = f'{current.id}_p{index}'
        taken.add(new_id)
        rename[page.id] = new_id
        page.id = new_id
    _keep_rates(parsed.lanes, current.lanes)
    for page in [parsed, *pages]:
        for node in page.nodes:
            if node.calledSubProcessId in rename:
                node.calledSubProcessId = rename[node.calledSubProcessId]
    return current.model_copy(update={
        'nodes': parsed.nodes,
        'edges': parsed.edges,
        'lanes': parsed.lanes,
        'validation': parsed.validation,
        'subProcesses': parsed.subProcesses,
        'variants': parsed.variants,
        'mainPageName': parsed.mainPageName or current.mainPageName,
        'passport': current.passport.model_copy(update={'updatedDate': datetime.now().strftime('%Y-%m-%d')}),
    })


def _keep_rates(lanes: List[ProcessNode], old_lanes: List[ProcessNode]) -> None:
    """Ставки дорожек прежней карты — дорожкам без ставки с тем же именем."""
    rates = {' '.join(lane.name.lower().split()): lane.hourlyRate for lane in old_lanes if lane.hourlyRate is not None}
    for lane in lanes:
        if lane.hourlyRate is None:
            lane.hourlyRate = rates.get(' '.join(lane.name.lower().split()))
