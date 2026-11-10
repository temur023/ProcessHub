"""Выгрузка открывается в PIX такой, какой её собрали.

Три дефекта с карты «AVGUST-1.29. Bank tarmogi boyicha malumotlarni
shakllantirish», проверенные импортом на PIX банка (PIX Процессы 2.3.2):

* линии .pmm превращались в кашу: концы ломаной не лежали в точках крепления
  PIX, и студия строила свою линию, вклеивая в неё середину нашей;
* плашка заголовка уезжала «Объектом данных» и рисовалась огромным листом;
* из .bpmn пропадали все связи шагов с документами и базами: обычную
  ассоциацию к данным импорт PIX выбрасывает.

Правила студии записаны здесь отдельно от экспортёра — по коду её клиента, —
чтобы тест не повторял ошибку того, что проверяет.
"""
import io
import os
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.drawio_parser import parse_bpmn_xml, parse_drawio_xml
from app.services.edge_routing import MIN_SEGMENT
from app.services.export_validation import validate_bpmn_xml, validate_pmm_package
from app.services.pix_spec_checker import validate_bpmn_for_pix, validate_pmm_for_pix
from app.services.pmm_exporter import generate_pmm_zip

BPMN_NS = {'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
           'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI'}

#: Точка крепления PIX: номер -> (грань, доля вдоль грани). Снято с DOM клиента.
PIX_HANDLES = {
    0: ('bottom', .5), 3: ('bottom', .5), 14: ('bottom', .75), 15: ('bottom', .25),
    20: ('bottom', .75), 21: ('bottom', .25),
    1: ('top', .5), 2: ('top', .5), 16: ('top', .75), 17: ('top', .25),
    18: ('top', .75), 19: ('top', .25),
    4: ('right', .5), 5: ('right', .5), 22: ('right', .25), 23: ('right', .75),
    24: ('right', .25), 25: ('right', .75),
    6: ('left', .5), 7: ('left', .5), 26: ('left', .25), 27: ('left', .75),
    28: ('left', .25), 29: ('left', .75),
}

#: Сочетания «стиль + начало + конец», которые PIX узнаёт; прочие становятся
#: сплошной стрелкой перехода.
PIX_STYLES = {
    ('solid', 'line', 'arrowclosed'),
    ('dotted', 'line', 'arrowLine'),
    ('dashed', 'circle', 'arrowEmpty'),
}

TITLE = "Bank tarmog'i bo'yicha ma'lumotlarni shakllantirish jarayoni"

DRAWIO = f'''<mxfile host="test"><diagram id="d" name="Page-1"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="title" value="{TITLE}" style="rounded=1;dashed=1;whiteSpace=wrap;fontSize=72;fontStyle=1;" vertex="1" parent="1">
    <mxGeometry x="0" y="-300" width="1400" height="160" as="geometry"/></mxCell>
  <mxCell id="laneA" value="Xizmat" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="1400" height="260" as="geometry"/></mxCell>
  <mxCell id="laneB" value="Direktor" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
    <mxGeometry x="0" y="260" width="1400" height="260" as="geometry"/></mxCell>
  <mxCell id="s" value="Start" style="shape=mxgraph.bpmn.event;symbol=general;outline=standard;" vertex="1" parent="1">
    <mxGeometry x="60" y="110" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="t1" value="1. Ma'lumotlarni yig'ish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="160" y="85" width="139" height="90" as="geometry"/></mxCell>
  <mxCell id="t2" value="2. Jadvalni to'ldirish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="380" y="96" width="150" height="80" as="geometry"/></mxCell>
  <mxCell id="doc" value="" style="shape=mxgraph.bpmn.data2;labelPosition=center;verticalLabelPosition=bottom;" vertex="1" parent="1">
    <mxGeometry x="130" y="20" width="25" height="33" as="geometry"/></mxCell>
  <mxCell id="db" value="EHA" style="shape=datastore;" vertex="1" parent="1">
    <mxGeometry x="560" y="20" width="30" height="20" as="geometry"/></mxCell>
  <mxCell id="t3" value="3. Imzolash" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="380" y="345" width="150" height="80" as="geometry"/></mxCell>
  <mxCell id="arch" value="" style="shape=mxgraph.bpmn.data2;labelPosition=center;verticalLabelPosition=bottom;" vertex="1" parent="1">
    <mxGeometry x="420" y="460" width="25" height="33" as="geometry"/></mxCell>
  <mxCell id="g" value="Kelishildimi?" style="shape=mxgraph.bpmn.gateway2;gwType=exclusive;" vertex="1" parent="1">
    <mxGeometry x="640" y="360" width="50" height="50" as="geometry"/></mxCell>
  <mxCell id="e" value="Yakun" style="shape=mxgraph.bpmn.event;symbol=general;outline=end;" vertex="1" parent="1">
    <mxGeometry x="900" y="110" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="note" value="Izoh" style="shape=note;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="760" y="440" width="100" height="40" as="geometry"/></mxCell>
  <mxCell id="f1" edge="1" parent="1" source="s" target="t1"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f2" edge="1" parent="1" source="t1" target="t2"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f3" edge="1" parent="1" source="t2" target="t3"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f4" edge="1" parent="1" source="t3" target="g"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f5" value="XA" edge="1" parent="1" source="g" target="e"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f6" value="YO'Q" style="edgeStyle=orthogonalEdgeStyle;exitX=0.5;exitY=0;entryX=0.5;entryY=1;" edge="1" parent="1" source="g" target="t2"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="a1" style="edgeStyle=elbowEdgeStyle;dashed=1;endArrow=none;exitX=0.5;exitY=1;entryX=0;entryY=0.25;" edge="1" parent="1" source="doc" target="t1"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="a2" style="dashed=1;endArrow=none;" edge="1" parent="1" source="db" target="t2"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="a3" style="dashed=1;endArrow=none;" edge="1" parent="1" source="t3" target="arch"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="a4" style="dashed=1;endArrow=none;" edge="1" parent="1" source="note" target="t3"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>'''


def _map_root(payload: bytes) -> ET.Element:
    archive = zipfile.ZipFile(io.BytesIO(payload))
    maps = [n for n in archive.namelist() if n.startswith('pm/maps/')]
    return ET.fromstring(archive.read(maps[0]))


def _frames(root: ET.Element) -> dict:
    """Абсолютные рамки фигур: внутри дорожки координаты отсчитываются от неё."""
    out = {}

    def walk(parent, ox, oy):
        for node in parent.findall('node'):
            x, y = ox + float(node.get('x')), oy + float(node.get('y'))
            out[node.get('id')] = (x, y, float(node.get('width')), float(node.get('height')), node)
            if node.get('type') == 'horizontalRoad':
                walk(node, x, y)

    walk(root, 0.0, 0.0)
    return out


def _handle_point(frame, handle: int):
    """Точка крепления так, как её считает клиент PIX (функция ``ZOt``)."""
    x, y, w, h = frame[:4]
    side, fraction = PIX_HANDLES[handle]
    return {
        'top': (x + w * fraction, y),
        'bottom': (x + w * fraction, y + h),
        'left': (x, y + h * fraction),
        'right': (x + w, y + h * fraction),
    }[side]


class PmmConnectorsStayAsDrawnTest(unittest.TestCase):
    """PIX берёт ломаную как есть, только если выполнены его условия."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(DRAWIO, 'avgust.drawio')
        cls.payload = generate_pmm_zip(cls.process)
        cls.root = _map_root(cls.payload)
        cls.frames = _frames(cls.root)
        cls.connectors = cls.root.findall('connector')

    def _route(self, connector):
        return [(float(w.get('x')), float(w.get('y'))) for w in connector.findall('waypoint')]

    def test_the_fixture_has_every_kind_of_link(self):
        styles = {c.get('lineStyle') for c in self.connectors}
        self.assertEqual(styles, {'solid', 'dotted'})
        self.assertEqual(len(self.connectors), 10)

    def test_every_connector_names_its_anchor_points(self):
        # Без sourcePoint PIX ведёт линию от правой середины фигуры.
        for c in self.connectors:
            self.assertIn(int(c.get('sourcePoint')), PIX_HANDLES, c.get('id'))
            self.assertIn(int(c.get('targetPoint')), PIX_HANDLES, c.get('id'))

    def test_ends_lie_on_the_anchor_points(self):
        # Условие ``Ali`` клиента PIX: допуск 0,1 px, иначе линия перестраивается.
        for c in self.connectors:
            route = self._route(c)
            self.assertGreaterEqual(len(route), 2, c.get('id'))
            start = _handle_point(self.frames[c.get('sourceNodeId')], int(c.get('sourcePoint')))
            end = _handle_point(self.frames[c.get('targetNodeId')], int(c.get('targetPoint')))
            for got, want in ((route[0], start), (route[-1], end)):
                self.assertLessEqual(abs(got[0] - want[0]), 0.1, (c.get('id'), got, want))
                self.assertLessEqual(abs(got[1] - want[1]), 0.1, (c.get('id'), got, want))

    def test_every_segment_runs_along_an_axis(self):
        # Условие ``Cli``: косой отрезок — и студия выбрасывает всю ломаную.
        for c in self.connectors:
            for a, b in zip(self._route(c), self._route(c)[1:]):
                self.assertTrue(a[0] == b[0] or a[1] == b[1], (c.get('id'), a, b))

    def test_steps_that_are_not_level_get_no_micro_jog(self):
        # t1 и t2 стоят не вровень: середины их граней расходятся на 6 px.
        # Ступенька в 6 px читается как поломка — точки выбираются так, чтобы
        # её не было.
        for c in self.connectors:
            route = self._route(c)
            for a, b in zip(route[1:-2], route[2:-1]):
                self.assertGreaterEqual(max(abs(b[0] - a[0]), abs(b[1] - a[1])), MIN_SEGMENT,
                                        (c.get('id'), route))

    def test_connector_styles_are_ones_pix_recognises(self):
        for c in self.connectors:
            style = (c.get('lineStyle'), c.findtext('MarkerStart'), c.findtext('MarkerEnd'))
            self.assertIn(style, PIX_STYLES, c.get('id'))

    def test_package_passes_both_checks(self):
        self.assertEqual(validate_pmm_package(self.payload).errors, [])
        self.assertEqual(validate_pmm_for_pix(self.payload).errors, [])


class TitleIsNotASheetTest(unittest.TestCase):
    """Плашка заголовка — рамка с надписью, а не лист «Объекта данных»."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(DRAWIO, 'avgust.drawio')
        cls.frames = _frames(_map_root(generate_pmm_zip(cls.process)))

    def test_the_fixture_title_is_a_free_text_annotation(self):
        title = next(n for n in self.process.nodes if n.id == 'title')
        self.assertEqual((title.type, title.laneId), ('textAnnotation', None))

    def test_title_goes_as_an_empty_pool_with_its_font(self):
        titles = [f[4] for f in self.frames.values() if f[4].get('label') == TITLE]
        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].get('type'), 'emptyPool')
        self.assertEqual(titles[0].get('font_size'), '72')
        self.assertEqual((titles[0].get('width'), titles[0].get('height')), ('1400', '160'))

    def test_a_linked_note_is_a_frame_too(self):
        # Примечание, к которому ведёт пунктир, — тоже рамка, а не лист: лист
        # на карте аналитика не нарисован, а пунктир от «Пустого пула» к шагу
        # студия принимает (PIX банка, 2026-09-22).
        notes = [f[4] for f in self.frames.values() if f[4].get('label') == 'Izoh']
        self.assertEqual([n.get('type') for n in notes], ['emptyPool'])


class BpmnDataLinksReachPixTest(unittest.TestCase):
    """Связи шагов с документами и базами не пропадают при импорте BPMN."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(DRAWIO, 'avgust.drawio')
        cls.xml = generate_bpmn_xml(cls.process)
        cls.root = ET.fromstring(cls.xml.encode('utf-8'))

    def _ids(self, tag):
        return {el.get('id') for el in self.root.iter(f'{{{BPMN_NS["bpmn"]}}}{tag}')}

    def test_no_plain_association_touches_data(self):
        data = self._ids('dataObjectReference') | self._ids('dataStoreReference')
        self.assertEqual(len(data), 3)
        plain = [a for a in self.root.iter(f'{{{BPMN_NS["bpmn"]}}}association')
                 if {a.get('sourceRef'), a.get('targetRef')} & data]
        self.assertEqual(plain, [], 'такую связь импорт PIX выбрасывает')

    def test_data_links_sit_inside_their_steps(self):
        inputs, outputs = {}, {}
        for task in self.root.iter(f'{{{BPMN_NS["bpmn"]}}}userTask'):
            properties = {p.get('id') for p in task.findall('bpmn:property', BPMN_NS)}
            for link in task.findall('bpmn:dataInputAssociation', BPMN_NS):
                self.assertIn(link.findtext('bpmn:targetRef', namespaces=BPMN_NS), properties)
                inputs[link.findtext('bpmn:sourceRef', namespaces=BPMN_NS)] = task.get('id')
            for link in task.findall('bpmn:dataOutputAssociation', BPMN_NS):
                outputs[task.get('id')] = link.findtext('bpmn:targetRef', namespaces=BPMN_NS)
        self.assertEqual(set(inputs.values()), {'t1', 't2'})
        self.assertEqual(outputs, {'t3': 'arch'})

    def test_every_data_link_is_drawn(self):
        links = self._ids('dataInputAssociation') | self._ids('dataOutputAssociation')
        drawn = {e.get('bpmnElement') for e in self.root.iter(f'{{{BPMN_NS["bpmndi"]}}}BPMNEdge')}
        self.assertEqual(len(links), 3)
        self.assertLessEqual(links, drawn)

    def test_a_note_link_stays_an_association(self):
        # PIX принимает ассоциацию к примечанию — делает из неё выноску.
        notes = self._ids('textAnnotation')
        linked = [a for a in self.root.iter(f'{{{BPMN_NS["bpmn"]}}}association')
                  if {a.get('sourceRef'), a.get('targetRef')} & notes]
        self.assertEqual(len(linked), 1)

    def test_file_passes_both_checks(self):
        self.assertEqual(validate_bpmn_xml(self.xml).errors, [])
        self.assertEqual(validate_bpmn_for_pix(self.xml).errors, [])

    def test_round_trip_keeps_every_data_link(self):
        reopened = parse_bpmn_xml(self.xml, 'avgust.bpmn')
        data = {n.id for n in reopened.nodes if n.type in ('dataStore', 'dataObject')}
        linked = {end for e in reopened.edges if e.kind == 'association'
                  for end in (e.sourceId, e.targetId)}
        self.assertEqual(data - linked, set(), 'документ или база потеряли связь')


POOLS = '''<mxfile host="test"><diagram id="d" name="Page-1"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="client" value="Mijoz" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="900" height="120" as="geometry"/></mxCell>
  <mxCell id="bank" value="Bank" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
    <mxGeometry x="0" y="200" width="900" height="260" as="geometry"/></mxCell>
  <mxCell id="s" value="Start" style="shape=mxgraph.bpmn.event;symbol=general;outline=standard;" vertex="1" parent="1">
    <mxGeometry x="60" y="280" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="g" value="" style="shape=mxgraph.bpmn.gateway2;gwType=exclusive;" vertex="1" parent="1">
    <mxGeometry x="160" y="275" width="50" height="50" as="geometry"/></mxCell>
  <mxCell id="t" value="Arizani ko'rish" style="rounded=1;" vertex="1" parent="1">
    <mxGeometry x="300" y="260" width="160" height="80" as="geometry"/></mxCell>
  <mxCell id="e" value="End" style="shape=mxgraph.bpmn.event;symbol=general;outline=end;" vertex="1" parent="1">
    <mxGeometry x="600" y="280" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="f1" edge="1" parent="1" source="s" target="g"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f2" value="Ha" edge="1" parent="1" source="g" target="t"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="f3" edge="1" parent="1" source="t" target="e"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="c1" style="dashed=1;" edge="1" parent="1" source="g" target="client"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="m1" style="dashed=1;" edge="1" parent="1" source="t" target="client"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>'''


class BpmnChildOrderTest(unittest.TestCase):
    """Порядок элементов — по схеме BPMN 2.0: иначе PIX не открывает файл.

    Проба на PIX банка: ассоциация «пул ↔ шлюз», вставшая в collaboration
    перед потоком сообщений, — «Файл не является диаграммой BPMN 2.0»; та же
    ассоциация после потоков — карта открылась.
    """

    def test_collaboration_lists_message_flows_before_associations(self):
        xml = generate_bpmn_xml(parse_drawio_xml(POOLS, 'pools.drawio'))
        collab = ET.fromstring(xml.encode('utf-8')).find('bpmn:collaboration', BPMN_NS)
        tags = [child.tag.split('}')[1] for child in collab]
        self.assertIn('association', tags, 'фикстура обязана дать ассоциацию к пулу')
        self.assertIn('messageFlow', tags)
        self.assertEqual(tags, sorted(tags, key=['participant', 'messageFlow', 'association'].index))
        self.assertEqual([p.code for p in validate_bpmn_for_pix(xml).errors], [])

    def test_the_checker_catches_the_wrong_order(self):
        # Контроль самого правила: та же выгрузка с ассоциацией перед потоком.
        xml = generate_bpmn_xml(parse_drawio_xml(POOLS, 'pools.drawio'))
        assoc = next(line for line in xml.splitlines() if '<bpmn:association ' in line
                     and 'Participant' in line)
        broken = xml.replace(assoc + '\n', '').replace(
            '    <bpmn:messageFlow ', assoc + '\n    <bpmn:messageFlow ', 1)
        self.assertIn('pix_bpmn_child_order', [p.code for p in validate_bpmn_for_pix(broken).errors])

    def test_sub_pages_put_every_process_before_the_diagrams(self):
        from test_export_readback_kinds import FILE

        xml = generate_bpmn_xml(parse_drawio_xml(FILE, 'pages.drawio'))
        tops = [child.tag.split('}')[1] for child in ET.fromstring(xml.encode('utf-8'))]
        self.assertEqual(tops.count('BPMNDiagram'), 2, 'фикстура обязана дать две страницы')
        first_diagram = tops.index('BPMNDiagram')
        self.assertTrue(all(t == 'BPMNDiagram' for t in tops[first_diagram:]), tops)
        self.assertEqual([p.code for p in validate_bpmn_for_pix(xml).errors], [])


BUS = '''<mxfile host="test"><diagram id="d" name="Page-1"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="1200" height="600" as="geometry"/></mxCell>
  <mxCell id="p" value="" style="shape=mxgraph.bpmn.gateway2;gwType=parallel;" vertex="1" parent="1">
    <mxGeometry x="100" y="265" width="50" height="50" as="geometry"/></mxCell>
  <mxCell id="t3" value="3. Kelishish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="250" y="80" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="t4" value="4. Kelishish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="250" y="250" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="t5" value="5. Kelishish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="250" y="420" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="x" value="Kelishildimi?" style="shape=mxgraph.bpmn.gateway2;gwType=exclusive;" vertex="1" parent="1">
    <mxGeometry x="500" y="265" width="50" height="50" as="geometry"/></mxCell>
  <mxCell id="done" value="6. Imzolash" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="650" y="250" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="a3" edge="1" parent="1" source="p" target="t3"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="a4" edge="1" parent="1" source="p" target="t4"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="a5" edge="1" parent="1" source="p" target="t5"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="carrier" style="edgeStyle=orthogonalEdgeStyle;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="1" source="t3" target="x">
    <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="420" y="120"/><mxPoint x="420" y="290"/></Array></mxGeometry></mxCell>
  <mxCell id="bus" style="edgeStyle=elbowEdgeStyle;endArrow=none;exitX=1;exitY=0.5;entryX=1;entryY=0.5;" edge="1" parent="1" source="t4" target="t5">
    <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="420" y="380"/></Array></mxGeometry></mxCell>
  <mxCell id="ha" value="Ha" edge="1" parent="1" source="x" target="done"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>'''


class MergeBusTest(unittest.TestCase):
    """Шина draw.io сводит параллельные шаги в ромб, а не соединяет их друг с другом."""

    def setUp(self):
        self.process = parse_drawio_xml(BUS, 'bus.drawio')
        self.pairs = {(e.sourceId, e.targetId) for e in self.process.edges}

    def test_the_bus_is_not_a_flow_between_the_steps(self):
        self.assertNotIn(('t4', 't5'), self.pairs)

    def test_every_step_on_the_bus_reaches_the_gateway(self):
        incoming = {s for s, t in self.pairs if t == 'x'}
        self.assertEqual(incoming, {'t3', 't4', 't5'})

    def test_joined_flow_follows_the_bus(self):
        joined = next(e for e in self.process.edges if (e.sourceId, e.targetId) == ('t5', 'x'))
        self.assertTrue(all(p.x == 420 for p in joined.points), joined.points)

    def test_an_arrowless_line_without_a_carrier_stays(self):
        lonely = BUS.replace('source="t3" target="x"', 'source="t3" target="done"').replace(
            '<mxPoint x="420" y="120"/><mxPoint x="420" y="290"/>', '<mxPoint x="600" y="120"/>')
        pairs = {(e.sourceId, e.targetId) for e in parse_drawio_xml(lonely, 'b.drawio').edges}
        self.assertIn(('t4', 't5'), pairs)


FORK = '''<mxfile host="test"><diagram id="d" name="Page-1"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
    <mxGeometry x="1800" y="1000" width="800" height="1100" as="geometry"/></mxCell>
  <mxCell id="k" value="Bildirishnomani kelishish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="2080" y="1760" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="i" value="Bildirishnomani imzolash" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="2080" y="1920" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="b" value="Boshqaruv Raisi o'rinbosariga bildirishnoma yuborish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="2320" y="1040" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="carrier" style="edgeStyle=elbowEdgeStyle;endArrow=classic;exitX=0;exitY=0.5;exitDx=0;exitDy=0;exitPerimeter=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;entryPerimeter=0;" edge="1" parent="1" source="k" target="i">
    <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="2030" y="1880"/></Array></mxGeometry></mxCell>
  <mxCell id="bus" style="edgeStyle=elbowEdgeStyle;endArrow=none;startArrow=none;exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;" edge="1" parent="1" source="k" target="b">
    <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="2270" y="1440"/></Array></mxGeometry></mxCell>
</root></mxGraphModel></diagram></mxfile>'''


class ForkIsNotABusTest(unittest.TestCase):
    """Две линии, выходящие из одного шага, — не шина (Tovarlar, шаги у x = 2080).

    Линия без стрелки «Bildirishnomani kelishish — Boshqaruv…» и переход
    «kelishish → imzolash» касались только общей начальной точкой, и импорт
    принимал это за слияние: появлялся переход Boshqaruv… → imzolash, которого
    на карте нет. В PIX он входил в «imzolash» слева, пройдя сквозь сам шаг.
    """

    def test_no_flow_from_the_far_step_into_the_carrier_target(self):
        pairs = {(e.sourceId, e.targetId) for e in parse_drawio_xml(FORK, 'fork.drawio').edges}
        self.assertNotIn(('b', 'i'), pairs)
        self.assertIn(('k', 'i'), pairs)


BRACKETS = '''<mxfile host="test"><diagram id="d" name="Page-1"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="900" height="600" as="geometry"/></mxCell>
  <mxCell id="p" value="4. Farmoyishni kelishuvga kiritish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="50" y="250" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="t1" value="5. Farmoyish kelishish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="250" y="80" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="t2" value="6. Farmoyish kelishish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="250" y="250" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="t3" value="7. Farmoyish kelishish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="250" y="420" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="x" value="Kelishildimi?" style="shape=mxgraph.bpmn.gateway2;gwType=exclusive;" vertex="1" parent="1">
    <mxGeometry x="500" y="265" width="50" height="50" as="geometry"/></mxCell>
  <mxCell id="done" value="8. Imzolash" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="650" y="250" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="in" style="edgeStyle=orthogonalEdgeStyle;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="1" source="p" target="t1">
    <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="210" y="290"/><mxPoint x="210" y="120"/></Array></mxGeometry></mxCell>
  <mxCell id="left12" style="edgeStyle=elbowEdgeStyle;endArrow=none;startArrow=none;exitX=0;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="1" source="t1" target="t2">
    <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="210" y="205"/></Array></mxGeometry></mxCell>
  <mxCell id="left23" style="edgeStyle=elbowEdgeStyle;endArrow=none;startArrow=none;exitX=0;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="1" source="t3" target="t2">
    <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="210" y="375"/></Array></mxGeometry></mxCell>
  <mxCell id="out" style="edgeStyle=orthogonalEdgeStyle;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="1" source="t1" target="x">
    <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="420" y="120"/><mxPoint x="420" y="290"/></Array></mxGeometry></mxCell>
  <mxCell id="right12" style="edgeStyle=elbowEdgeStyle;endArrow=none;startArrow=none;exitX=1;exitY=0.5;entryX=1;entryY=0.5;" edge="1" parent="1" source="t1" target="t2">
    <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="420" y="205"/></Array></mxGeometry></mxCell>
  <mxCell id="right23" style="edgeStyle=elbowEdgeStyle;endArrow=none;startArrow=none;exitX=1;exitY=0.5;entryX=1;entryY=0.5;" edge="1" parent="1" source="t2" target="t3">
    <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="420" y="375"/></Array></mxGeometry></mxCell>
  <mxCell id="ha" value="Ha" edge="1" parent="1" source="x" target="done"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>'''


class BracketGroupTest(unittest.TestCase):
    """Согласующие столбиком со скобками без стрелок: вход раздаётся, выход сводится («1.3», 217 → 218–220).

    Скобка слева прицеплена к шагам с той же грани, в которую входит стрелка
    «4 → 5», — это раздача: в 6 и 7 входит тот же переход. Скобка справа
    сводит выходы в ромб. Раньше левая скобка оставалась переходом между
    согласующими, и шаг 7 выходил недостижимым.
    """

    def setUp(self):
        self.pairs = {(e.sourceId, e.targetId) for e in parse_drawio_xml(BRACKETS, 'brackets.drawio').edges
                      if e.kind == 'sequenceFlow'}

    def test_the_incoming_arrow_reaches_every_step_of_the_group(self):
        self.assertEqual({s for s, t in self.pairs if t in ('t1', 't2', 't3')}, {'p'})
        self.assertTrue({('p', 't1'), ('p', 't2'), ('p', 't3')} <= self.pairs, self.pairs)

    def test_every_step_of_the_group_reaches_the_gateway(self):
        self.assertEqual({s for s, t in self.pairs if t == 'x'}, {'t1', 't2', 't3'})

    def test_no_flow_between_the_approvers(self):
        between = {(s, t) for s, t in self.pairs if s in ('t1', 't2', 't3') and t in ('t1', 't2', 't3')}
        self.assertEqual(between, set())

    def test_a_line_from_the_right_face_to_the_left_face_is_not_a_bracket(self):
        """«Tovarlar»: kelishish — Boshqaruv… без стрелки и «imzolash → Boshqaruv…» в ту же грань."""
        lined = BRACKETS.replace(
            'exitX=0;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="1" source="t1" target="t2"',
            'exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="1" source="p" target="t2"')
        pairs = {(e.sourceId, e.targetId) for e in parse_drawio_xml(lined, 'b.drawio').edges if e.kind == 'sequenceFlow'}
        self.assertNotIn(('t1', 'p'), pairs)


DOTTED_STUB = '''<mxfile host="test"><diagram id="d" name="Page-1"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="600" height="300" as="geometry"/></mxCell>
  <mxCell id="a" value="213. Sotish uchun e-auksionga qo'yish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="100" y="100" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="b" value="214. G'olib bilan bog'lanish" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">
    <mxGeometry x="260" y="100" width="160" height="80" as="geometry"/></mxCell>
  <mxCell id="stub" style="edgeStyle=elbowEdgeStyle;dashed=1;dashPattern=1 4;endArrow=none;startArrow=none;exitX=0;exitY=0.25;" edge="1" parent="1" source="b">
    <mxGeometry relative="1" as="geometry"><mxPoint x="238" y="90" as="targetPoint"/></mxGeometry></mxCell>
</root></mxGraphModel></diagram></mxfile>'''


class DottedStubTest(unittest.TestCase):
    """Точечный хвостик без стрелок у соседнего шага — не переход («1.3»: «214 → 213»)."""

    def test_the_stub_is_decoration(self):
        edges = parse_drawio_xml(DOTTED_STUB, 'stub.drawio').edges
        self.assertFalse([e for e in edges if e.kind == 'sequenceFlow'], [(e.sourceId, e.targetId, e.kind) for e in edges])


class DrawioElbowTest(unittest.TestCase):
    """Пунктир «локтем» рисуется ровно так, как его рисует draw.io.

    ``fixtures/drawio_elbow_cases.json`` посчитан исходными функциями draw.io
    (jgraph/drawio: ``mxEdgeStyle.ElbowConnector``, ``SideToSide``,
    ``TopToBottom`` и ``mxPerimeter.RectanglePerimeter``) на случайных рамках,
    закреплённых и свободных концах, с контрольной точкой и без.
    """

    def test_port_matches_drawio(self):
        import json
        from app.models.process import Geometry, ProcessEdge, ProcessEdgePoint, ProcessNode
        from app.services.edge_routing import _simplify, drawio_elbow_route

        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'drawio_elbow_cases.json')
        with open(path, encoding='utf-8') as fh:
            cases = json.load(fh)
        self.assertEqual(len(cases), 300)
        for n, c in enumerate(cases):
            def node(nid, box):
                return ProcessNode(id=nid, name='', type='task',
                                   geometry=Geometry(x=box[0], y=box[1], width=box[2], height=box[3]))
            edge = ProcessEdge(
                id='e', kind='association', sourceId='s', targetId='t', edgeStyle='elbowEdgeStyle',
                exitX=(c['exit'] or [None, None])[0], exitY=(c['exit'] or [None, None])[1],
                entryX=(c['entry'] or [None, None])[0], entryY=(c['entry'] or [None, None])[1],
                points=[ProcessEdgePoint(x=c['pt'][0], y=c['pt'][1])] if c['pt'] else [],
                style='elbow=vertical;' if c['elbow'] else '')
            ours = drawio_elbow_route(edge, node('s', c['src']), node('t', c['tgt']))
            want = _simplify([tuple(p) for p in c['route']])
            self.assertEqual(len(ours), len(want), (n, ours, want))
            for a, b in zip(ours, want):
                self.assertAlmostEqual(a[0], b[0], places=6, msg=n)
                self.assertAlmostEqual(a[1], b[1], places=6, msg=n)


if __name__ == '__main__':
    unittest.main()
