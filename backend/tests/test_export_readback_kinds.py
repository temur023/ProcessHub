"""Выгрузка и её обратное чтение: виды связей и страницы не теряются.

* пунктирный переход уходил в .pmm оформлением ассоциации — точечной линией
  без стрелки;
* прочитанный .pmm не узнавал потоки сообщений (ждал их точечными, а
  выгрузка пишет пунктиром) и читал пунктирные переходы ассоциациями;
* прочитанный .bpmn и .pmm приходили без страниц-подпроцессов.
"""
import io
import os
import sys
import unittest
import zipfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.drawio_parser import parse_drawio_xml
from app.services.pmm_exporter import _line_decoration, generate_pmm_zip
from app.services.pmm_importer import connector_kind, parse_pmm_package
from app.models.process import ProcessEdge

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'sap.pmm')


def _page(name: str, p: str) -> str:
    """Страница: полоса клиента, дорожка банка, шлюз с пунктирной веткой, база."""
    return f'''<diagram id="{p}" name="{name}"><mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="{p}_client" value="Mijoz" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
        <mxGeometry x="0" y="0" width="900" height="120" as="geometry"/></mxCell>
      <mxCell id="{p}_bank" value="Bank" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
        <mxGeometry x="0" y="200" width="900" height="260" as="geometry"/></mxCell>
      <mxCell id="{p}_s" value="Start" style="shape=mxgraph.bpmn.event;symbol=general;outline=standard;" vertex="1" parent="1">
        <mxGeometry x="60" y="280" width="40" height="40" as="geometry"/></mxCell>
      <mxCell id="{p}_t" value="Arizani ko'rish 5 min" style="rounded=1;" vertex="1" parent="1">
        <mxGeometry x="160" y="260" width="160" height="80" as="geometry"/></mxCell>
      <mxCell id="{p}_g" value="" style="shape=mxgraph.bpmn.gateway2;gwType=exclusive;" vertex="1" parent="1">
        <mxGeometry x="380" y="275" width="50" height="50" as="geometry"/></mxCell>
      <mxCell id="{p}_ok" value="Tasdiqlash 5 min" style="rounded=1;" vertex="1" parent="1">
        <mxGeometry x="500" y="260" width="160" height="80" as="geometry"/></mxCell>
      <mxCell id="{p}_e" value="End" style="shape=mxgraph.bpmn.event;symbol=general;outline=end;" vertex="1" parent="1">
        <mxGeometry x="740" y="280" width="40" height="40" as="geometry"/></mxCell>
      <mxCell id="{p}_db" value="IABS" style="shape=datastore;" vertex="1" parent="1">
        <mxGeometry x="200" y="390" width="40" height="40" as="geometry"/></mxCell>
      <mxCell id="{p}_f1" edge="1" parent="1" source="{p}_s" target="{p}_t"><mxGeometry relative="1" as="geometry"/></mxCell>
      <mxCell id="{p}_f2" edge="1" parent="1" source="{p}_t" target="{p}_g"><mxGeometry relative="1" as="geometry"/></mxCell>
      <mxCell id="{p}_f3" value="Ha" edge="1" parent="1" source="{p}_g" target="{p}_ok"><mxGeometry relative="1" as="geometry"/></mxCell>
      <mxCell id="{p}_f4" value="Yo'q" style="dashed=1;" edge="1" parent="1" source="{p}_g" target="{p}_e"><mxGeometry relative="1" as="geometry"/></mxCell>
      <mxCell id="{p}_f5" edge="1" parent="1" source="{p}_ok" target="{p}_e"><mxGeometry relative="1" as="geometry"/></mxCell>
      <mxCell id="{p}_a1" style="dashed=1;endArrow=none;" edge="1" parent="1" source="{p}_db" target="{p}_t"><mxGeometry relative="1" as="geometry"/></mxCell>
      <mxCell id="{p}_m1" style="dashed=1;" edge="1" parent="1" source="{p}_t" target="{p}_client"><mxGeometry relative="1" as="geometry"/></mxCell>
    </root></mxGraphModel></diagram>'''


FILE = f'<mxfile host="test">{_page("Asosiy", "a")}{_page("Qism", "b")}</mxfile>'


class DashedSequenceDecorationTest(unittest.TestCase):
    """Род связи решает оформление, пунктир — только вид линии."""

    def test_decoration_by_kind(self):
        edge = lambda kind, dashed: ProcessEdge(id='e', kind=kind, dashed=dashed)
        self.assertEqual(_line_decoration(edge('sequenceFlow', True)), ('dashed', 'line', 'arrowclosed'))
        self.assertEqual(_line_decoration(edge('sequenceFlow', None)), ('solid', 'line', 'arrowclosed'))
        self.assertEqual(_line_decoration(edge('association', True)), ('dotted', 'line', 'arrowLine'))
        self.assertEqual(_line_decoration(edge('messageFlow', True)), ('dashed', 'circle', 'arrowEmpty'))

    def test_reader_is_the_inverse_of_the_decoration(self):
        for kind in ('sequenceFlow', 'association', 'messageFlow'):
            for dashed in (True, None):
                style, start, end = _line_decoration(ProcessEdge(id='e', kind=kind, dashed=dashed))
                self.assertEqual(connector_kind(style, start.lower(), end.lower(), False), kind,
                                 (kind, dashed, style, start, end))


class PmmReadbackTest(unittest.TestCase):
    """Прочитанный пакет даёт те же виды связей и те же страницы."""

    def setUp(self):
        self.process = parse_drawio_xml(FILE, 'kinds.drawio')
        self.opened, _xml, _parts = parse_pmm_package(generate_pmm_zip(self.process), 'kinds.pmm')

    def test_edge_kinds_survive_the_round_trip(self):
        # Страницы файла едут в пакет одной картой (``merged_pages``), поэтому
        # сверяются связи всех страниц сразу.
        exported = Counter(
            e.kind for page in [self.process, *self.process.subProcesses] for e in page.edges
            if e.kind != 'annotationLine' and e.sourceId != e.targetId
        )
        self.assertEqual(Counter(e.kind for e in self.opened.edges), exported)
        self.assertIn('messageFlow', exported, 'в карте нет потока сообщений — проверка не показательна')

    def test_dashed_transition_is_read_as_a_transition(self):
        dashed = [e for e in self.opened.edges if e.name == "Yo'q"]
        self.assertTrue(dashed)
        self.assertEqual({e.kind for e in dashed}, {'sequenceFlow'})

    def test_every_page_comes_back_on_the_one_map(self):
        self.assertEqual(self.opened.subProcesses, [])
        names = {n.name for n in self.opened.nodes}
        for page in self.process.subProcesses:
            steps = {n.name for n in page.nodes if n.name and n.type.endswith('Task')}
            self.assertTrue(steps, page.name)
            self.assertTrue(steps <= names, page.name)

    def test_studio_reference_kinds(self):
        with open(FIXTURE, 'rb') as fh:
            sap, _x, _p = parse_pmm_package(fh.read(), 'sap.pmm')
        self.assertEqual(Counter(e.kind for e in sap.edges),
                         Counter({'sequenceFlow': 28, 'association': 21, 'messageFlow': 1}))


class BpmnReadbackTest(unittest.TestCase):
    """Прочитанный .bpmn возвращает страницы-подпроцессы."""

    def test_pages_come_back(self):
        process = parse_drawio_xml(FILE, 'kinds.drawio')
        reread = parse_drawio_xml(generate_bpmn_xml(process), 'kinds.bpmn')
        self.assertEqual([p.name for p in reread.subProcesses], [p.name for p in process.subProcesses])
        names = {n.name for n in reread.subProcesses[0].nodes}
        self.assertIn("Arizani ko'rish", ' '.join(names))
        main_ids = {n.id for n in reread.nodes}
        self.assertFalse(main_ids & {n.id for n in reread.subProcesses[0].nodes},
                         'страница легла поверх основной карты')

    def test_single_diagram_file_has_no_pages(self):
        single = parse_drawio_xml(f'<mxfile host="test">{_page("One", "o")}</mxfile>', 'one.drawio')
        self.assertEqual(parse_drawio_xml(generate_bpmn_xml(single), 'one.bpmn').subProcesses, [])


if __name__ == '__main__':
    unittest.main()
