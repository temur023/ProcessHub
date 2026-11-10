"""Сохранение из встроенного draw.io (``POST /import/{id}/drawio``): та же карта, новая версия."""
import re
import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.models.process import PixRegistryRecord
from app.services.drawio_exporter import generate_drawio_xml
from app.services.drawio_parser import parse_drawio_xml
from app.services.store import get_store
from tests.test_drawio_exporter import MAP


def _cell(xml: str, cell_id: str) -> re.Match:
    return re.search(r'<(mxCell|object) id="%s"[^>]*>.*?</\1>' % re.escape(cell_id), xml, re.S)


class DrawioSaveTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        process = parse_drawio_xml(MAP, 'Правка в draw.io.drawio')
        process.name = 'Карта для правки'
        process.registry.records.append(PixRegistryRecord(
            id='rec-x', caseId='CASE-1', createdAt='2026-10-04 10:00', status='in_progress',
            currentStepId='t1', currentStepName='Принять заявку', assignedTo='Иванов',
            elapsedMinutes=5, data={}))
        next(n for n in process.nodes if n.id == 't3').role = 'Операционист'
        process.lanes[0].hourlyRate = 70_000
        self.process = get_store().save(process, 'import')
        self.xml = self.client.get(f'/api/v1/import/{self.process.id}/export/drawio').text

    def save(self, xml, revision='current'):
        rev = self.process.revision if revision == 'current' else revision
        return self.client.post(f'/api/v1/import/{self.process.id}/drawio', json={'xml': xml, 'revision': rev})

    def test_drawing_edits_land_in_the_same_map(self):
        xml = self.xml.replace('value="Проверить документы"', 'value="Проверить все документы"')
        t1 = _cell(xml, 't1').group(0)
        moved = re.sub(r'<mxGeometry x="(\d+)"', lambda m: f'<mxGeometry x="{int(m.group(1)) + 40}"', t1, count=1)
        xml = xml.replace(t1, moved)
        xml = xml.replace('</root>', '<mxCell id="new1" value="Новый шаг из draw.io" style="rounded=1;whiteSpace=wrap;html=1;" '
                          'vertex="1" parent="1"><mxGeometry x="1200" y="700" width="140" height="70" as="geometry" /></mxCell>'
                          '<mxCell id="newe" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="t3" target="new1">'
                          '<mxGeometry relative="1" as="geometry" /></mxCell></root>', 1)
        res = self.save(xml)
        self.assertEqual(res.status_code, 200, res.text)
        saved = get_store().get(self.process.id)
        by_id = {n.id: n for n in saved.nodes}
        self.assertEqual((saved.id, saved.name, saved.passport.code), (self.process.id, 'Карта для правки', self.process.passport.code))
        self.assertEqual([r.caseId for r in saved.registry.records], [r.caseId for r in self.process.registry.records])
        self.assertEqual(by_id['t2'].name, 'Проверить все документы')
        before = next(n for n in self.process.nodes if n.id == 't1')
        self.assertEqual((by_id['t1'].drawioGeometry or by_id['t1'].geometry).x,
                         (before.drawioGeometry or before.geometry).x + 40)
        self.assertIn('new1', by_id)
        self.assertTrue(any(e.sourceId == 't3' and e.targetId == 'new1' for e in saved.edges))
        self.assertEqual(by_id['t3'].role, 'Операционист')
        self.assertEqual(saved.lanes[0].hourlyRate, 70_000)
        # Себестоимость пересчитана по ставке дорожки, как при любом сохранении.
        t1_lane = next(l for l in saved.lanes if l.id == by_id['t1'].laneId)
        if t1_lane.hourlyRate:
            self.assertEqual(by_id['t1'].costPerExecution, round(by_id['t1'].slaMinutes * t1_lane.hourlyRate / 60))
        top = get_store().versions(self.process.id)[0]
        self.assertEqual(top.source, 'drawio')
        self.assertEqual(saved.revision, self.process.revision + 1)

    def test_saving_unchanged_keeps_the_map(self):
        res = self.save(self.xml)
        self.assertEqual(res.status_code, 200, res.text)
        saved = get_store().get(self.process.id)
        key = lambda p: sorted((n.id, n.type, n.name, n.laneId, n.slaMinutes, n.role) for n in p.nodes)
        self.assertEqual(key(saved), key(self.process))
        self.assertEqual(sorted((e.id, e.kind, e.sourceId, e.targetId) for e in saved.edges),
                         sorted((e.id, e.kind, e.sourceId, e.targetId) for e in self.process.edges))

    def test_page_ids_survive_when_the_file_reorders_pages(self):
        """Сводная «JAMI» в исходнике второй, а выгрузка ставит основную первой: номера страниц едут."""
        main, rest = MAP.split('<diagram id="d1" name="Детали">')
        jami = main.replace('<mxfile><diagram id="d0" name="Основная">', '<diagram id="d0" name="JAMI">')
        reordered = '<mxfile><diagram id="d1" name="Детали">' + rest.replace('</mxfile>', '') + jami + '</mxfile>'
        process = parse_drawio_xml(reordered, 'Страницы.drawio')
        self.assertEqual(process.mainPageName, 'JAMI')
        sub = process.subProcesses[0]
        self.assertTrue(sub.id.endswith('_p0'))
        t2 = next(n for n in process.nodes if n.id == 't2')
        t2.type, t2.calledSubProcessId = 'subProcess', sub.id
        t2.style = 'shape=mxgraph.bpmn.task2;isLoopSub=1;whiteSpace=wrap;html=1;'
        process = get_store().save(process, 'import')
        xml = self.client.get(f'/api/v1/import/{process.id}/export/drawio').text
        res = self.client.post(f'/api/v1/import/{process.id}/drawio', json={'xml': xml, 'revision': process.revision})
        self.assertEqual(res.status_code, 200, res.text)
        saved = get_store().get(process.id)
        self.assertEqual([(p.id, p.name) for p in saved.subProcesses], [(sub.id, 'Детали')])
        self.assertEqual(saved.subProcesses[0].passport.code, sub.passport.code)
        self.assertEqual(next(n for n in saved.nodes if n.id == 't2').calledSubProcessId, sub.id)

    def test_pages_with_the_same_name_keep_their_own_ids(self):
        """В файлах банка бывают одноимённые страницы («Investit kredit», «Харид»)."""
        second = MAP.split('<diagram id="d1" name="Детали">')[1].replace('</mxfile>', '')
        twice = MAP.replace('</mxfile>', '<diagram id="d2" name="Детали">' + second.replace('Заполнить анкету', 'Подписать анкету') + '</mxfile>')
        process = get_store().save(parse_drawio_xml(twice, 'Две детали.drawio'), 'import')
        ids = [p.id for p in process.subProcesses]
        self.assertEqual(len(set(ids)), 2)
        xml = self.client.get(f'/api/v1/import/{process.id}/export/drawio').text
        res = self.client.post(f'/api/v1/import/{process.id}/drawio', json={'xml': xml, 'revision': process.revision})
        self.assertEqual(res.status_code, 200, res.text)
        saved = get_store().get(process.id)
        self.assertEqual([(p.id, [n.name for n in p.nodes if n.type == 'userTask']) for p in saved.subProcesses],
                         [(p.id, [n.name for n in p.nodes if n.type == 'userTask']) for p in process.subProcesses])

    def test_save_from_an_outdated_copy_is_refused(self):
        self.assertEqual(self.save(self.xml).status_code, 200)
        res = self.save(self.xml.replace('value="Открыть счёт"', 'value="Чужая правка"'), revision=self.process.revision)
        self.assertEqual(res.status_code, 409)
        self.assertNotIn('Чужая правка', [n.name for n in get_store().get(self.process.id).nodes])

    def test_unreadable_file_is_refused(self):
        self.assertEqual(self.save('<mxfile><diagram').status_code, 422)
        self.assertEqual(self.save('   ').status_code, 400)
        res = self.client.post('/api/v1/import/nope/drawio', json={'xml': self.xml})
        self.assertEqual(res.status_code, 404)


if __name__ == '__main__':
    unittest.main()


BLANK_BOXES = (
    '<mxfile><diagram id="p" name="Новая схема"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<mxCell id="a" value="" style="rounded=0;whiteSpace=wrap;html=1;" vertex="1" parent="1">'
    '<mxGeometry x="100" y="100" width="120" height="60" as="geometry"/></mxCell>'
    '<mxCell id="b" value="" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">'
    '<mxGeometry x="300" y="100" width="120" height="60" as="geometry"/></mxCell>'
    '</root></mxGraphModel></diagram></mxfile>'
)


class EditorBlankShapesTest(unittest.TestCase):
    """Пустые фигуры из встроенного редактора — неподписанные шаги, а не оформление."""

    def setUp(self):
        self.client = TestClient(app)

    def test_new_map_from_editor_keeps_blank_shapes_and_they_come_back(self):
        res = self.client.post('/api/v1/import/xml', json={'xml': BLANK_BOXES, 'fileName': 'Новая.drawio', 'fromEditor': True})
        self.assertEqual(res.status_code, 200, res.text)
        process = res.json()
        self.assertEqual(sorted(n['id'] for n in process['nodes']), ['a', 'b'])
        back = self.client.get(f"/api/v1/import/{process['id']}/export/drawio").text
        self.assertIsNotNone(_cell(back, 'a'))
        self.assertIsNotNone(_cell(back, 'b'))

    def test_editor_save_keeps_blank_shapes(self):
        created = self.client.post('/api/v1/import/xml', json={'xml': BLANK_BOXES.replace('id="b" value=""', 'id="b" value="Шаг"'), 'fileName': 'Правка.drawio'}).json()
        res = self.client.post(f"/api/v1/import/{created['id']}/drawio", json={'xml': BLANK_BOXES, 'revision': created['revision']})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(sorted(n['id'] for n in res.json()['nodes']), ['a', 'b'])

    def test_uploaded_file_still_drops_blank_decoration(self):
        process = parse_drawio_xml(BLANK_BOXES, 'Загруженный.drawio')
        self.assertEqual(process.nodes, [])
