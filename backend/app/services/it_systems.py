"""Справочник ИТ-систем: одно написание на систему.

Система шага берётся с карты — хранилище данных, связанное с шагом пунктиром,
или свойство платформы. Аналитики подписывают одну и ту же систему по-разному:
в картах банка «IABS» и «iABS», «CROBS» и «Crobs», «EHA» и «ЭҲА», — и отчёт по
системам считал их разными. Здесь записаны написания, про которые точно
известно, что это одна система; всё остальное остаётся как подписано.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

#: Каноническое написание: его варианты (сравниваются без регистра и лишних пробелов).
ALIASES: Dict[str, Iterable[str]] = {
    'EHA': ('eha', 'eha dasturi', 'эҳа', 'эха', 'exa', 'еха'),
    'iABS': ('iabs', 'iabs (цфт-банк)', 'абс цфт', 'абс цфт-банк'),
    'CROBS': ('crobs', 'crobs risk engine'),
    'EDO': ('edo', 'эдо'),
    'SWIFT': ('swift', 'swift alliance'),
    'Zoom': ('zoom', 'zoom video conf'),
    'Jira': ('jira',),
}

_BY_ALIAS = {alias: name for name, aliases in ALIASES.items() for alias in (name.lower(), *aliases)}


def _key(text: str) -> str:
    return ' '.join(text.lower().split())


def canonical(system: Optional[str]) -> Optional[str]:
    """Системы шага через запятую — в написании справочника, без повторов; пусто — ``None``."""
    names: List[str] = []
    for part in (system or '').split(','):
        part = ' '.join(part.split())
        if not part:
            continue
        name = _BY_ALIAS.get(_key(part), part)
        if name not in names:
            names.append(name)
    return ', '.join(names) or None
