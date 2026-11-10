"""Выгрузка карты в draw.io и обратно (``drawio_exporter``): карта возвращается той же."""
import re
import unittest
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app.main import app
from app.models.process import ProcessField
from app.services.drawio_exporter import generate_drawio_xml
from app.services.drawio_parser import parse_drawio_xml
from app.services.store import get_store

MAP = """<mxfile><diagram id="d0" name="Основная"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="pool" value="Банк" style="swimlane;html=1;childLayout=stackLayout;horizontal=0;startSize=20;" vertex="1" parent="1">
  <mxGeometry x="40" y="40" width="1300" height="400" as="geometry"/></mxCell>
<mxCell id="laneA" value="Фронт-офис" style="swimlane;html=1;horizontal=0;startSize=26;" vertex="1" parent="pool">
  <mxGeometry x="20" width="1280" height="200" as="geometry"/></mxCell>
<mxCell id="laneB" value="Бэк-офис" style="swimlane;html=1;horizontal=0;startSize=26;" vertex="1" parent="pool">
  <mxGeometry x="20" y="200" width="1280" height="200" as="geometry"/></mxCell>
<mxCell id="s" value="" style="shape=mxgraph.bpmn.event;outline=standard;symbol=general;html=1;" vertex="1" parent="laneA">
  <mxGeometry x="60" y="80" width="40" height="40" as="geometry"/></mxCell>
<mxCell id="t1" value="Принять заявку &amp;lt;срочно&amp;gt; &amp;amp; проверить" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="laneA">
  <mxGeometry x="160" y="65" width="140" height="70" as="geometry"/></mxCell>
<mxCell id="b1" value="15 min" style="shape=mxgraph.bpmn.event;symbol=timer;html=1;" vertex="1" parent="laneA">
  <mxGeometry x="285" y="120" width="20" height="20" as="geometry"/></mxCell>
<mxCell id="t2" value="Проверить документы" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="laneA">
  <mxGeometry x="330" y="65" width="140" height="70" as="geometry"/></mxCell>
<mxCell id="b2" value="45 min" style="shape=mxgraph.bpmn.event;symbol=timer;html=1;" vertex="1" parent="laneA">
  <mxGeometry x="455" y="120" width="20" height="20" as="geometry"/></mxCell>
<mxCell id="w2" value="Kutish vaqti 30 min" style="shape=mxgraph.bpmn.event;symbol=timer;html=1;" vertex="1" parent="laneA">
  <mxGeometry x="330" y="40" width="20" height="20" as="geometry"/></mxCell>
<mxCell id="g" value="" style="rhombus;html=1;" vertex="1" parent="laneA">
  <mxGeometry x="540" y="75" width="50" height="50" as="geometry"/></mxCell>
<mxCell id="wait" value="Kutish vaqti 1 kun" style="shape=mxgraph.bpmn.event;outline=catching;symbol=timer;html=1;" vertex="1" parent="laneB">
  <mxGeometry x="640" y="80" width="40" height="40" as="geometry"/></mxCell>
<mxCell id="t3" value="Открыть счёт" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="laneB">
  <mxGeometry x="760" y="65" width="140" height="70" as="geometry"/></mxCell>
<mxCell id="db" value="IABS" style="shape=datastore;html=1;" vertex="1" parent="laneB">
  <mxGeometry x="800" y="160" width="30" height="25" as="geometry"/></mxCell>
<mxCell id="e" value="" style="shape=mxgraph.bpmn.event;outline=end;html=1;" vertex="1" parent="laneB">
  <mxGeometry x="1000" y="80" width="40" height="40" as="geometry"/></mxCell>
<mxCell id="f1" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="s" target="t1"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="f2" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="t1" target="t2"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="f3" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="t2" target="g"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="f4" value="Ha" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="g" target="wait">
  <mxGeometry x="-0.4" y="10" relative="1" as="geometry"><Array as="points"><mxPoint x="620" y="140"/></Array></mxGeometry></mxCell>
<mxCell id="f5" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="wait" target="t3"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="f6" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="t3" target="e"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="f7" style="dashed=1;endArrow=none;html=1;" edge="1" parent="1" source="db" target="t3"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="f8" style="dashed=1;html=1;" edge="1" parent="1" source="t2">
  <mxGeometry relative="1" as="geometry"><mxPoint x="1500" y="-200" as="targetPoint"/></mxGeometry></mxCell>
</root></mxGraphModel></diagram>
<diagram id="d1" name="Детали"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="ds" value="" style="ellipse;html=1;" vertex="1" parent="1"><mxGeometry x="40" y="40" width="40" height="40" as="geometry"/></mxCell>
<mxCell id="dt" value="Заполнить анкету" style="rounded=1;html=1;" vertex="1" parent="1"><mxGeometry x="140" y="25" width="140" height="70" as="geometry"/></mxCell>
<mxCell id="de" value="" style="ellipse;strokeWidth=3;html=1;" vertex="1" parent="1"><mxGeometry x="340" y="40" width="40" height="40" as="geometry"/></mxCell>
<mxCell id="df1" edge="1" parent="1" source="ds" target="dt"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="df2" edge="1" parent="1" source="dt" target="de"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>"""


def roundtrip(process):
    return parse_drawio_xml(generate_drawio_xml(process), 'roundtrip.drawio')


class DrawioExportTest(unittest.TestCase):
    def setUp(self):
        self.process = parse_drawio_xml(MAP, 'Проверка.drawio')
        self.by_id = {n.id: n for n in self.process.nodes}

    def node(self, process, node_id):
        return next(n for n in process.nodes if n.id == node_id)

    def test_drawing_comes_back(self):
        back = roundtrip(self.process)
        for want in self.process.nodes:
            got = self.node(back, want.id)
            self.assertEqual((got.type, got.name, got.laneId, got.drawioGeometry or got.geometry),
                             (want.type, want.name, want.laneId, want.drawioGeometry or want.geometry), want.id)
        self.assertEqual({(e.id, e.kind, e.sourceId, e.targetId, e.name) for e in back.edges},
                         {(e.id, e.kind, e.sourceId, e.targetId, e.name) for e in self.process.edges})
        f4 = next(e for e in back.edges if e.id == 'f4')
        self.assertEqual([(p.x, p.y) for p in f4.points], [(620, 140)])

    def test_pool_comes_back(self):
        back = roundtrip(self.process)
        self.assertEqual({(l.id, l.laneId, l.name) for l in back.lanes},
                         {(l.id, l.laneId, l.name) for l in self.process.lanes})
        self.assertTrue(all(l.laneId == 'pool' for l in back.lanes))

    def test_time_badges_land_on_their_own_step(self):
        self.assertEqual((self.by_id['t1'].slaMinutes, self.by_id['t2'].slaMinutes), (15, 45))
        back = roundtrip(self.process)
        t1, t2 = self.node(back, 't1'), self.node(back, 't2')
        self.assertEqual((t1.slaMinutes, t1.slaMeasured, t1.slaText), (15, True, '15 min'))
        self.assertEqual((t2.slaMinutes, t2.slaText, t2.waitMinutes), (45, '45 min', 30))

    def test_time_badge_moves_with_its_step(self):
        """Шаг, сдвинутый в draw.io, не теряет своё время: значок — его потомок."""
        xml = generate_drawio_xml(self.process)
        t2 = re.search(r'<mxCell id="t2" [^>]*><mxGeometry x="(\d+)"', xml)
        moved = xml.replace(t2.group(0), t2.group(0).replace(f'x="{t2.group(1)}"', f'x="{int(t2.group(1)) + 150}"'), 1)
        got = self.node(parse_drawio_xml(moved, 'moved.drawio'), 't2')
        self.assertEqual(((got.drawioGeometry or got.geometry).x, got.slaMinutes, got.slaText, got.waitMinutes),
                         ((self.by_id['t2'].drawioGeometry or self.by_id['t2'].geometry).x + 150, 45, '45 min', 30))

    def test_event_time_stays_in_its_label(self):
        wait = self.by_id['wait']
        self.assertEqual((wait.type, wait.slaMinutes, wait.slaText), ('intermediateTimerEvent', 1440, '1 kun'))
        got = self.node(roundtrip(self.process), 'wait')
        self.assertEqual((got.name, got.slaMinutes, got.slaText), (wait.name, 1440, '1 kun'))

    def test_wait_badge_alone_does_not_make_step_time_measured(self):
        xml = MAP.replace('<mxCell id="b2" value="45 min"', '<mxCell id="b2x" value=""')
        t2 = self.node(parse_drawio_xml(xml, 'x.drawio'), 't2')
        self.assertEqual(t2.waitMinutes, 30)
        self.assertFalse(t2.slaMeasured, 'значок ожидания выдал условное ST за снятое с карты')

    def test_platform_fields_survive(self):
        t3 = self.by_id['t3']
        t3.role = 'Операционист'
        t3.system = 'АБС, CRM'
        t3.category = 'approval'
        t3.code = 'STEP-77'
        t3.automationPotential = 15
        t3.description = 'Проверить <паспорт> & устав'
        t3.formFields = [ProcessField(id='f', name='ИНН', code='inn')]
        t1 = self.by_id['t1']
        t1.category = 'rpa_bot'
        t1.type = 'serviceTask'
        t1.costPerExecution = 900
        self.process.lanes[0].hourlyRate = 70_000
        back = roundtrip(self.process)
        got = self.node(back, 't3')
        self.assertEqual((got.role, got.system, got.category, got.code, got.automationPotential, got.description),
                         ('Операционист', 'АБС, CRM', 'approval', 'STEP-77', 15, 'Проверить <паспорт> & устав'))
        self.assertEqual([f.code for f in got.formFields], ['inn'])
        got1 = self.node(back, 't1')
        self.assertEqual((got1.type, got1.category, got1.costPerExecution), ('serviceTask', 'rpa_bot', 900))
        lane = next(l for l in back.lanes if l.id == self.process.lanes[0].id)
        self.assertEqual(lane.hourlyRate, 70_000)

    def test_shape_data_is_written_only_where_the_drawing_does_not_say_it(self):
        root = ET.fromstring(generate_drawio_xml(self.process))
        self.assertEqual(root.findall('.//object'), [], 'без правок на платформе свойства не нужны')
        self.by_id['t3'].role = 'Операционист'
        objects = ET.fromstring(generate_drawio_xml(self.process)).findall('.//object')
        self.assertEqual([(o.get('id'), o.get('sqb_role')) for o in objects], [('t3', 'Операционист')])

    def test_redrawn_shape_wins_over_its_old_data(self):
        """Перерисованная в draw.io фигура получает тип по рисунку, а не по прежнему свойству."""
        self.by_id['t3'].role = 'Операционист'
        xml = generate_drawio_xml(self.process).replace(
            'sqb_role="Операционист"', 'sqb_role="Операционист" sqb_type="userTask"')
        xml = xml.replace('<mxCell style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="laneB"',
                          '<mxCell style="rhombus;html=1;" vertex="1" parent="laneB"', 1)
        got = self.node(parse_drawio_xml(xml, 'x.drawio'), 't3')
        self.assertEqual(got.type, 'exclusiveGateway')

    def test_generated_names_stay_generated(self):
        gateway = self.by_id['g']
        self.assertEqual(gateway.name, 'Условие')
        root = ET.fromstring(generate_drawio_xml(self.process))
        cell = next(c for c in root.iter('mxCell') if c.get('id') == 'g')
        self.assertEqual(cell.get('value'), '', 'придуманное импортом имя на карте не пишется')
        self.assertEqual(self.node(roundtrip(self.process), 'g').name, 'Условие')

    def test_html_sensitive_text_survives(self):
        self.assertEqual(self.by_id['t1'].name, 'Принять заявку <срочно> & проверить')
        self.assertEqual(self.node(roundtrip(self.process), 't1').name, 'Принять заявку <срочно> & проверить')

    def test_free_line_end_stays_free(self):
        f8 = next(e for e in self.process.edges if e.id == 'f8')
        self.assertIsNone(f8.targetId)
        got = next(e for e in roundtrip(self.process).edges if e.id == 'f8')
        self.assertEqual((got.kind, got.targetId, got.targetPoint), (f8.kind, None, f8.targetPoint))

    def test_free_line_end_over_a_shape_is_not_snapped(self):
        """Свободный в карте конец, лежащий над фигурой, импорт притянул бы — свойство не даёт."""
        f8 = next(e for e in self.process.edges if e.id == 'f8')
        t3 = self.by_id['t3'].geometry
        f8.targetPoint = f8.targetPoint.model_copy(update={'x': t3.x + 10, 'y': t3.y + 10})
        got = next(e for e in roundtrip(self.process).edges if e.id == 'f8')
        self.assertIsNone(got.targetId)
        self.assertEqual(got.kind, 'annotationLine')

    def test_pages_and_page_links_survive(self):
        self.assertEqual([p.name for p in self.process.subProcesses], ['Детали'])
        sub = self.process.subProcesses[0]
        t2 = self.by_id['t2']
        t2.type = 'subProcess'
        t2.style = 'shape=mxgraph.bpmn.task2;isLoopSub=1;whiteSpace=wrap;html=1;'
        t2.calledSubProcessId = sub.id
        back = roundtrip(self.process)
        self.assertEqual([p.name for p in back.subProcesses], ['Детали'])
        self.assertEqual([n.name for n in back.subProcesses[0].nodes], [n.name for n in sub.nodes])
        self.assertEqual(self.node(back, 't2').calledSubProcessId, back.subProcesses[0].id)


class EdgeKindKeptTest(unittest.TestCase):
    """Обрывок, признанный оформлением, после выгрузки и разбора остаётся оформлением («1.3», шаг 214)."""

    def test_a_decoration_line_attached_to_two_steps_stays_decoration(self):
        from tests.test_pix_import_fidelity import DOTTED_STUB

        process = parse_drawio_xml(DOTTED_STUB, 'stub.drawio')
        stub = next(e for e in process.edges if e.id == 'stub')
        self.assertEqual((stub.kind, stub.sourceId, stub.targetId), ('annotationLine', 'b', 'a'))
        back = next(e for e in roundtrip(process).edges if e.id == 'stub')
        self.assertEqual((back.kind, back.sourceId, back.targetId), ('annotationLine', 'b', 'a'))


class DrawioEndpointTest(unittest.TestCase):
    def test_download_imports_back(self):
        client = TestClient(app)
        process = get_store().save(parse_drawio_xml(MAP, 'Проверка.drawio'), 'import')
        res = client.get(f'/api/v1/import/{process.id}/export/drawio')
        self.assertEqual(res.status_code, 200)
        self.assertIn('.drawio', res.headers['content-disposition'])
        back = parse_drawio_xml(res.text, 'back.drawio')
        self.assertEqual(sorted(n.id for n in back.nodes), sorted(n.id for n in process.nodes))
        self.assertEqual(client.get('/api/v1/import/nope/export/drawio').status_code, 404)


if __name__ == '__main__':
    unittest.main()
