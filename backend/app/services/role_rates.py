"""Ставки часа по ролям: одна ставка на роль для всех карт.

Раньше ставка задавалась дорожке в каждой карте отдельно — из 1256 дорожек
банка её задали у двух, и себестоимость всех карт считалась по ставке по
умолчанию. Роль — это дорожка с тем же названием (без регистра и лишних
пробелов): «Kredit menejeri» на любой карте получает одну и ту же ставку.

Ставки читаются из хранилища и держатся в памяти несколько секунд: расчёт
стоимости зовёт их на каждую дорожку, а база общая у контейнера и локального
сервера.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional

from app.services.store import get_store, role_key

#: Сколько секунд ставки живут в памяти, прежде чем перечитаться из базы.
TTL_SECONDS = 5.0

_lock = threading.Lock()
_rates: Dict[str, int] = {}
_loaded_at = float('-inf')


def rates() -> Dict[str, int]:
    global _rates, _loaded_at
    with _lock:
        if time.monotonic() - _loaded_at > TTL_SECONDS:
            _rates = get_store().role_rates()
            _loaded_at = time.monotonic()
        return _rates


def rate_for(lane_name: Optional[str]) -> Optional[int]:
    return rates().get(role_key(lane_name or '')) if lane_name else None


def set_rate(name: str, rate: Optional[int], author: Optional[int]) -> None:
    global _loaded_at
    get_store().set_role_rate(name, rate, author)
    with _lock:
        _loaded_at = float('-inf')
