"""Нарисованное в draw.io доходит до холста, .bpmn и .pmm таким, как нарисовано.

Сверка большой карты «Кредит_шартнома_муддатини_ўзгартириш_—КБ_7» с draw.io
нашла пять расхождений, общих для обеих выгрузок:

1. пунктир от шага к полосе клиента обрывался на её границе и из выгрузок
   пропадал: в полосе стоят события, и она считалась обычной дорожкой;
2. цилиндр без связей, стоящий внутри дорожки, выбрасывался как легенда;
3. время «60 min» выходило на карту как «1 ч», «5 daqiqa» — как «5 мин»;
4. стартовое событие без подписи получало подпись «Старт»;
5. часы «Kutish vaqti / 1 440 min» в начале процесса становились простым
   стартом без часов и без времени.

Фикстура повторяет каждый случай в миниатюре.
"""
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.bpmn_exporter import canvas_layout, generate_bpmn_xml, step_duration_text  # noqa: E402
from app.services.drawio_parser import parse_drawio_xml  # noqa: E402
from app.services.pmm_exporter import _pix_id, generate_map_xml  # noqa: E402

TIMER = ('points=[[0.145,0.145,0],[0.5,0,0],[0.855,0.145,0],[1,0.5,0],[0.855,0.855,0],'
         '[0.5,1,0],[0.145,0.855,0],[0,0.5,0]];shape=mxgraph.bpmn.event;html=1;'
         'outline=standard;symbol=timer;')

MAP = f'''<mxfile host="test"><diagram id="d" name="Fidelity"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="mijoz" value="Mijoz" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="1400" height="120" as="geometry"/></mxCell>
  <mxCell id="wait_start" value="&lt;div&gt;Kutish vaqti&lt;/div&gt;&lt;div&gt;1 440 min&lt;/div&gt;"
    style="{TIMER}labelPosition=left;align=right;" vertex="1" parent="mijoz">
    <mxGeometry x="700" y="40" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="bank" value="Bank" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
    <mxGeometry x="0" y="160" width="1400" height="300" as="geometry"/></mxCell>
  <mxCell id="start" value="" style="shape=mxgraph.bpmn.event;html=1;outline=standard;symbol=general;" vertex="1" parent="bank">
    <mxGeometry x="60" y="110" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="t1" value="Arizani qabul qilish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="bank">
    <mxGeometry x="200" y="90" width="160" height="80" as="geometry"/></mxCell>
  <mxCell id="t1_time" value="60 min" style="{TIMER}" vertex="1" parent="t1">
    <mxGeometry x="148" y="68" width="20" height="20" as="geometry"/></mxCell>
  <mxCell id="t2" value="Hujjatlarni tekshirish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="bank">
    <mxGeometry x="560" y="90" width="160" height="80" as="geometry"/></mxCell>
  <mxCell id="t2_time" value="5 daqiqa" style="{TIMER}" vertex="1" parent="t2">
    <mxGeometry x="148" y="68" width="20" height="20" as="geometry"/></mxCell>
  <mxCell id="end" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;outline=end;symbol=terminate;" vertex="1" parent="bank">
    <mxGeometry x="1000" y="110" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="iabs" value="IABS" style="shape=datastore;html=1;verticalLabelPosition=top;" vertex="1" parent="bank">
    <mxGeometry x="820" y="40" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="legend_db" value="Tizim" style="shape=datastore;html=1;" vertex="1" parent="1">
    <mxGeometry x="200" y="600" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="f0" edge="1" parent="1" source="wait_start" target="t1"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f1" edge="1" parent="1" source="start" target="t1"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f2" edge="1" parent="1" source="t1" target="t2"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f3" edge="1" parent="1" source="t2" target="end"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="to_client" style="dashed=1;html=1;" edge="1" parent="1" source="t2">
    <mxGeometry relative="1" as="geometry"><mxPoint x="640" y="122" as="targetPoint"/></mxGeometry></mxCell>
  <mxCell id="dangling" style="dashed=1;html=1;endArrow=none;" edge="1" parent="1" source="t1">
    <mxGeometry relative="1" as="geometry"><mxPoint x="280" y="400" as="targetPoint"/></mxGeometry></mxCell>
</root></mxGraphModel></diagram></mxfile>'''


def _pmm_nodes(map_xml):
    """Узлы карты студии с абсолютными координатами."""
    root = ET.fromstring(map_xml.encode('utf-8'))
    out = {}

    def walk(element, ox=0.0, oy=0.0, parent=None):
        for child in element.findall('node'):
            x, y = float(child.get('x')) + ox, float(child.get('y')) + oy
            out[child.get('id')] = dict(type=child.get('type'), label=child.get('label'),
                                        x=x, y=y, w=float(child.get('width')), parent=parent)
            walk(child, x, y, child.get('id'))
    walk(root)
    return root, out


class FidelityItemsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(MAP, 'fidelity.drawio')
        cls.nodes = {n.id: n for n in cls.process.nodes}
        cls.edges = {e.id: e for e in cls.process.edges}
        cls.bpmn = generate_bpmn_xml(cls.process)
        _slug, cls.map_xml = generate_map_xml(cls.process)
        cls.pmm_root, cls.pmm = _pmm_nodes(cls.map_xml)

    # ── 1. Пунктир к полосе клиента ──────────────────────────────────────────

    def test_dash_ending_on_another_lane_is_a_contact(self):
        edge = self.edges['to_client']
        self.assertEqual((edge.kind, edge.targetId), ('messageFlow', 'mijoz'))

    def test_dash_dangling_inside_its_own_lane_stays_decoration(self):
        self.assertEqual(self.edges['dangling'].kind, 'annotationLine')

    def test_contact_reaches_the_bpmn_file(self):
        self.assertRegex(self.bpmn, r'<bpmn:association id="[^"]*to_client"')
        self.assertRegex(self.bpmn, r'<bpmndi:BPMNEdge id="[^"]*to_client_di"')

    def test_contact_reaches_the_pmm_map_at_its_own_point_of_the_band(self):
        # Линия ведётся прямо в полосу — без конверта-посредника: туда, где её
        # кончил аналитик (x = 640), и оттуда к ближайшей точке крепления.
        road = _pix_id('mijoz')
        source = _pix_id('t2')
        connector = next((c for c in self.pmm_root.findall('connector')
                          if c.get('sourceNodeId') == source
                          and c.get('targetNodeId') == road), None)
        self.assertIsNotNone(connector, 'пунктир к «Mijoz» не дошёл до .pmm')
        points = [(float(p.get('x')), float(p.get('y'))) for p in connector.findall('waypoint')]
        band = self.pmm[road]
        self.assertIn(points[-1][0] - band['x'], [band['w'] * f for f in (0.25, 0.5, 0.75)] + [0.0, band['w']])
        self.assertTrue(any(abs(x - 640) < 1 for x, _y in points),
                        f'линия подошла к полосе не там, где её нарисовал аналитик: {points}')

    # ── 2. Цилиндр без связей ────────────────────────────────────────────────

    def test_unconnected_store_inside_a_lane_is_kept_everywhere(self):
        self.assertEqual(self.nodes.get('iabs') and self.nodes['iabs'].type, 'dataStore')
        self.assertIn('<bpmn:dataStoreReference id="iabs" name="IABS"', self.bpmn)
        self.assertEqual(self.pmm[_pix_id('iabs')]['type'], 'dataStorage')

    def test_unconnected_store_outside_every_lane_is_a_legend(self):
        self.assertNotIn('legend_db', self.nodes)

    # ── 3. Время словами draw.io ─────────────────────────────────────────────

    def test_step_time_is_written_as_drawn(self):
        self.assertEqual(step_duration_text(self.nodes['t1']), '60 min')
        self.assertEqual(step_duration_text(self.nodes['t2']), '5 daqiqa')
        labels = {n['label'] for n in self.pmm.values()}
        self.assertTrue({'60 min', '5 daqiqa'} <= labels, labels)
        self.assertIn('name="60 min"', self.bpmn)
        self.assertNotRegex(self.map_xml + self.bpmn, r'label="1 ч"|name="1 ч"|5 мин')

    def test_time_changed_in_the_platform_is_not_shown_with_the_old_words(self):
        step = self.nodes['t1'].model_copy(update={'slaMinutes': 90})
        self.assertEqual(step_duration_text(step), '1 ч 30 мин')

    def test_canvas_shows_the_same_time_as_the_file(self):
        layout = canvas_layout(self.process)
        self.assertEqual(layout.durations.get('t1'), '60 min')

    # ── 4. Безымянный старт ──────────────────────────────────────────────────

    def test_unnamed_start_has_no_caption_on_the_map(self):
        self.assertIn('<bpmn:startEvent id="start" name=""', self.bpmn)
        self.assertEqual(self.pmm[_pix_id('start')]['label'], '')
        self.assertNotIn('start', {k for k, v in canvas_layout(self.process).nodeLabels.items() if v.text})

    # ── 5. Часы в начале процесса ────────────────────────────────────────────

    def test_timer_start_is_a_timer_with_its_time(self):
        match = re.search(r'<bpmn:startEvent id="wait_start" name="([^"]*)">(.*?)</bpmn:startEvent>',
                          self.bpmn, re.S)
        self.assertIsNotNone(match, self.bpmn[:2000])
        self.assertEqual(match.group(1), 'Kutish vaqti 1 440 min')
        self.assertIn('<bpmn:timerEventDefinition>', match.group(2))
        self.assertIn('P1D', match.group(2))
        node = self.pmm[_pix_id('wait_start')]
        self.assertEqual((node['type'], node['label']), ('start_event_timer', 'Kutish vaqti 1 440 min'))


if __name__ == '__main__':
    unittest.main()
