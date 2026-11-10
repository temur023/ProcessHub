"""Пересчёт после правки и раскладка холста.

* пересчёт после правки шага обновлял одни метрики и только в памяти, а
  замечания оставались снятыми при импорте;
* шаблон, сохранённый на сервер, не проходил проверку и назывался
  «импортированным без замечаний»;
* холст трассировал линии своим трассировщиком и расходился с выгрузкой.
"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.bpmn_exporter import canvas_layout, generate_bpmn_xml  # noqa: E402
from app.services.diagnostics import MODEL_CHECK_CODES, collect_import_diagnostics, refresh_diagnostics  # noqa: E402
from app.services.drawio_parser import parse_drawio_xml  # noqa: E402
from app.services.store import get_store  # noqa: E402

MAP = '''<mxfile host="test"><diagram id="r" name="Refresh"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="1100" height="220" as="geometry"/></mxCell>
  <mxCell id="s" value="Start" style="shape=mxgraph.bpmn.event;symbol=general;outline=standard;" vertex="1" parent="lane">
    <mxGeometry x="40" y="90" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="t1" value="Arizani qabul qilish" style="rounded=1;" vertex="1" parent="lane">
    <mxGeometry x="140" y="70" width="160" height="80" as="geometry"/></mxCell>
  <mxCell id="t2" value="Hujjatlarni tekshirish 10 min" style="rounded=1;" vertex="1" parent="lane">
    <mxGeometry x="360" y="70" width="160" height="80" as="geometry"/></mxCell>
  <mxCell id="g" value="To'liqmi?" style="shape=mxgraph.bpmn.gateway2;gwType=exclusive;verticalLabelPosition=bottom;labelBackgroundColor=#ffffff;" vertex="1" parent="lane">
    <mxGeometry x="580" y="85" width="50" height="50" as="geometry"/></mxCell>
  <mxCell id="e1" value="Tugadi" style="shape=mxgraph.bpmn.event;symbol=general;outline=end;" vertex="1" parent="lane">
    <mxGeometry x="720" y="90" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="e2" value="Rad" style="shape=mxgraph.bpmn.event;symbol=general;outline=end;" vertex="1" parent="lane">
    <mxGeometry x="720" y="160" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="f1" edge="1" parent="lane" source="s" target="t1"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f2" edge="1" parent="lane" source="t1" target="t2"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f3" edge="1" parent="lane" source="t2" target="g"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f4" value="Ha" edge="1" parent="lane" source="g" target="e1"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f5" value="Yo'q" edge="1" parent="lane" source="g" target="e2"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="deco" style="endArrow=none;" edge="1" parent="1" source="t1"><mxGeometry relative="1" as="geometry"><mxPoint x="220" y="400" as="targetPoint"/></mxGeometry></mxCell>
</root></mxGraphModel></diagram></mxfile>'''


class RefreshDiagnosticsTest(unittest.TestCase):
    """Проверки модели — заново, факты импорта — как были."""

    def setUp(self):
        self.process = parse_drawio_xml(MAP, 'refresh.drawio')

    def test_model_checks_are_exactly_what_collect_produces_without_facts(self):
        flow = [n for n in self.process.nodes if n.type != 'lane']
        codes = {v.code.removesuffix('_more') for v in collect_import_diagnostics(flow, self.process.lanes, self.process.edges)}
        self.assertTrue(codes <= MODEL_CHECK_CODES, codes - MODEL_CHECK_CODES)

    def test_refresh_is_idempotent_on_an_unchanged_map(self):
        self.assertEqual([v.message for v in refresh_diagnostics(self.process)],
                         [v.message for v in self.process.validation])

    def test_every_report_code_has_a_place_in_the_order(self):
        """Новый код замечания без места в порядке отчёта переставил бы строки при пересчёте."""
        import app.services.diagnostics as diagnostics
        with open(diagnostics.__file__, encoding='utf-8') as fh:
            source = fh.read()
        emitted = set(re.findall(r"collector\.add\(\s*'(?:error|warning|info)',\s*'([a-z_]+)'", source))
        emitted |= set(re.findall(r"_cap\(\s*collector,\s*'([a-z_]+)'", source))
        self.assertGreater(len(emitted), 20, emitted)
        self.assertEqual(sorted(emitted - set(diagnostics._REPORT_RANK)), [])

    def test_time_entered_by_a_person_clears_the_guessed_time_note(self):
        step = next(n for n in self.process.nodes if n.id == 't1')
        self.assertFalse(step.slaMeasured)
        self.assertTrue(any(v.code == 'no_step_time' and v.nodeId == 't1' for v in self.process.validation))
        step.slaMinutes, step.slaMeasured = 25, True
        fresh = refresh_diagnostics(self.process)
        self.assertFalse(any(v.code == 'no_step_time' and v.nodeId == 't1' for v in fresh))

    def test_import_facts_are_kept_in_place(self):
        self.process.validation.insert(0, self.process.validation[0].model_copy(
            update={'code': 'source_example', 'message': 'Замечание предпроверки'}))
        fresh = refresh_diagnostics(self.process)
        self.assertEqual(fresh[0].code, 'source_example')


class SaveAndRecalculateApiTest(unittest.TestCase):
    """Сохранение проверяет карту, пересчёт пишет метрики в хранилище."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_save_runs_the_checks(self):
        process = json.loads(parse_drawio_xml(MAP, 'save.drawio').model_dump_json())
        process['id'] = 'proc-save-check'
        process['validation'] = []
        body = self.client.post('/api/v1/processes/', json=process).json()
        self.assertTrue(any(v['code'] == 'no_step_time' for v in body['validation']),
                        'сохранённая карта не прошла проверку')

    def test_recalculate_refreshes_notes_and_persists_metrics(self):
        process = json.loads(parse_drawio_xml(MAP, 'recalc.drawio').model_dump_json())
        process['id'] = 'proc-recalc-check'
        self.client.post('/api/v1/processes/', json=process)
        for node in process['nodes']:
            if node['id'] == 't2':
                node['name'] = 'Arizani qabul qilish'  # имя совпало с t1
        self.client.post('/api/v1/processes/', json=process)
        metrics = self.client.post('/api/v1/analytics/proc-recalc-check/mining/recalculate')
        self.assertEqual(metrics.status_code, 200)
        fresh = self.client.get('/api/v1/processes/proc-recalc-check').json()
        self.assertTrue(any(v['code'] == 'duplicate_step_name' for v in fresh['validation']))
        stored = get_store().get('proc-recalc-check')
        self.assertEqual(stored.miningMetrics.model_dump(mode='json'), metrics.json())


class CanvasLayoutTest(unittest.TestCase):
    """Холст получает линии и подписи из расчёта выгрузки."""

    def setUp(self):
        self.process = parse_drawio_xml(MAP, 'canvas.drawio')
        self.layout = canvas_layout(self.process)

    def test_routes_have_the_shape_of_the_bpmn_routes(self):
        xml = generate_bpmn_xml(self.process)
        di = {
            m.group(1): re.findall(r'<di:waypoint x="(-?\d+)" y="(-?\d+)"', m.group(2))
            for m in re.finditer(r'<bpmndi:BPMNEdge id="[^"]*" bpmnElement="([^"]+)">(.*?)</bpmndi:BPMNEdge>', xml, re.S)
        }
        for edge_id in ('f1', 'f2', 'f3', 'f4', 'f5'):
            self.assertIn(edge_id, self.layout.routes)
            self.assertEqual(len(self.layout.routes[edge_id]), len(di[edge_id]), edge_id)
            self.assertEqual([p[0] for p in self.layout.routes[edge_id]], [int(x) for x, _ in di[edge_id]])

    def test_decoration_line_is_routed_too(self):
        self.assertIn('deco', self.layout.routes, 'линия с концом в пустоте пропала с холста')

    def test_labels_come_with_text_and_do_not_overlap(self):
        labels = {**{f'e:{k}': v for k, v in self.layout.edgeLabels.items()},
                  **{f'n:{k}': v for k, v in self.layout.nodeLabels.items()}}
        self.assertEqual(labels['e:f5'].text, "Yo'q")
        self.assertEqual(labels['n:g'].text, "To'liqmi?")
        boxes = list(labels.values())
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                dx = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
                dy = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
                self.assertFalse(dx > 0 and dy > 0, f'{a.text} ложится на {b.text}')

    def test_endpoint_accepts_a_page_and_ignores_nested_pages(self):
        client = TestClient(app)
        page = json.loads(self.process.model_dump_json())
        page['subProcesses'] = [json.loads(self.process.model_dump_json())]
        res = client.post('/api/v1/canvas/layout', json=page)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.json()['routes']), set(self.layout.routes))


if __name__ == '__main__':
    unittest.main()
