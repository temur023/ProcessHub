"""Пространства, подпространства и карты — путь от главного экрана до карты процесса."""
from fastapi import APIRouter, HTTPException

from app.models.catalog import Catalog, CatalogNode, CreateItemBody, UpdateItemBody
from app.services.store import get_store
from app.services import catalog
from app.services.catalog import CatalogError

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _fail(err: CatalogError) -> HTTPException:
    return HTTPException(status_code=err.status, detail=err.message)


def _processes():
    """Карты хранилища для дерева: только сведения, без самих схем."""
    return {p.id: p for p in get_store().infos()}


@router.get("/", response_model=Catalog, summary="Всё дерево пространств и карт и процессы без места")
def get_catalog():
    return catalog.snapshot(_processes())


@router.post("/items", response_model=CatalogNode, status_code=201, summary="Создать пространство или карту")
def create_item(body: CreateItemBody):
    try:
        processes = _processes()
        item_id = catalog.create_item(body.parentId, body.kind, body.title, body.processId, processes)
        return catalog.node(item_id, processes)
    except CatalogError as err:
        raise _fail(err)


@router.patch("/items/{item_id}", response_model=CatalogNode,
              summary="Переименовать, перенести, отметить звездой, положить процесс в карту")
def update_item(item_id: str, body: UpdateItemBody):
    try:
        processes = _processes()
        catalog.update_item(item_id, body.model_dump(exclude_unset=True), processes)
        return catalog.node(item_id, processes)
    except CatalogError as err:
        raise _fail(err)


@router.delete("/items/{item_id}", status_code=204, summary="Удалить пустое пространство или карту")
def delete_item(item_id: str):
    try:
        catalog.delete_item(item_id)
    except CatalogError as err:
        raise _fail(err)
