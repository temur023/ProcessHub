"""Куда выгрузка кладёт подписи, которые BPMN рисует вне фигуры.

Подпись события, ветки шлюза и времени шага bpmn.io печатает отдельным блоком
по координатам из ``BPMNLabel``. Пока в списке занятых мест были только фигуры
и линии, такой блок садился на заголовок дорожки — полосу, в которой редактор
печатает повёрнутое название, — и два текста читались один поверх другого.
Ровно это и было видно в выгруженной карте.

Карта в фикстуре нарочно тесная: событие стоит у самого левого края дорожки,
а сообщение приходит из полосы внешнего участника — оба тянут подпись в
заголовочную полосу.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.bpmn_exporter import (
    LANE_BORDER,
    LANE_HEADER,
    POOL_HEADER,
    generate_bpmn_xml,
)
from app.services.drawio_parser import parse_drawio_xml

CROWDED_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <mxCell id="lane_client" value="Mijoz" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="900" height="90" as="geometry" />
    </mxCell>

    <mxCell id="lane_office" value="Devonxona xizmati" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="90" width="900" height="220" as="geometry" />
    </mxCell>
    <mxCell id="start_1" value="Xat keldi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane_office">
      <mxGeometry x="40" y="30" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="task_1" value="Xatni qabul qilib oladi&#10;5 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_office">
      <mxGeometry x="120" y="20" width="160" height="60" as="geometry" />
    </mxCell>
    <mxCell id="gw_1" value="To'g'ri yo'naltirilganmi?" style="rhombus;html=1;" vertex="1" parent="lane_office">
      <mxGeometry x="330" y="30" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="task_2" value="Rezolyutsiya tayyorlanadi&#10;2 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_office">
      <mxGeometry x="420" y="20" width="160" height="60" as="geometry" />
    </mxCell>

    <mxCell id="lane_deputy" value="Boshqaruv Raisi o'rinbosari" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="310" width="900" height="200" as="geometry" />
    </mxCell>
    <mxCell id="timer_1" value="Ожидание 60 мин" style="shape=mxgraph.bpmn.event;html=1;symbol=timer;outline=standard;" vertex="1" parent="lane_deputy">
      <mxGeometry x="45" y="20" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="task_3" value="Xatning mazmuni bilan tanishib chiqadi&#10;2 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_deputy">
      <mxGeometry x="130" y="10" width="180" height="60" as="geometry" />
    </mxCell>

    <mxCell id="flow_msg" value="xat beradi" style="dashed=1;html=1;" edge="1" parent="1" source="lane_client" target="start_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="flow_1" value="" style="html=1;" edge="1" parent="1" source="start_1" target="task_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="flow_2" value="" style="html=1;" edge="1" parent="1" source="task_1" target="gw_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="flow_3" value="Ha" style="html=1;" edge="1" parent="1" source="gw_1" target="task_2">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="flow_4" value="Yo'q" style="html=1;" edge="1" parent="1" source="gw_1" target="timer_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="flow_5" value="" style="html=1;" edge="1" parent="1" source="timer_1" target="task_3">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""

_DI = re.compile(
    r'<bpmndi:BPMN(Shape|Edge) id="[^"]+" bpmnElement="([^"]+)"[^>]*>(.*?)</bpmndi:BPMN\1>',
    re.S,
)
_BOUNDS = re.compile(r'<dc:Bounds x="(-?\d+)" y="(-?\d+)" width="(\d+)" height="(\d+)"')
_LABEL = re.compile(r'<bpmndi:BPMNLabel>\s*<dc:Bounds x="(-?\d+)" y="(-?\d+)" width="(\d+)" height="(\d+)"', re.S)


#: Меньше пары пикселей по строке текста глаз не замечает — это не наложение.
TOUCH = 40


def _overlap(a, b):
    dx = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    dy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return dx * dy if dx > 0 and dy > 0 else 0


def _collides(a, b):
    return _overlap(a, b) > TOUCH


def _diagram(xml):
    """Фигуры, подписи и признак «это дорожка» из BPMNDI."""
    shapes, labels = [], []
    for kind, element, body in _DI.findall(xml):
        bounds = _BOUNDS.search(body)
        if kind == 'Shape' and bounds:
            shapes.append((element, tuple(int(v) for v in bounds.groups())))
        label = _LABEL.search(body)
        if label:
            labels.append((element, tuple(int(v) for v in label.groups())))
    containers = set(re.findall(r'<bpmn:lane id="([^"]+)"', xml))
    containers |= set(re.findall(r'<bpmn:participant id="([^"]+)"', xml))
    return shapes, labels, containers


_EDGE = re.compile(
    r'<bpmndi:BPMNEdge id="[^"]+" bpmnElement="([^"]+)">(.*?)</bpmndi:BPMNEdge>', re.S)
_WAYPOINT = re.compile(r'<di:waypoint x="(-?\d+)" y="(-?\d+)"')


def _flow_labels(xml):
    """Подписи связей вместе с ломаной своей линии."""
    out = []
    for element, body in _EDGE.findall(xml):
        label = _LABEL.search(body)
        if not label:
            continue
        route = [(int(x), int(y)) for x, y in _WAYPOINT.findall(body)]
        out.append((element, tuple(int(v) for v in label.groups()), route))
    return out


def _distance_to_route(point, route):
    """Насколько центр подписи отстоит от своей линии."""
    px, py = point
    best = float('inf')
    for (x1, y1), (x2, y2) in zip(route, route[1:]):
        dx, dy = x2 - x1, y2 - y1
        length = dx * dx + dy * dy
        t = 0.0 if not length else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length))
        best = min(best, ((x1 + dx * t - px) ** 2 + (y1 + dy * t - py) ** 2) ** 0.5)
    return best


class BpmnLabelPlacementTests(unittest.TestCase):
    def setUp(self):
        process = parse_drawio_xml(CROWDED_MAP, 'crowded.drawio')
        self.xml = generate_bpmn_xml(process)
        self.shapes, self.labels, self.containers = _diagram(self.xml)

    def test_map_has_external_labels(self):
        """Без подписей вне фигур проверять было бы нечего."""
        self.assertGreater(len(self.labels), 3, self.xml)

    def test_labels_avoid_lane_headers(self):
        """Подпись не садится в полосу с названием дорожки или пула."""
        hits = []
        for owner, box in self.labels:
            for element, (x, y, _w, h) in self.shapes:
                if element not in self.containers:
                    continue
                header = max(POOL_HEADER, LANE_HEADER)
                if _collides(box, (x, y, header, h)):
                    hits.append((owner, element))
        self.assertEqual(hits, [], f'подписи легли на заголовок дорожки: {hits}')

    def test_labels_avoid_lane_dividers(self):
        """Подпись не ложится на линию между дорожками — иначе она перечёркнута."""
        hits = []
        for owner, box in self.labels:
            for element, (x, y, w, h) in self.shapes:
                if element not in self.containers:
                    continue
                for edge_y in (y, y + h):
                    if _collides(box, (x, edge_y - LANE_BORDER, w, 2 * LANE_BORDER)):
                        hits.append((owner, element))
        self.assertEqual(hits, [], f'подписи легли на разделитель дорожек: {hits}')

    def test_labels_do_not_stack_on_each_other(self):
        """Две подписи не печатаются одна поверх другой."""
        hits = []
        for i, (owner_a, box_a) in enumerate(self.labels):
            for owner_b, box_b in self.labels[i + 1:]:
                if _collides(box_a, box_b):
                    hits.append((owner_a, owner_b))
        self.assertEqual(hits, [], f'подписи наложились друг на друга: {hits}')

    def test_labels_stay_off_foreign_shapes(self):
        """Подпись не накрывает чужую фигуру — её текст рисуется внутри."""
        hits = []
        for owner, box in self.labels:
            for element, sbox in self.shapes:
                if element in self.containers or element == owner:
                    continue
                if _collides(box, sbox):
                    hits.append((owner, element))
        self.assertEqual(hits, [], f'подписи накрыли фигуры: {hits}')


ROOMY_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <mxCell id="lane_office" value="Devonxona xizmati" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="1200" height="700" as="geometry" />
    </mxCell>
    <mxCell id="gw_1" value="" style="rhombus;html=1;" vertex="1" parent="lane_office">
      <mxGeometry x="300" y="100" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="task_yes" value="Xat tayyorlanadi" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_office">
      <mxGeometry x="700" y="95" width="160" height="60" as="geometry" />
    </mxCell>
    <mxCell id="task_no" value="Xat qaytariladi" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_office">
      <mxGeometry x="245" y="450" width="160" height="60" as="geometry" />
    </mxCell>

    <mxCell id="flow_yes" value="Yo'q" style="html=1;" edge="1" parent="1" source="gw_1" target="task_yes">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="flow_no" value="Ha" style="html=1;" edge="1" parent="1" source="gw_1" target="task_no">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""


class BranchLabelOnLineTests(unittest.TestCase):
    """Подпись ветки шлюза стоит на своей линии, а не рядом с ней.

    Так её ставит аналитик в draw.io: ``edgeLabel`` с перпендикулярным
    смещением в 0–3 px, то есть по центру связи. Выгрузка это выбрасывала и
    отводила подпись на 13 px над горизонтальным отрезком и на 24 px правее
    вертикального. Формально текст ничем не перечёркнут, но у шлюза с двумя
    ветками такая подпись висит между линиями, и к какой из них она относится,
    по карте уже не понять.

    Карта в фикстуре нарочно просторная: вокруг обеих веток пусто, и отводить
    подпись в сторону выгрузке не от чего.
    """

    def setUp(self):
        process = parse_drawio_xml(ROOMY_MAP, 'roomy.drawio')
        self.labels = _flow_labels(generate_bpmn_xml(process))

    def test_both_branches_are_labelled(self):
        """Без подписей веток проверять было бы нечего."""
        self.assertEqual(len(self.labels), 2, self.labels)

    def test_branch_labels_sit_on_their_own_line(self):
        off = []
        for element, (lx, ly, lw, lh), route in self.labels:
            distance = _distance_to_route((lx + lw / 2, ly + lh / 2), route)
            if distance > 1:
                off.append((element, round(distance, 1)))
        self.assertEqual(off, [], f'подписи веток отошли от своей линии: {off}')


# Оба цилиндра подписаны СВЕРХУ (`verticalLabelPosition=top`), и оба стоят
# сразу под границей дорожки: место над ними занято разделителем, а под
# ними — чисто. Ровно эта пара условий и разворачивала подпись на другую
# сторону фигуры.
STORE_UNDER_DIVIDER = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <mxCell id="lane_front" value="Front-ofis" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="900" height="200" as="geometry" />
    </mxCell>
    <mxCell id="task_front" value="Mijozni qabul qilish&#10;2 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_front">
      <mxGeometry x="120" y="60" width="180" height="80" as="geometry" />
    </mxCell>

    <mxCell id="lane_back" value="Bek-ofis" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="200" width="900" height="240" as="geometry" />
    </mxCell>
    <mxCell id="task_back" value="Hujjatlarni tekshirish&#10;3 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_back">
      <mxGeometry x="120" y="66" width="180" height="80" as="geometry" />
    </mxCell>
    <mxCell id="store_iabs" value="IABS" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;align=center;verticalAlign=bottom;" vertex="1" parent="lane_back">
      <mxGeometry x="190" y="8" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="dash_iabs" style="rounded=0;orthogonalLoop=1;jettySize=auto;html=1;entryX=0.5;entryY=0;entryDx=0;entryDy=0;entryPerimeter=0;dashed=1;" edge="1" parent="lane_back" source="store_iabs" target="task_back">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>

    <mxCell id="task_eha" value="Xatni imzolash&#10;1 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_back">
      <mxGeometry x="520" y="66" width="180" height="80" as="geometry" />
    </mxCell>
    <mxCell id="store_eha" value="EHA" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;align=center;verticalAlign=bottom;" vertex="1" parent="lane_back">
      <mxGeometry x="590" y="8" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="dash_eha" style="rounded=0;orthogonalLoop=1;jettySize=auto;html=1;entryX=0.5;entryY=0;entryDx=0;entryDy=0;entryPerimeter=0;dashed=1;" edge="1" parent="lane_back" source="store_eha" target="task_eha">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>

    <mxCell id="flow_1" value="" style="html=1;" edge="1" parent="1" source="task_front" target="task_back">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="flow_2" value="" style="html=1;" edge="1" parent="1" source="task_back" target="task_eha">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""


class StoreLabelSideTests(unittest.TestCase):
    """Подпись цилиндра остаётся на стороне, выбранной аналитиком.

    В draw.io у каждого цилиндра банка стоит ``verticalLabelPosition=top``, и
    «IABS» напечатано НАД ним. Выгрузка перебирала позиции так, что сразу за
    стороной аналитика шла противоположная, и стоило месту над цилиндром
    оказаться занятым — подпись уезжала ПОД цилиндр. Занимал это место обычно
    даже не сосед, а разделитель дорожек: он проходит тонкой полосой вплотную
    над фигурой, подпись задевает его краем на пару пикселей, а заезжать на
    разделитель втрое дороже обычного наложения.

    На карте от этого одни системы подписаны сверху, другие снизу; подпись
    оказывается ближе к чужой фигуре, чем к своей, и читается как её.
    """

    def setUp(self):
        process = parse_drawio_xml(STORE_UNDER_DIVIDER, 'stores.drawio')
        self.xml = generate_bpmn_xml(process)
        self.stores = {
            n.id: (n.geometry.x, n.geometry.y, n.geometry.width, n.geometry.height)
            for n in process.nodes if n.type == 'dataStore'
        }
        self.shapes, self.labels, _ = _diagram(self.xml)

    def test_both_stores_reached_the_file(self):
        """Без цилиндров проверять было бы нечего."""
        self.assertEqual(len(self.stores), 2, self.stores)

    def test_store_labels_stay_above_their_cylinder(self):
        below = []
        for owner, (lx, ly, lw, lh) in self.labels:
            box = self.stores.get(owner)
            if box is None:
                continue
            if ly + lh > box[1]:
                below.append((owner, (lx, ly, lw, lh), box))
        self.assertEqual(
            below, [], f'подпись цилиндра уехала под фигуру: {below}')

    def test_store_labels_stay_over_their_own_cylinder(self):
        """Подпись стоит над СВОИМ цилиндром, а не съезжает к соседнему."""
        off = []
        for owner, (lx, ly, lw, lh) in self.labels:
            box = self.stores.get(owner)
            if box is None:
                continue
            if abs((lx + lw / 2) - (box[0] + box[2] / 2)) > 2:
                off.append((owner, lx + lw / 2, box[0] + box[2] / 2))
        self.assertEqual(off, [], f'подпись съехала вбок от своего цилиндра: {off}')


# Три цилиндра «EHA» подписаны сверху и стоят в 8, 18 и 25 px под границей
# дорожки — ровно так они стоят на «Asosiy hisob raqam ochish».
STORES_UNDER_DIVIDER_AT_DEPTHS = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <mxCell id="lane_front" value="Front-ofis" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="1200" height="200" as="geometry" />
    </mxCell>
    <mxCell id="task_front" value="Mijozni qabul qilish&#10;2 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_front">
      <mxGeometry x="920" y="60" width="180" height="80" as="geometry" />
    </mxCell>

    <mxCell id="lane_back" value="Bek-ofis" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="200" width="1200" height="240" as="geometry" />
    </mxCell>
    <mxCell id="store_8" value="EHA" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;align=center;verticalAlign=bottom;" vertex="1" parent="lane_back">
      <mxGeometry x="190" y="8" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="store_18" value="EHA" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;align=center;verticalAlign=bottom;" vertex="1" parent="lane_back">
      <mxGeometry x="420" y="18" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="store_25" value="EHA" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;align=center;verticalAlign=bottom;" vertex="1" parent="lane_back">
      <mxGeometry x="650" y="25" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="task_back" value="Hujjatlarni tekshirish&#10;3 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_back">
      <mxGeometry x="900" y="130" width="220" height="80" as="geometry" />
    </mxCell>
    <mxCell id="dash_8" style="html=1;dashed=1;" edge="1" parent="lane_back" source="store_8" target="task_back">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="dash_18" style="html=1;dashed=1;" edge="1" parent="lane_back" source="store_18" target="task_back">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="dash_25" style="html=1;dashed=1;" edge="1" parent="lane_back" source="store_25" target="task_back">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>

    <mxCell id="flow_1" value="" style="html=1;" edge="1" parent="1" source="task_front" target="task_back">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""


class StoreLabelStaysNextToCylinderTests(unittest.TestCase):
    """Подпись цилиндра не отрывается от него, когда рядом разделитель дорожек.

    В draw.io «EHA» напечатано вплотную над цилиндром. Выгрузка считала
    разделитель полосой в 8 px и сравнивала её со всей рамкой подписи, вместе
    с пустыми полями. Если цилиндр стоял ближе 30 px к границе дорожки, место
    над ним считалось перечёркнутым, и подпись переносилась на целую «полку»
    вверх — за разделитель, в чужую дорожку, в 34 px от своего цилиндра. На
    карте «EHA» висело посреди соседней дорожки.
    """

    def setUp(self):
        process = parse_drawio_xml(STORES_UNDER_DIVIDER_AT_DEPTHS, 'depths.drawio')
        self.xml = generate_bpmn_xml(process)
        self.stores = {
            n.id: (n.geometry.x, n.geometry.y, n.geometry.width, n.geometry.height)
            for n in process.nodes if n.type == 'dataStore'
        }
        shapes, labels, _ = _diagram(self.xml)
        self.labels = {owner: box for owner, box in labels if owner in self.stores}
        self.divider = dict(shapes)['lane_back'][1]

    def test_every_store_is_labelled(self):
        self.assertEqual(sorted(self.stores), ['store_18', 'store_25', 'store_8'])
        self.assertEqual(set(self.labels), set(self.stores), self.xml)

    def test_labels_sit_right_above_their_cylinder(self):
        off = []
        for owner, (lx, ly, lw, lh) in self.labels.items():
            x, y, w, _h = self.stores[owner]
            distance = y - (ly + lh)
            centred = abs((lx + lw / 2) - (x + w / 2)) <= 2
            if not (0 <= distance <= 12 and centred):
                off.append((owner, (lx, ly, lw, lh), distance))
        self.assertEqual(off, [], f'подпись оторвалась от своего цилиндра: {off}')

    def test_label_stays_in_its_lane_when_there_is_room(self):
        """Над цилиндром в 18 и 25 px от границы текст помещается в своей дорожке."""
        outside = []
        for owner in ('store_18', 'store_25'):
            _lx, ly, _lw, _lh = self.labels[owner]
            # bpmn.io оставляет над строкой 2 px поля: текст начинается ниже.
            if ly + 2 <= self.divider:
                outside.append((owner, self.labels[owner]))
        self.assertEqual(outside, [], f'подпись ушла за разделитель в чужую дорожку: {outside}')


if __name__ == '__main__':
    unittest.main()

# Подпись ИТ-системы — только под своим цилиндром. Раньше, если под ним шла
# чужая связь, подпись уходила вбок на полширины или ниже: на карте «1.53»
# «EHA» у шага 345 стояла левее цилиндра, а у шага 344 — на месте, и так по
# всем картам банка была сдвинута каждая седьмая подпись.
class DataLabelStaysOnItsSideTest(unittest.TestCase):
    def test_every_candidate_is_right_under_the_cylinder(self):
        from app.models.process import Geometry, ProcessNode
        from app.services.layout import EXTERNAL_LABEL_GAP, node_label_candidates

        store = ProcessNode(
            id='s', type='dataStore', name='EHA',
            style='shape=datastore;html=1;labelPosition=center;verticalLabelPosition=bottom;align=center;verticalAlign=top;',
            geometry=Geometry(x=275, y=90, width=30, height=20))
        g = store.geometry
        for x, y, w, h in node_label_candidates(store):
            self.assertEqual(x + w / 2, g.x + g.width / 2)
            self.assertTrue(g.y + g.height <= y <= g.y + g.height + EXTERNAL_LABEL_GAP, (x, y, w, h))
