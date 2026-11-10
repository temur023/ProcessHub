"""Хранилище карт процессов (с историей версий) и дерева пространств.

Раньше все карты лежали в одном ``process_store.json`` — 96 МБ на 68 карт, — и
каждое сохранение переписывало его целиком: сдвиг одного шага стоил 0,84 с
под общей блокировкой. Файл писался не атомарно, а ошибки чтения глушились:
сбой посреди записи оставлял платформу пустой без единого сообщения.

Теперь основное хранилище — PostgreSQL (``docker compose up -d db``), где
каждая карта — своя строка, и сохранение пишет только её. Для тестов есть то
же самое в памяти (``SQB_STORE=memory``): без Docker весь набор тестов идёт на
нём, а с ``SQB_TEST_DATABASE_URL`` — на настоящей базе (см. ``tests/conftest.py``).

ПРАВИЛА ВЕРСИЙ (общие для обоих хранилищ, ``_amends``)

* Каждое сохранение увеличивает ``revision`` карты. Сохранение, сделанное по
  устаревшей копии (``expected_revision`` не совпал), отклоняется
  ``ProcessConflict`` — иначе второй сотрудник молча стирал бы правки первого.
* Версия в истории — снимок карты. Правки холста сыплются каждые полсекунды,
  а встроенный draw.io сохраняет сам через пару секунд после правки, поэтому
  правки подряд того же вида (холст или draw.io) в пределах ``squash_seconds``
  от начала версии дописываются в неё же; импорт и восстановление — всегда
  новая версия.
* Пересчёт аналитики и новые кейсы реестра карту не меняют: они дописываются
  в последнюю версию, а новой не создают.
* Сохранение без изменений (тот же снимок) новой версии тоже не создаёт.
* У версии есть автор — кто её начал. Правки другого сотрудника в ту же
  версию не дописываются: каждый отвечает за свою.

Здесь же сотрудники и их сессии (``app.services.auth``).
"""
from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import threading
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel

from app.models.process import TASK_NODE_TYPES, BusinessProcess

log = logging.getLogger(__name__)

#: База из ``docker-compose.yml``; на сервере — ``SQB_DATABASE_URL``.
DEFAULT_DATABASE_URL = 'postgresql://sqb:sqb@localhost:5433/sqb'

#: Правки подряд дописываются в одну версию, пока ей меньше этого, с.
SQUASH_SECONDS = 600

#: Откуда сохранение: так оно подписано в истории.
SOURCES = ('import', 'edit', 'drawio', 'restore', 'migration', 'analytics', 'registry')
#: Сохранения, которые карту не меняют и версий не создают.
AMEND_SOURCES = ('analytics', 'registry')
#: Правки, которые идут часто и подряд дописываются в одну версию.
SQUASH_SOURCES = ('edit', 'drawio')

#: Сессия жива столько после последнего запроса, с.
SESSION_SECONDS = 30 * 24 * 3600
#: Сколько карт показывает профиль в «Моих последних картах».
RECENT_MAPS = 20

_APP = Path(__file__).resolve().parent.parent


class ProcessConflict(Exception):
    """Карту успели сохранить по более свежей копии."""

    def __init__(self, revision: int):
        super().__init__(f'stored revision {revision}')
        self.revision = revision


@dataclass
class ProcessInfo:
    """То, что о карте нужно списку и дереву, — без самой карты."""
    id: str
    name: str
    fileName: str
    passportCode: str
    passportStatus: str
    nodeCount: int
    laneCount: int
    conformanceRate: float
    slaHours: float
    updatedDate: str
    revision: int
    #: Шагов регламента, ошибок и предупреждений проверки — для карточки карты.
    stepCount: int = 0
    errorCount: int = 0
    warningCount: int = 0
    #: Когда и кем сохранена последняя версия; у версий до входа в систему автора нет.
    updatedAt: Optional[datetime] = None
    updatedBy: Optional[str] = None
    #: Вид модели из паспорта: as_is, to_be или не указан.
    stage: Optional[str] = None


class ProcessVersion(BaseModel):
    """Строка истории карты."""
    version: int
    revision: int
    source: str
    note: Optional[str] = None
    name: str
    nodeCount: int
    edgeCount: int
    stepCount: int
    createdAt: datetime
    savedAt: datetime
    #: Кто сохранил; у версий, сохранённых до входа в систему, — никто.
    author: Optional[str] = None
    authorEmail: Optional[str] = None


class User(BaseModel):
    """Сотрудник: под этим именем его правки видны в истории."""
    id: int
    email: str
    name: str
    isAdmin: bool = False


#: Сотрудник «сейчас в системе», если обращался к API не позже этого.
ONLINE_SECONDS = 15 * 60


class PlatformUser(BaseModel):
    """Строка списка пользователей для администратора; без хеша пароля и токенов."""
    id: int
    email: str
    name: str
    isAdmin: bool
    createdAt: datetime
    #: Последнее обращение к API по ещё живой сессии.
    lastSeenAt: Optional[datetime] = None
    online: bool
    activeSessions: int
    versions: int


class ProcessSource(BaseModel):
    """Файл, из которого карту загрузили последним, — байт в байт."""
    fileName: str
    content: bytes
    uploadedAt: datetime
    uploadedBy: Optional[str] = None


class ProcessAttachment(BaseModel):
    """Материал карты: утверждённый PDF, регламент в Excel, презентация — без содержимого."""
    id: str
    processId: str
    kind: str
    fileName: str
    mediaType: str
    size: int
    uploadedAt: datetime
    uploadedBy: Optional[str] = None
    uploadedById: Optional[int] = None


class UserExists(Exception):
    """Почта уже зарегистрирована."""


class MapActivity(BaseModel):
    """Карта, которую сотрудник правил."""
    id: str
    name: str
    #: Его версий на этой карте.
    versions: int
    lastSavedAt: datetime


@dataclass
class RoleUsage:
    """Роль (дорожка) на картах: сколько карт и шагов, сколько в ней работы, мин."""
    name: str
    maps: int
    steps: int
    workMinutes: int


def role_key(name: str) -> str:
    """Ключ роли: название дорожки без регистра и лишних пробелов."""
    return ' '.join((name or '').lower().split())


def _role_usage(pages_by_map: Iterable[Tuple[str, Iterable[dict]]]) -> List[RoleUsage]:
    """Роли по картам: страницы карты (основная и части, без вариантов) — их дорожки и шаги."""
    names: Dict[str, Counter] = {}
    maps: Dict[str, set] = {}
    steps: Counter = Counter()
    work: Counter = Counter()
    for map_id, pages in pages_by_map:
        for page in pages:
            lane_names = {lane.get('id'): lane.get('name') or '' for lane in page.get('lanes') or []}
            for lane_name in lane_names.values():
                key = role_key(lane_name)
                if key:
                    names.setdefault(key, Counter())[lane_name] += 1
                    maps.setdefault(key, set()).add(map_id)
            for node in page.get('nodes') or []:
                if node.get('type') not in TASK_NODE_TYPES:
                    continue
                key = role_key(lane_names.get(node.get('laneId'), ''))
                if key:
                    steps[key] += 1
                    work[key] += max(0, int(node.get('slaMinutes') or 0))
    return [RoleUsage(name=names[k].most_common(1)[0][0], maps=len(maps[k]), steps=steps[k], workMinutes=work[k])
            for k in names]


#: По какому полю шага ищет поиск по картам.
SEARCH_FIELDS = ('step', 'role', 'system')


@dataclass
class StepHit:
    """Шаг, найденный поиском по картам: где он и что о нём сказано."""
    processId: str
    processName: str
    #: Страница карты: пусто — основная.
    pageId: str
    pageName: str
    nodeId: str
    nodeName: str
    laneName: Optional[str]
    system: Optional[str]


def _search_text(node: dict, field: str) -> str:
    if field == 'role':
        return node.get('laneName') or node.get('role') or ''
    if field == 'system':
        return node.get('system') or ''
    return node.get('name') or ''


class Profile(BaseModel):
    """Сотрудник и то, что он сохранил на платформе."""
    user: User
    createdAt: datetime
    #: Версий, которые он начал, — всего и по источнику (edit, import, drawio, restore).
    versions: int
    bySource: Dict[str, int]
    #: Разных карт среди них.
    maps: int
    lastSavedAt: Optional[datetime] = None
    #: Его карты от свежих к старым, не больше ``RECENT_MAPS``.
    recentMaps: List[MapActivity]


def _info(p: BusinessProcess, revision: int, updated_at: Optional[datetime] = None,
          updated_by: Optional[str] = None) -> ProcessInfo:
    levels = Counter(issue.level for issue in p.validation)
    return ProcessInfo(
        id=p.id, name=p.name, fileName=p.fileName, passportCode=p.passport.code,
        passportStatus=p.passport.status, nodeCount=len(p.nodes), laneCount=len(p.lanes),
        conformanceRate=p.miningMetrics.conformanceRate, slaHours=p.passport.targetSlaHours,
        updatedDate=p.passport.updatedDate or '', revision=revision,
        stepCount=_counts(p)[2], errorCount=levels['error'], warningCount=levels['warning'],
        updatedAt=updated_at, updatedBy=updated_by, stage=p.passport.stage,
    )


def _counts(p: BusinessProcess) -> Tuple[int, int, int]:
    return len(p.nodes), len(p.edges), sum(1 for n in p.nodes if n.type in TASK_NODE_TYPES)


def _snapshot(p: BusinessProcess) -> Tuple[str, str]:
    """Снимок карты для хранения и его отпечаток.

    Ревизия в снимок не входит — она хранится рядом отдельным полем. Иначе
    отпечаток менялся бы при каждом сохранении, и «ничего не изменилось» было
    бы не узнать, а у 17 МБ карты JSON пришлось бы собирать дважды.
    """
    text = p.model_copy(update={'revision': None}).model_dump_json()
    return text, hashlib.sha256(text.encode()).hexdigest()


def _load(text: str, revision: int) -> BusinessProcess:
    process = BusinessProcess.model_validate_json(text)
    process.revision = revision
    return process


@dataclass
class _Last:
    version: int
    source: str
    content_hash: str
    age_seconds: float
    author: Optional[int]


def _amends(source: str, last: Optional[_Last], content_hash: str, squash_seconds: float,
            author: Optional[int]) -> bool:
    """Дописать ли сохранение в последнюю версию вместо новой."""
    if last is None:
        return False
    if source in AMEND_SOURCES or content_hash == last.content_hash:
        return True
    return (source in SQUASH_SOURCES and last.source == source and last.author == author
            and last.age_seconds < squash_seconds)


# ── В памяти: тесты ────────────────────────────────────────────────────────

class MemoryStore:
    """Хранилище тестов: те же правила, что у базы, но без Docker.

    Карты хранятся текстом JSON, а не объектами: правка полученной карты не
    должна менять сохранённую, как и в базе.
    """

    def __init__(self) -> None:
        self.squash_seconds: float = SQUASH_SECONDS
        self.session_seconds: float = SESSION_SECONDS
        self._lock = threading.RLock()
        #: id -> (снимок, сведения для списка)
        self._current: Dict[str, Tuple[str, ProcessInfo]] = {}
        self._versions: Dict[str, List[dict]] = {}
        self._catalog: List[dict] = []
        #: ключ роли -> {name, rate, updated_by, updated_at}
        self._role_rates: Dict[str, dict] = {}
        self._users: Dict[int, dict] = {}
        self._access_audit: List[dict] = []
        #: отпечаток токена -> {user_id, seen}
        self._sessions: Dict[str, dict] = {}
        #: id карты -> (имя файла, содержимое, когда, кто)
        self._sources: Dict[str, Tuple[str, bytes, datetime, Optional[int]]] = {}
        #: id материала -> (сведения без имени автора, содержимое)
        self._attachments: Dict[str, Tuple[ProcessAttachment, bytes]] = {}

    def get(self, process_id: str) -> Optional[BusinessProcess]:
        with self._lock:
            row = self._current.get(process_id)
        return _load(row[0], row[1].revision) if row else None

    def info(self, process_id: str) -> Optional[ProcessInfo]:
        with self._lock:
            row = self._current.get(process_id)
        return row[1] if row else None

    def infos(self) -> List[ProcessInfo]:
        with self._lock:
            return sorted((row[1] for row in self._current.values()), key=lambda i: i.name.lower())

    def is_empty(self) -> bool:
        with self._lock:
            return not self._current and not self._catalog

    def save(self, process: BusinessProcess, source: str, expected_revision: Optional[int] = None,
             note: Optional[str] = None, author: Optional[int] = None) -> BusinessProcess:
        assert source in SOURCES, source
        now = datetime.now(timezone.utc)
        with self._lock:
            old = self._current.get(process.id)
            stored = old[1].revision if old else 0
            if old and expected_revision is not None and expected_revision != stored:
                raise ProcessConflict(stored)
            saved = process.model_copy(update={'revision': stored + 1})
            text, digest = _snapshot(saved)
            self._current[saved.id] = (text, _info(saved, stored + 1, now,
                                                   self._users[author]['name'] if author in self._users else None))
            history = self._versions.setdefault(saved.id, [])
            last = history[-1] if history else None
            nodes, edges, steps = _counts(saved)
            row = dict(revision=stored + 1, name=saved.name, nodeCount=nodes, edgeCount=edges,
                       stepCount=steps, savedAt=now, data=text, hash=digest)
            if _amends(source, last and _Last(last['version'], last['source'], last['hash'],
                                              (now - last['createdAt']).total_seconds(), last['author_id']),
                       digest, self.squash_seconds, author):
                last.update(row)
            else:
                history.append(dict(row, version=(last['version'] + 1) if last else 1, source=source,
                                    note=note, createdAt=now, author_id=author))
            return saved

    def delete(self, process_id: str) -> bool:
        with self._lock:
            self._versions.pop(process_id, None)
            self._sources.pop(process_id, None)
            self._attachments = {k: v for k, v in self._attachments.items() if v[0].processId != process_id}
            return self._current.pop(process_id, None) is not None

    def _attachment_meta(self, meta: ProcessAttachment) -> ProcessAttachment:
        user = self._users.get(meta.uploadedById) if meta.uploadedById is not None else None
        return meta.model_copy(update={'uploadedBy': user and user['name']})

    def add_attachment(self, process_id: str, kind: str, file_name: str, media_type: str, content: bytes,
                       author: Optional[int] = None) -> ProcessAttachment:
        meta = ProcessAttachment(id=f'att_{uuid.uuid4().hex[:12]}', processId=process_id, kind=kind, fileName=file_name,
                                 mediaType=media_type, size=len(content), uploadedAt=datetime.now(timezone.utc),
                                 uploadedById=author)
        with self._lock:
            self._attachments[meta.id] = (meta, content)
            return self._attachment_meta(meta)

    def attachments(self, process_id: str) -> List[ProcessAttachment]:
        with self._lock:
            rows = [self._attachment_meta(m) for m, _ in self._attachments.values() if m.processId == process_id]
        return sorted(rows, key=lambda m: m.uploadedAt, reverse=True)

    def attachment(self, process_id: str, attachment_id: str) -> Optional[Tuple[ProcessAttachment, bytes]]:
        with self._lock:
            row = self._attachments.get(attachment_id)
            if row is None or row[0].processId != process_id:
                return None
            return self._attachment_meta(row[0]), row[1]

    def remove_attachment(self, process_id: str, attachment_id: str) -> bool:
        with self._lock:
            row = self._attachments.get(attachment_id)
            if row is None or row[0].processId != process_id:
                return False
            del self._attachments[attachment_id]
            return True

    def save_source(self, process_id: str, file_name: str, content: bytes, author: Optional[int] = None) -> None:
        with self._lock:
            self._sources[process_id] = (file_name, content, datetime.now(timezone.utc), author)

    def source(self, process_id: str) -> Optional[ProcessSource]:
        with self._lock:
            row = self._sources.get(process_id)
            user = self._users.get(row[3]) if row and row[3] is not None else None
        if row is None:
            return None
        return ProcessSource(fileName=row[0], content=row[1], uploadedAt=row[2], uploadedBy=user and user['name'])

    def versions(self, process_id: str) -> List[ProcessVersion]:
        with self._lock:
            rows = [(r, self._users.get(r['author_id'])) for r in self._versions.get(process_id, [])]
        return [ProcessVersion(**{k: r[k] for k in ProcessVersion.model_fields if k in r},
                               author=u and u['name'], authorEmail=u and u['email'])
                for r, u in reversed(rows)]

    def get_version(self, process_id: str, version: int) -> Optional[BusinessProcess]:
        with self._lock:
            row = next((r for r in self._versions.get(process_id, []) if r['version'] == version), None)
        return _load(row['data'], row['revision']) if row else None

    def load_catalog(self) -> List[dict]:
        with self._lock:
            return json.loads(json.dumps(self._catalog))

    def save_catalog(self, items: List[dict]) -> None:
        with self._lock:
            self._catalog = json.loads(json.dumps(items))

    def upsert_catalog_items(self, items: List[dict]) -> None:
        with self._lock:
            fresh = {i['id']: json.loads(json.dumps(i)) for i in items}
            self._catalog = [fresh.pop(i['id'], i) for i in self._catalog] + list(fresh.values())

    def delete_catalog_item(self, item_id: str) -> None:
        with self._lock:
            self._catalog = [i for i in self._catalog if i['id'] != item_id]

    def create_user(self, email: str, name: str, password_hash: str) -> User:
        with self._lock:
            if any(u['email'] == email for u in self._users.values()):
                raise UserExists(email)
            user = User(id=len(self._users) + 1, email=email, name=name)
            self._users[user.id] = dict(user.model_dump(), password_hash=password_hash,
                                        createdAt=datetime.now(timezone.utc))
        return user

    def rename_user(self, user_id: int, name: str) -> User:
        with self._lock:
            u = self._users[user_id]
            u['name'] = name
        return User(id=u['id'], email=u['email'], name=u['name'], isAdmin=u.get('isAdmin', False))

    def set_platform_admin(self, email: str, enabled: bool, reason: str) -> User:
        """Только операторская CLI; регистрация и браузер не назначают администратора."""
        reason = reason.strip()
        if not reason:
            raise ValueError('Укажите основание действия')
        with self._lock:
            u = next((u for u in self._users.values() if u['email'] == email), None)
            if u is None:
                raise ValueError('Аккаунт не найден. Сначала зарегистрируйте сотрудника в Hub')
            if u.get('isAdmin', False) != enabled:
                if not enabled and sum(bool(item.get('isAdmin')) for item in self._users.values()) <= 1:
                    raise ValueError('Нельзя отозвать права последнего администратора')
                u['isAdmin'] = enabled
                self._access_audit.append(dict(user_id=u['id'], enabled=enabled, reason=reason))
            return User(id=u['id'], email=u['email'], name=u['name'], isAdmin=enabled)

    def set_password(self, user_id: int, password_hash: str, keep_session: str) -> None:
        """Новый пароль; все сессии сотрудника, кроме ``keep_session``, закрываются."""
        with self._lock:
            self._users[user_id]['password_hash'] = password_hash
            self._sessions = {t: s for t, s in self._sessions.items()
                              if s['user_id'] != user_id or t == keep_session}

    def role_rates(self) -> Dict[str, int]:
        """Ставки часа по ролям: {ключ роли: сум/ч}."""
        with self._lock:
            return {key: row['rate'] for key, row in self._role_rates.items()}

    def set_role_rate(self, name: str, rate: Optional[int], author: Optional[int]) -> None:
        """Ставка роли для всех карт; ``None`` — убрать, роль считается по ставке по умолчанию."""
        with self._lock:
            if rate is None:
                self._role_rates.pop(role_key(name), None)
            else:
                self._role_rates[role_key(name)] = dict(name=name, rate=rate, updated_by=author,
                                                        updated_at=datetime.now(timezone.utc))

    def role_usage(self) -> List[RoleUsage]:
        with self._lock:
            texts = [(pid, text) for pid, (text, _info) in self._current.items()]
        out = []
        for pid, text in texts:
            data = json.loads(text)
            if (data.get('passport') or {}).get('stage') == 'to_be':
                continue  # нагрузку ролей считают по картам «как есть»
            out.append((pid, [data, *data.get('subProcesses', [])]))
        return _role_usage(out)

    def search_steps(self, query: str, field: str, limit: int) -> Tuple[int, List[StepHit]]:
        """Шаги всех карт и их страниц, у которых поле содержит запрос (без регистра)."""
        needle = query.lower()
        with self._lock:
            texts = sorted(((info.name.lower(), text) for text, info in self._current.values()))
        hits: List[StepHit] = []
        for _name, text in texts:
            data = json.loads(text)
            pages = [('', data.get('mainPageName') or data['name'], data),
                     *((p['id'], p['name'], p) for p in data.get('subProcesses', []) + data.get('variants', []))]
            for page_id, page_name, page in pages:
                for node in page.get('nodes', []):
                    if node.get('type') in TASK_NODE_TYPES and needle in _search_text(node, field).lower():
                        hits.append(StepHit(data['id'], data['name'], page_id, page_name, node['id'], node.get('name') or '',
                                            node.get('laneName'), node.get('system')))
        return len(hits), hits[:limit]

    def system_mentions(self) -> List[Tuple[str, int]]:
        """Подписи ИТ-систем у шагов всех карт и их страниц: (подпись, шагов)."""
        with self._lock:
            texts = [text for text, _info in self._current.values()]
        counts: Counter = Counter()
        for text in texts:
            data = json.loads(text)
            for page in [data, *data.get('subProcesses', []), *data.get('variants', [])]:
                for node in page.get('nodes', []):
                    if node.get('type') in TASK_NODE_TYPES and node.get('system'):
                        counts[node['system']] += 1
        return list(counts.items())

    def profile(self, user_id: int) -> Optional[Profile]:
        with self._lock:
            u = self._users.get(user_id)
            if u is None:
                return None
            mine = [(pid, r['source'], r['savedAt']) for pid, rows in self._versions.items()
                    for r in rows if r['author_id'] == user_id]
            names = {pid: self._current[pid][1].name for pid, _, _ in mine}
        maps: Dict[str, Tuple[int, datetime]] = {}
        for pid, _, saved in mine:
            count, last = maps.get(pid, (0, saved))
            maps[pid] = (count + 1, max(last, saved))
        recent = sorted((MapActivity(id=pid, name=names[pid], versions=count, lastSavedAt=last)
                         for pid, (count, last) in maps.items()), key=lambda m: m.lastSavedAt, reverse=True)
        return Profile(user=User(id=u['id'], email=u['email'], name=u['name'], isAdmin=u.get('isAdmin', False)), createdAt=u['createdAt'],
                       versions=len(mine), bySource=dict(Counter(source for _, source, _ in mine)),
                       maps=len(maps), lastSavedAt=recent[0].lastSavedAt if recent else None,
                       recentMaps=recent[:RECENT_MAPS])

    def find_user(self, email: str) -> Optional[Tuple[User, str]]:
        """Сотрудник по почте и хэш его пароля."""
        with self._lock:
            u = next((u for u in self._users.values() if u['email'] == email), None)
        return (User(id=u['id'], email=u['email'], name=u['name'], isAdmin=u.get('isAdmin', False)), u['password_hash']) if u else None

    def public_users(self, ids: List[int]) -> Dict[int, User]:
        """Публичные профили конкретных ID; без хешей паролей и сессий."""
        with self._lock:
            return {i: User(id=u['id'], email=u['email'], name=u['name'], isAdmin=u.get('isAdmin', False))
                    for i in set(ids) if (u := self._users.get(i)) is not None}

    def create_session(self, token_hash: str, user_id: int) -> None:
        with self._lock:
            self._sessions[token_hash] = dict(user_id=user_id, seen=datetime.now(timezone.utc))

    def list_users(self) -> List[PlatformUser]:
        """Все аккаунты: роль платформы, активность и число версий."""
        now = datetime.now(timezone.utc)
        with self._lock:
            live = [s for s in self._sessions.values() if (now - s['seen']).total_seconds() < self.session_seconds]
            authors = Counter(r['author_id'] for rows in self._versions.values() for r in rows)
            result = []
            for u in self._users.values():
                seen = max((s['seen'] for s in live if s['user_id'] == u['id']), default=None)
                result.append(PlatformUser(
                    id=u['id'], email=u['email'], name=u['name'], isAdmin=u.get('isAdmin', False),
                    createdAt=u['createdAt'], lastSeenAt=seen,
                    online=bool(seen and (now - seen).total_seconds() < ONLINE_SECONDS),
                    activeSessions=sum(s['user_id'] == u['id'] for s in live), versions=authors[u['id']]))
        return sorted(result, key=lambda item: item.name.lower())

    def session_user(self, token_hash: str) -> Optional[User]:
        """Чья сессия; живая продлевается, истёкшая — никто."""
        now = datetime.now(timezone.utc)
        with self._lock:
            session = self._sessions.get(token_hash)
            if not session or (now - session['seen']).total_seconds() >= self.session_seconds:
                return None
            session['seen'] = now
            u = self._users[session['user_id']]
        return User(id=u['id'], email=u['email'], name=u['name'], isAdmin=u.get('isAdmin', False))

    def delete_session(self, token_hash: str) -> None:
        with self._lock:
            self._sessions.pop(token_hash, None)


# ── PostgreSQL ─────────────────────────────────────────────────────────────

SCHEMA = (
    '''create table if not exists users (
        id integer generated always as identity primary key,
        email text not null unique,
        name text not null,
        password_hash text not null,
        created_at timestamptz not null default now()
    )''',
    '''create table if not exists sessions (
        token_hash text primary key,
        user_id integer not null references users(id) on delete cascade,
        created_at timestamptz not null default now(),
        seen_at timestamptz not null default now()
    )''',
    '''create table if not exists platform_admins (
        user_id integer primary key references users(id),
        granted_at timestamptz not null default now()
    )''',
    '''create table if not exists platform_access_audit (
        id bigint generated always as identity primary key,
        target_user_id integer not null references users(id),
        action text not null check (action in ('operator.admin.granted', 'operator.admin.revoked')),
        reason text not null check (length(trim(reason)) > 0),
        created_at timestamptz not null default now()
    )''',
    '''create or replace function reject_platform_access_audit_mutation() returns trigger language plpgsql as $$
        begin raise exception 'Access audit is immutable'; end;
    $$''',
    '''do $$ begin
        if not exists (select 1 from pg_trigger where tgname = 'immutable_platform_access_audit'
            and tgrelid = 'platform_access_audit'::regclass) then
            create trigger immutable_platform_access_audit before update or delete on platform_access_audit
                for each row execute function reject_platform_access_audit_mutation();
        end if;
    end $$''',
    '''create table if not exists processes (
        id text primary key,
        name text not null,
        file_name text not null,
        passport_code text not null,
        passport_status text not null,
        node_count integer not null,
        lane_count integer not null,
        conformance_rate double precision not null,
        sla_hours double precision not null,
        updated_date text not null,
        revision integer not null,
        updated_at timestamptz not null default now(),
        data jsonb not null
    )''',
    '''create table if not exists process_versions (
        process_id text not null references processes(id) on delete cascade,
        version integer not null,
        revision integer not null,
        source text not null,
        note text,
        name text not null,
        node_count integer not null,
        edge_count integer not null,
        step_count integer not null,
        content_hash text not null,
        created_at timestamptz not null default now(),
        saved_at timestamptz not null default now(),
        data jsonb not null,
        primary key (process_id, version)
    )''',
    '''create table if not exists catalog_items (
        id text primary key,
        data jsonb not null
    )''',
    # История, начатая до входа в систему, остаётся без автора.
    'alter table process_versions add column if not exists author_id integer references users(id)',
    'create index if not exists process_versions_author on process_versions (author_id)',
    # Сводка для карточки карты: шаги и итоги проверки. Карты, сохранённые до
    # появления столбцов, досчитываются по своему JSON один раз.
    '''create table if not exists role_rates (
        key text primary key,
        name text not null,
        rate integer not null,
        updated_by integer references users(id),
        updated_at timestamptz not null default now()
    )''',
    'alter table processes add column if not exists step_count integer',
    'alter table processes add column if not exists error_count integer',
    'alter table processes add column if not exists warning_count integer',
    '''update processes set
        step_count = (select count(*) from jsonb_array_elements(data->'nodes') n
                      where n->>'type' in (%s)),
        error_count = (select count(*) from jsonb_array_elements(coalesce(data->'validation', '[]'::jsonb)) v
                       where v->>'level' = 'error'),
        warning_count = (select count(*) from jsonb_array_elements(coalesce(data->'validation', '[]'::jsonb)) v
                         where v->>'level' = 'warning')
      where step_count is null''' % ', '.join(f"'{t}'" for t in TASK_NODE_TYPES),
    # Загруженный файл как есть: последний по каждой карте, для просмотра оригинала.
    '''create table if not exists process_attachments (
        id text primary key,
        process_id text not null references processes(id) on delete cascade,
        kind text not null,
        file_name text not null,
        media_type text not null,
        size integer not null,
        content bytea not null,
        uploaded_at timestamptz not null default now(),
        uploaded_by integer references users(id)
    )''',
    'create index if not exists process_attachments_process on process_attachments (process_id, uploaded_at desc)',
    '''create table if not exists process_sources (
        process_id text primary key references processes(id) on delete cascade,
        file_name text not null,
        content bytea not null,
        uploaded_at timestamptz not null default now(),
        uploaded_by integer references users(id)
    )''',
)

_INFO_COLUMNS = ('id, name, file_name, passport_code, passport_status, node_count, lane_count, '
                 'conformance_rate, sla_hours, updated_date, revision, step_count, error_count, warning_count')
#: Сведения о карте для списка и дерева: столбцы и автор последней версии.
_INFO_SELECT = (
    'select p.id, p.name, p.file_name, p.passport_code, p.passport_status, p.node_count, p.lane_count, '
    'p.conformance_rate, p.sla_hours, p.updated_date, p.revision, '
    'coalesce(p.step_count, 0), coalesce(p.error_count, 0), coalesce(p.warning_count, 0), p.updated_at, '
    '(select u.name from process_versions v left join users u on u.id = v.author_id '
    ' where v.process_id = p.id order by v.version desc limit 1), '
    "p.data->'passport'->>'stage' "
    'from processes p')


def _row_info(row) -> ProcessInfo:
    return ProcessInfo(*row)


class PostgresStore:
    def __init__(self, url: str) -> None:
        import psycopg
        from psycopg_pool import ConnectionPool

        self.squash_seconds: float = SQUASH_SECONDS
        self.session_seconds: float = SESSION_SECONDS
        self.url = url
        try:
            # Пробное соединение: без базы бэкенд не должен «работать» молча.
            with psycopg.connect(url, connect_timeout=5) as conn:
                for statement in SCHEMA:
                    conn.execute(statement)
        except psycopg.OperationalError as err:
            raise RuntimeError(
                f'Нет связи с PostgreSQL ({_safe_url(url)}): {err}. '
                'Поднимите базу: docker compose up -d db (в корне проекта).') from err
        # Соединение проверяется, когда его берут из пула: после перезапуска
        # базы (docker compose restart) пул иначе отдавал оборванные, и первые
        # запросы падали с 500.
        self._pool = ConnectionPool(url, min_size=1, max_size=8, open=True,
                                    check=ConnectionPool.check_connection)

    def close(self) -> None:
        self._pool.close()

    def get(self, process_id: str) -> Optional[BusinessProcess]:
        with self._pool.connection() as conn:
            row = conn.execute('select data::text, revision from processes where id = %s',
                               (process_id,)).fetchone()
        return _load(*row) if row else None

    def info(self, process_id: str) -> Optional[ProcessInfo]:
        with self._pool.connection() as conn:
            row = conn.execute(f'{_INFO_SELECT} where p.id = %s', (process_id,)).fetchone()
        return _row_info(row) if row else None

    def infos(self) -> List[ProcessInfo]:
        with self._pool.connection() as conn:
            rows = conn.execute(f'{_INFO_SELECT} order by lower(p.name)').fetchall()
        return [_row_info(r) for r in rows]

    def is_empty(self) -> bool:
        with self._pool.connection() as conn:
            return not conn.execute(
                'select exists(select 1 from processes) or exists(select 1 from catalog_items)').fetchone()[0]

    def save(self, process: BusinessProcess, source: str, expected_revision: Optional[int] = None,
             note: Optional[str] = None, author: Optional[int] = None) -> BusinessProcess:
        assert source in SOURCES, source
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute('select revision from processes where id = %s for update',
                               (process.id,)).fetchone()
            stored = row[0] if row else 0
            if row and expected_revision is not None and expected_revision != stored:
                raise ProcessConflict(stored)
            saved = process.model_copy(update={'revision': stored + 1})
            info = _info(saved, stored + 1)
            text, digest = _snapshot(saved)
            conn.execute(
                f'insert into processes ({_INFO_COLUMNS}, data) '
                'values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) '
                'on conflict (id) do update set name = excluded.name, file_name = excluded.file_name, '
                'passport_code = excluded.passport_code, passport_status = excluded.passport_status, '
                'node_count = excluded.node_count, lane_count = excluded.lane_count, '
                'conformance_rate = excluded.conformance_rate, sla_hours = excluded.sla_hours, '
                'updated_date = excluded.updated_date, revision = excluded.revision, '
                'step_count = excluded.step_count, error_count = excluded.error_count, '
                'warning_count = excluded.warning_count, updated_at = now(), data = excluded.data',
                (info.id, info.name, info.fileName, info.passportCode, info.passportStatus, info.nodeCount,
                 info.laneCount, info.conformanceRate, info.slaHours, info.updatedDate, info.revision,
                 info.stepCount, info.errorCount, info.warningCount, text))
            last_row = conn.execute(
                'select version, source, content_hash, extract(epoch from now() - created_at), author_id '
                'from process_versions where process_id = %s order by version desc limit 1',
                (saved.id,)).fetchone()
            last = (_Last(last_row[0], last_row[1], last_row[2], float(last_row[3]), last_row[4])
                    if last_row else None)
            nodes, edges, steps = _counts(saved)
            # Снимок берётся из только что записанной строки: 17 МБ карты не
            # гоняются по сети второй раз.
            if _amends(source, last, digest, self.squash_seconds, author):
                conn.execute(
                    'update process_versions v set revision = %s, name = %s, node_count = %s, edge_count = %s, '
                    'step_count = %s, content_hash = %s, saved_at = now(), data = p.data '
                    'from processes p where p.id = v.process_id and v.process_id = %s and v.version = %s',
                    (saved.revision, saved.name, nodes, edges, steps, digest, saved.id, last.version))
            else:
                conn.execute(
                    'insert into process_versions (process_id, version, revision, source, note, name, '
                    'node_count, edge_count, step_count, content_hash, author_id, data) '
                    'select id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, data from processes where id = %s',
                    ((last.version + 1) if last else 1, saved.revision, source, note, saved.name,
                     nodes, edges, steps, digest, author, saved.id))
        return saved

    def delete(self, process_id: str) -> bool:
        with self._pool.connection() as conn:
            return conn.execute('delete from processes where id = %s', (process_id,)).rowcount > 0

    _ATTACHMENT_COLUMNS = ('select a.id, a.process_id, a.kind, a.file_name, a.media_type, a.size, a.uploaded_at, '
                           'u.name, a.uploaded_by{content} from process_attachments a left join users u on u.id = a.uploaded_by ')

    @staticmethod
    def _attachment_row(row) -> ProcessAttachment:
        return ProcessAttachment(id=row[0], processId=row[1], kind=row[2], fileName=row[3], mediaType=row[4], size=row[5],
                                 uploadedAt=row[6], uploadedBy=row[7], uploadedById=row[8])

    def add_attachment(self, process_id: str, kind: str, file_name: str, media_type: str, content: bytes,
                       author: Optional[int] = None) -> ProcessAttachment:
        attachment_id = f'att_{uuid.uuid4().hex[:12]}'
        with self._pool.connection() as conn:
            conn.execute(
                'insert into process_attachments (id, process_id, kind, file_name, media_type, size, content, uploaded_by) '
                'values (%s, %s, %s, %s, %s, %s, %s, %s)',
                (attachment_id, process_id, kind, file_name, media_type, len(content), content, author))
            row = conn.execute(self._ATTACHMENT_COLUMNS.format(content='') + 'where a.id = %s', (attachment_id,)).fetchone()
        return self._attachment_row(row)

    def attachments(self, process_id: str) -> List[ProcessAttachment]:
        with self._pool.connection() as conn:
            rows = conn.execute(self._ATTACHMENT_COLUMNS.format(content='') +
                                'where a.process_id = %s order by a.uploaded_at desc, a.id', (process_id,)).fetchall()
        return [self._attachment_row(r) for r in rows]

    def attachment(self, process_id: str, attachment_id: str) -> Optional[Tuple[ProcessAttachment, bytes]]:
        with self._pool.connection() as conn:
            row = conn.execute(self._ATTACHMENT_COLUMNS.format(content=', a.content') +
                               'where a.id = %s and a.process_id = %s', (attachment_id, process_id)).fetchone()
        return (self._attachment_row(row), bytes(row[9])) if row else None

    def remove_attachment(self, process_id: str, attachment_id: str) -> bool:
        with self._pool.connection() as conn:
            return conn.execute('delete from process_attachments where id = %s and process_id = %s',
                                (attachment_id, process_id)).rowcount > 0

    def save_source(self, process_id: str, file_name: str, content: bytes, author: Optional[int] = None) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                'insert into process_sources (process_id, file_name, content, uploaded_by) values (%s, %s, %s, %s) '
                'on conflict (process_id) do update set file_name = excluded.file_name, content = excluded.content, '
                'uploaded_at = now(), uploaded_by = excluded.uploaded_by',
                (process_id, file_name, content, author))

    def source(self, process_id: str) -> Optional[ProcessSource]:
        with self._pool.connection() as conn:
            row = conn.execute(
                'select s.file_name, s.content, s.uploaded_at, u.name from process_sources s '
                'left join users u on u.id = s.uploaded_by where s.process_id = %s', (process_id,)).fetchone()
        if row is None:
            return None
        return ProcessSource(fileName=row[0], content=bytes(row[1]), uploadedAt=row[2], uploadedBy=row[3])

    def versions(self, process_id: str) -> List[ProcessVersion]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                'select v.version, v.revision, v.source, v.note, v.name, v.node_count, v.edge_count, '
                'v.step_count, v.created_at, v.saved_at, u.name, u.email '
                'from process_versions v left join users u on u.id = v.author_id '
                'where v.process_id = %s order by v.version desc',
                (process_id,)).fetchall()
        keys = ('version', 'revision', 'source', 'note', 'name', 'nodeCount', 'edgeCount', 'stepCount',
                'createdAt', 'savedAt', 'author', 'authorEmail')
        return [ProcessVersion(**dict(zip(keys, r))) for r in rows]

    def get_version(self, process_id: str, version: int) -> Optional[BusinessProcess]:
        with self._pool.connection() as conn:
            row = conn.execute('select data::text, revision from process_versions '
                               'where process_id = %s and version = %s', (process_id, version)).fetchone()
        return _load(*row) if row else None

    def load_catalog(self) -> List[dict]:
        with self._pool.connection() as conn:
            return [r[0] for r in conn.execute('select data from catalog_items').fetchall()]

    def save_catalog(self, items: List[dict]) -> None:
        """Дерево целиком — одной транзакцией, как раньше одним файлом: оно маленькое."""
        with self._pool.connection() as conn, conn.transaction():
            conn.execute('delete from catalog_items')
            with conn.cursor() as cur:
                cur.executemany('insert into catalog_items (id, data) values (%s, %s::jsonb)',
                                [(item['id'], json.dumps(item, ensure_ascii=False)) for item in items])

    def upsert_catalog_items(self, items: List[dict]) -> None:
        """Только изменённые элементы — правки соседних элементов другим процессом остаются."""
        with self._pool.connection() as conn, conn.transaction():
            with conn.cursor() as cur:
                cur.executemany(
                    'insert into catalog_items (id, data) values (%s, %s::jsonb) '
                    'on conflict (id) do update set data = excluded.data',
                    [(item['id'], json.dumps(item, ensure_ascii=False)) for item in items])

    def delete_catalog_item(self, item_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute('delete from catalog_items where id = %s', (item_id,))

    def create_user(self, email: str, name: str, password_hash: str) -> User:
        with self._pool.connection() as conn:
            row = conn.execute('insert into users (email, name, password_hash) values (%s, %s, %s) '
                               'on conflict (email) do nothing returning id',
                               (email, name, password_hash)).fetchone()
        if row is None:
            raise UserExists(email)
        return User(id=row[0], email=email, name=name)

    def rename_user(self, user_id: int, name: str) -> User:
        with self._pool.connection() as conn:
            row = conn.execute('update users set name = %s where id = %s returning id, email, name, '
                               'exists(select 1 from platform_admins a where a.user_id=users.id)',
                               (name, user_id)).fetchone()
        return User(id=row[0], email=row[1], name=row[2], isAdmin=row[3])

    def set_platform_admin(self, email: str, enabled: bool, reason: str) -> User:
        """Назначение по существующему аккаунту, с аудитом и защитой последнего администратора."""
        reason = reason.strip()
        if not reason:
            raise ValueError('Укажите основание действия')
        with self._pool.connection() as conn, conn.transaction():
            conn.execute('select pg_advisory_xact_lock(73010702)')
            row = conn.execute('select id,email,name,exists(select 1 from platform_admins a where a.user_id=u.id) '
                               'from users u where email=%s for update', (email,)).fetchone()
            if not row:
                raise ValueError('Аккаунт не найден. Сначала зарегистрируйте сотрудника в Hub')
            if row[3] != enabled:
                if enabled:
                    conn.execute('insert into platform_admins(user_id) values (%s)', (row[0],))
                else:
                    if conn.execute('select count(*) from platform_admins').fetchone()[0] <= 1:
                        raise ValueError('Нельзя отозвать права последнего администратора')
                    conn.execute('delete from platform_admins where user_id=%s', (row[0],))
                conn.execute('insert into platform_access_audit(target_user_id,action,reason) values (%s,%s,%s)',
                             (row[0], 'operator.admin.granted' if enabled else 'operator.admin.revoked', reason))
            return User(id=row[0], email=row[1], name=row[2], isAdmin=enabled)

    def set_password(self, user_id: int, password_hash: str, keep_session: str) -> None:
        """Новый пароль; все сессии сотрудника, кроме ``keep_session``, закрываются."""
        with self._pool.connection() as conn, conn.transaction():
            conn.execute('update users set password_hash = %s where id = %s', (password_hash, user_id))
            conn.execute('delete from sessions where user_id = %s and token_hash <> %s', (user_id, keep_session))

    def role_rates(self) -> Dict[str, int]:
        with self._pool.connection() as conn:
            return dict(conn.execute('select key, rate from role_rates').fetchall())

    def set_role_rate(self, name: str, rate: Optional[int], author: Optional[int]) -> None:
        with self._pool.connection() as conn:
            if rate is None:
                conn.execute('delete from role_rates where key = %s', (role_key(name),))
            else:
                conn.execute(
                    'insert into role_rates (key, name, rate, updated_by) values (%s, %s, %s, %s) '
                    'on conflict (key) do update set name = excluded.name, rate = excluded.rate, '
                    'updated_by = excluded.updated_by, updated_at = now()',
                    (role_key(name), name, rate, author))

    def role_usage(self) -> List[RoleUsage]:
        """Роли по картам — из JSON в базе: дорожки и шаги основной страницы и частей, без вариантов."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select p.id, jsonb_build_array(jsonb_build_object('lanes', p.data->'lanes', 'nodes', "
                "  (select coalesce(jsonb_agg(jsonb_build_object('type', n->'type', 'laneId', n->'laneId', "
                "   'slaMinutes', n->'slaMinutes')), '[]'::jsonb) from jsonb_array_elements(p.data->'nodes') n))) "
                "|| coalesce((select jsonb_agg(jsonb_build_object('lanes', s->'lanes', 'nodes', "
                "  (select coalesce(jsonb_agg(jsonb_build_object('type', n->'type', 'laneId', n->'laneId', "
                "   'slaMinutes', n->'slaMinutes')), '[]'::jsonb) from jsonb_array_elements(s->'nodes') n))) "
                "  from jsonb_array_elements(coalesce(p.data->'subProcesses', '[]'::jsonb)) s), '[]'::jsonb) "
                "from processes p where coalesce(p.data->'passport'->>'stage', '') <> 'to_be'").fetchall()
        return _role_usage((pid, pages) for pid, pages in rows)

    def search_steps(self, query: str, field: str, limit: int) -> Tuple[int, List[StepHit]]:
        column = {'step': "n->>'name'", 'role': "coalesce(n->>'laneName', n->>'role')", 'system': "n->>'system'"}[field]
        types = ', '.join(f"'{t}'" for t in TASK_NODE_TYPES)
        pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        with self._pool.connection() as conn:
            rows = conn.execute(
                'with pages as ('
                " select p.id pid, p.name pname, '' page_id, coalesce(p.data->>'mainPageName', p.name) page_name,"
                '  p.data d, 0 ord from processes p'
                " union all select p.id, p.name, s->>'id', s->>'name', s, 1"
                "  from processes p, jsonb_array_elements(coalesce(p.data->'subProcesses', '[]'::jsonb)) s"
                " union all select p.id, p.name, v->>'id', v->>'name', v, 2"
                "  from processes p, jsonb_array_elements(coalesce(p.data->'variants', '[]'::jsonb)) v)"
                " select pid, pname, page_id, page_name, n->>'id', coalesce(n->>'name', ''), n->>'laneName', n->>'system',"
                '  count(*) over ()'
                " from pages, jsonb_array_elements(pages.d->'nodes') n"
                f" where n->>'type' in ({types}) and {column} ilike %s"
                ' order by lower(pname), ord, page_name limit %s',
                (pattern, limit)).fetchall()
        total = rows[0][8] if rows else 0
        return total, [StepHit(*row[:8]) for row in rows]

    def system_mentions(self) -> List[Tuple[str, int]]:
        """Подписи ИТ-систем у шагов всех карт и их страниц: (подпись, шагов)."""
        types = ', '.join(f"'{t}'" for t in TASK_NODE_TYPES)
        with self._pool.connection() as conn:
            return [tuple(r) for r in conn.execute(
                'with pages as ('
                ' select data from processes'
                " union all select s from processes p, jsonb_array_elements(coalesce(p.data->'subProcesses', '[]'::jsonb)) s"
                " union all select v from processes p, jsonb_array_elements(coalesce(p.data->'variants', '[]'::jsonb)) v)"
                " select n->>'system', count(*) from pages, jsonb_array_elements(pages.data->'nodes') n"
                f" where n->>'type' in ({types}) and coalesce(n->>'system', '') <> '' group by 1").fetchall()]

    def profile(self, user_id: int) -> Optional[Profile]:
        with self._pool.connection() as conn:
            u = conn.execute('select id,email,name,created_at,exists(select 1 from platform_admins a where a.user_id=u.id) '
                             'from users u where id=%s', (user_id,)).fetchone()
            if u is None:
                return None
            by_source = dict(conn.execute(
                'select source, count(*) from process_versions where author_id = %s group by source',
                (user_id,)).fetchall())
            maps = conn.execute(
                'select v.process_id, p.name, count(*), max(v.saved_at) from process_versions v '
                'join processes p on p.id = v.process_id where v.author_id = %s '
                'group by v.process_id, p.name order by max(v.saved_at) desc', (user_id,)).fetchall()
        return Profile(user=User(id=u[0], email=u[1], name=u[2], isAdmin=u[4]), createdAt=u[3],
                       versions=sum(by_source.values()), bySource=by_source, maps=len(maps),
                       lastSavedAt=maps[0][3] if maps else None,
                       recentMaps=[MapActivity(id=m[0], name=m[1], versions=m[2], lastSavedAt=m[3])
                                   for m in maps[:RECENT_MAPS]])

    def find_user(self, email: str) -> Optional[Tuple[User, str]]:
        """Сотрудник по почте и хэш его пароля."""
        with self._pool.connection() as conn:
            row = conn.execute('select id,email,name,password_hash,exists(select 1 from platform_admins a where a.user_id=u.id) '
                               'from users u where email=%s',
                               (email,)).fetchone()
        return (User(id=row[0], email=row[1], name=row[2], isAdmin=row[4]), row[3]) if row else None

    def public_users(self, ids: List[int]) -> Dict[int, User]:
        """Только публичные поля запрошенных аккаунтов, одним запросом."""
        if not ids:
            return {}
        with self._pool.connection() as conn:
            rows = conn.execute('select id,email,name,exists(select 1 from platform_admins a where a.user_id=u.id) '
                                'from users u where id=ANY(%s)', (ids,)).fetchall()
        return {r[0]: User(id=r[0], email=r[1], name=r[2], isAdmin=r[3]) for r in rows}

    def create_session(self, token_hash: str, user_id: int) -> None:
        with self._pool.connection() as conn:
            # Заодно убираются истёкшие: иначе таблица только растёт.
            conn.execute('delete from sessions where seen_at < now() - make_interval(secs => %s)',
                         (self.session_seconds,))
            conn.execute('insert into sessions (token_hash, user_id) values (%s, %s)', (token_hash, user_id))

    def list_users(self) -> List[PlatformUser]:
        """Все аккаунты: роль платформы, активность и число версий — одним запросом."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                'select u.id, u.email, u.name, exists(select 1 from platform_admins a where a.user_id = u.id), '
                'u.created_at, s.seen, coalesce(s.live, 0), '
                's.seen > now() - make_interval(secs => %s), '
                '(select count(*) from process_versions v where v.author_id = u.id) '
                'from users u left join lateral ('
                ' select max(seen_at) seen, count(*) live from sessions '
                ' where user_id = u.id and seen_at > now() - make_interval(secs => %s)) s on true '
                'order by lower(u.name), u.id',
                (ONLINE_SECONDS, self.session_seconds)).fetchall()
        return [PlatformUser(id=r[0], email=r[1], name=r[2], isAdmin=r[3], createdAt=r[4],
                             lastSeenAt=r[5], online=bool(r[7]), activeSessions=r[6], versions=r[8]) for r in rows]

    def session_user(self, token_hash: str) -> Optional[User]:
        """Чья сессия; живая продлевается, истёкшая — никто. Один запрос на обращение к API."""
        with self._pool.connection() as conn:
            row = conn.execute(
                'update sessions s set seen_at = now() from users u '
                'where s.token_hash = %s and u.id = s.user_id '
                'and s.seen_at > now() - make_interval(secs => %s) '
                'returning u.id,u.email,u.name,exists(select 1 from platform_admins a where a.user_id=u.id)',
                (token_hash, self.session_seconds)).fetchone()
        return User(id=row[0], email=row[1], name=row[2], isAdmin=row[3]) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._pool.connection() as conn:
            conn.execute('delete from sessions where token_hash = %s', (token_hash,))


Store = MemoryStore | PostgresStore


def _safe_url(url: str) -> str:
    """Адрес базы для сообщений — без пароля."""
    if '@' in url and '://' in url:
        scheme, rest = url.split('://', 1)
        creds, host = rest.rsplit('@', 1)
        return f'{scheme}://{creds.split(":", 1)[0]}:***@{host}'
    return url


def restore(store: Store, process_id: str, version: int,
            author: Optional[int] = None) -> Optional[BusinessProcess]:
    """Старая версия становится текущей — новой версией, а не перезаписью истории."""
    old = store.get_version(process_id, version)
    if old is None:
        return None
    return store.save(old, 'restore', note=f'из версии {version}', author=author)


# ── Переезд из JSON-файлов ─────────────────────────────────────────────────

def json_store_files() -> Tuple[Path, Path]:
    """Файлы прежнего хранилища (их же можно увести переменными, как раньше)."""
    return (
        Path(os.environ.get('SQB_PROCESS_STORE') or _APP / 'data' / 'process_store.json'),
        Path(os.environ.get('SQB_CATALOG_STORE') or _APP / 'data' / 'catalog.json'),
    )


def migrate_from_files(store: Store, process_file: Path, catalog_file: Path) -> Tuple[int, int, int]:
    """Карты и дерево из прежних JSON-файлов — в пустое хранилище, один раз.

    Файлы не трогаются: остаются резервной копией. Хранилище, в котором уже
    что-то есть, не трогается тоже — повторный запуск ничего не дублирует.
    Возвращает (перенесено карт, пропущено битых карт, элементов дерева).
    """
    if not store.is_empty():
        return 0, 0, 0
    moved = skipped = 0
    if process_file.exists():
        for pid, raw in json.loads(process_file.read_text(encoding='utf-8')).items():
            try:
                process = BusinessProcess.model_validate(raw)
            except ValueError as err:
                skipped += 1
                log.warning('Карта %s не перенесена: %s', pid, err)
                continue
            store.save(process, 'migration', note=process_file.name)
            moved += 1
    items: List[dict] = []
    if catalog_file.exists():
        items = json.loads(catalog_file.read_text(encoding='utf-8'))
        store.save_catalog(items)
    return moved, skipped, len(items)


# ── Хранилище процесса ─────────────────────────────────────────────────────

_store: Optional[Store] = None
_store_lock = threading.Lock()


def get_store() -> Store:
    """Хранилище, открытое при первом обращении.

    ``SQB_STORE=memory`` — в памяти (тесты); иначе PostgreSQL по
    ``SQB_DATABASE_URL``. Пустая база при первом открытии забирает карты и
    дерево из прежних JSON-файлов.
    """
    global _store
    with _store_lock:
        if _store is None:
            if os.environ.get('SQB_STORE') == 'memory':
                _store = MemoryStore()
            else:
                store = PostgresStore(os.environ.get('SQB_DATABASE_URL') or DEFAULT_DATABASE_URL)
                # Скрипты и тесты сервер не останавливают: пул закрывается при выходе.
                atexit.register(store.close)
                moved, skipped, items = migrate_from_files(store, *json_store_files())
                if moved or items:
                    log.warning('Перенесено в PostgreSQL: карт %s (пропущено битых: %s), элементов дерева %s',
                                moved, skipped, items)
                _store = store
        return _store


def close_store() -> None:
    """Закрыть соединения с базой при остановке сервера."""
    global _store
    with _store_lock:
        if isinstance(_store, PostgresStore):
            _store.close()
        _store = None
