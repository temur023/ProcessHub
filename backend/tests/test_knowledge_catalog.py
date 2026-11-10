import pytest

from app.services import catalog
from app.services.store import MemoryStore
from app.knowledge_catalog_cli import catalog_departments


@pytest.fixture
def source(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(catalog, 'get_store', lambda: store)
    def item(id, title, parent=None, kind='space', position=0):
        return dict(id=id, title=title, parentId=parent, kind=kind, position=position, createdAt='2026-10-01T00:00:00Z')
    store.save_catalog([item('bank', 'Банк'), item('dept2', 'Второй', 'bank', position=1),
                       item('dept1', 'Первый', 'bank'), item('map', 'Карта процесса', 'bank', kind='map', position=2),
                       item('nested', 'Вложенная папка', 'dept1'), item('another', 'Другое пространство')])
    return store


def test_copy_only_direct_department_folders_in_catalog_order(source):
    payload = catalog_departments('bank', 2)
    assert payload['space_id'] == 'bank'
    assert [(item['catalog_item_id'], item['name'], item['number'], item['position']) for item in payload['departments']] == [
        ('dept1', 'Первый', '1', 0), ('dept2', 'Второй', '2', 1)]
    assert len(source.load_catalog()) == 6


@pytest.mark.parametrize('space,count', [('unknown', None), ('dept1', None), ('map', None), ('another', None), ('bank', 41)])
def test_copy_rejects_wrong_source_or_count_before_write(source, space, count):
    with pytest.raises(ValueError):
        catalog_departments(space, count)
