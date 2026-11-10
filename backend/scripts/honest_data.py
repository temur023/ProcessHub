"""Разовая правка сохранённых карт: убрать то, что разбор выдумывал, а не брал с карты.

Что меняется:

* ИТ-система «SQB CRM / Core» — запасной ответ разбора, когда угадать
  систему по словам не вышло. На картах банка её нет, а подписана она была у
  2818 шагов из 8041. Теперь у таких шагов система не указана.
* Подписи, которые разбор выдавал догадкой по словам (точное совпадение с его
  ответами — «AML/CFT Moduli», «SWIFT Alliance»…), помечаются
  ``systemGuessed``: догадку видно на карточке шага и в панели карты.
* Написания систем сводятся по справочнику (``app.services.it_systems``):
  «IABS» и «iABS», «Crobs» и «CROBS», «ЭҲА» и «EHA».
* Паспорт: владелец-заглушка «Департамент бизнес-процессов…», подразделение,
  совпадающее с первой дорожкой карты, и описание-шаблон «Импортирован из
  файла…» — тоже ответы разбора по умолчанию. Они очищаются: паспорт
  заполняют на платформе.

Каждая изменённая карта сохраняется новой версией истории (источник
«migration», с примечанием) — прежнюю можно вернуть из «Истории версий».
Повторный запуск ничего не меняет.

Запуск (база Docker)::

    cd backend
    SQB_DATABASE_URL=postgresql://sqb:sqb@localhost:5433/sqb .venv/bin/python -m scripts.honest_data --dry-run
    SQB_DATABASE_URL=postgresql://sqb:sqb@localhost:5433/sqb .venv/bin/python -m scripts.honest_data
"""
from __future__ import annotations

import sys
from collections import Counter
from typing import Iterable

from app.models.process import BusinessProcess
from app.services.it_systems import canonical
from app.services.store import get_store

FALLBACK_SYSTEM = 'SQB CRM / Core'
#: Ответы прежнего ``detect_system`` — так разбор подписывал шаг по словам в названии.
OLD_GUESSES = {
    'PIX RPA', 'NIBBD / ЦБ РУз', 'EHA Dasturi', 'EDO / Didox (ЭЦП)', 'AML/CFT Moduli', 'iABS (ЦФТ-Банк)',
    'API Soliq (ГНК)', 'API KATM', 'ЕПИГУ / ЕГРПО', 'Dragle BI', 'CROBS Risk Engine', 'Zoom Video Conf',
    'SWIFT Alliance',
}
OWNER_PLACEHOLDER = 'Департамент бизнес-процессов АКБ «Узпромстройбанк»'
#: Начало описания, которое разбор писал вместо описания процесса.
DESCRIPTION_PLACEHOLDERS = ('Импортирован из файла draw.io:', 'Импортирован из файла BPMN:')
NOTE = 'честные данные: без системы-заглушки и владельца-заглушки, написания систем по справочнику'


def _pages(process: BusinessProcess) -> Iterable[BusinessProcess]:
    return [process, *process.subProcesses, *process.variants]


def fix(process: BusinessProcess, tally: Counter) -> bool:
    """Правит карту на месте; ``True`` — что-то изменилось."""
    changed = False
    for page in _pages(process):
        for node in page.nodes:
            # Все фигуры, а не только шаги: прежний разбор подписывал систему
            # и событиям, и шлюзам — заглушка лежала и на них.
            if not node.system:
                continue
            system, guessed = node.system, node.systemGuessed
            if node.system == FALLBACK_SYSTEM:
                system, guessed = None, False
                tally['system: заглушка убрана'] += 1
            else:
                if node.system in OLD_GUESSES and not node.systemGuessed:
                    guessed = True
                    tally['system: помечена как догадка'] += 1
                system = canonical(node.system)
                if system != node.system:
                    tally['system: написание по справочнику'] += 1
            if (system, guessed) != (node.system, node.systemGuessed):
                node.system, node.systemGuessed = system, guessed
                changed = True
        passport = page.passport
        if passport.owner == OWNER_PLACEHOLDER:
            passport.owner = ''
            tally['паспорт: владелец-заглушка'] += 1
            changed = True
        if passport.description.startswith(DESCRIPTION_PLACEHOLDERS):
            passport.description = ''
            tally['паспорт: описание-шаблон'] += 1
            changed = True
        if page.lanes and passport.department and passport.department == page.lanes[0].name:
            passport.department = ''
            tally['паспорт: подразделение = первая дорожка'] += 1
            changed = True
    return changed


def main(dry_run: bool) -> None:
    store = get_store()
    tally: Counter = Counter()
    changed_maps = 0
    for info in store.infos():
        process = store.get(info.id)
        if process is None or not fix(process, tally):
            continue
        changed_maps += 1
        if not dry_run:
            store.save(process, 'migration', expected_revision=info.revision, note=NOTE)
    for key, count in sorted(tally.items()):
        print(f'{key}: {count}')
    print(f"{'Будет изменено' if dry_run else 'Изменено'} карт: {changed_maps}")


if __name__ == '__main__':
    main(dry_run='--dry-run' in sys.argv)
