"""Пространства и карты: дерево, по которому сотрудник доходит до карты процесса.

Как в PIX: пространство — корень дерева, в нём подпространства (у банка —
департаменты), в них карты процессов. В PIX каждый уровень — тоже карта на
холсте, и открытие пространства рисует сетку из десятков подпроцессов; здесь
уровни до карты — обычные экраны, а холст есть только у самой карты.
"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

#: ``space`` — пространство (без родителя) или подпространство, ``map`` — карта процесса.
ItemKind = Literal['space', 'map']


class CatalogItem(BaseModel):
    id: str
    parentId: Optional[str] = None
    kind: ItemKind
    title: str
    #: Порядок среди соседей; номер «3.1» считается из него.
    position: int = 0
    #: Карта процесса в хранилище процессов; у карты, перенесённой из PIX без схемы, — нет.
    processId: Optional[str] = None
    favorite: bool = False
    createdAt: str
    #: Карта PIX, из которой перенесён элемент.
    pixId: Optional[str] = None


class ProcessBrief(BaseModel):
    """Сводка о схеме для карточки: сколько в ней шагов и ошибок, кто менял последним."""
    stepCount: int = 0
    errorCount: int = 0
    warningCount: int = 0
    updatedAt: Optional[datetime] = None
    #: Автор последней версии; у версий до входа в систему — никто.
    updatedBy: Optional[str] = None
    #: Статус паспорта: draft, in_review, approved, active, archived.
    status: str = 'draft'
    #: Вид модели: as_is, to_be или не указан.
    stage: Optional[str] = None


class CatalogNode(CatalogItem):
    """Элемент дерева в ответе API — с тем, что считается по дереву."""
    #: «3.1»: номер среди соседей через точку; у пространства номера нет.
    number: str = ''
    #: Уровень, как в PIX: пространство — 1, его департаменты — 2, их процессы — 3.
    level: int = 1
    #: Схема карты есть в хранилище процессов.
    hasProcess: bool = False
    spaceCount: int = 0
    mapCount: int = 0
    #: Сводка о схеме карты; у пространств и карт без схемы — нет.
    process: Optional[ProcessBrief] = None


class UnplacedProcess(ProcessBrief):
    """Процесс из хранилища, который ещё не лежит ни в одном пространстве."""
    id: str
    name: str
    nodeCount: int
    updatedDate: str = ''


class Catalog(BaseModel):
    items: List[CatalogNode]
    unplaced: List[UnplacedProcess]


class CreateItemBody(BaseModel):
    parentId: Optional[str] = None
    kind: ItemKind
    title: str = Field(min_length=1, max_length=300)
    processId: Optional[str] = None


class UpdateItemBody(BaseModel):
    """Меняется только то, что передано; ``parentId: null`` — пространство на верхний уровень."""
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    parentId: Optional[str] = None
    favorite: Optional[bool] = None
    processId: Optional[str] = None
