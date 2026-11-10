"""Раскладка дорожек и обход фигур — рефакторинг геометрии выгрузки.

Три вещи, которые ломались молча и обнаруживались только глазами в PIX:
дорожки не замощали пул, связи с данными выгружались не тем элементом, а
линии шли сквозь чужие фигуры. Каждой отвечает свой раздел.
"""
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import Geometry, ProcessEdge, ProcessEdgePoint, ProcessNode
from app.services.bpmn_exporter import (
    EVENT_SIDE,
    band_label,
    duration_markers,
    generate_bpmn_xml,
    map_label,
    marker_box,
)
from app.services.drawio_parser import parse_bpmn_xml, parse_drawio_xml
import app.services.edge_routing as er
from app.services.edge_routing import (
    Corridors,
    Obstacles,
    build_obstacles,
    orthogonal_waypoints,
)
from app.services.layout import (
    MIN_EXTERNAL_LABEL_WIDTH,
    external_label_candidates,
    text_width,
)
from app.services.map_layout import build_lane_stack, pool_bounds, stack_lanes

BPMN = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
DI = 'http://www.omg.org/spec/BPMN/20100524/DI'
DC = 'http://www.omg.org/spec/DD/20100524/DC'


# Дорожки нарисованы со щелью в 120 px и разной шириной — ровно то, что
# получается, когда аналитик двигает полосы мышью.
GAPPED = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="laneA" value="Фронт-офис" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="0" width="1200" height="300" as="geometry" />
        </mxCell>
        <mxCell id="laneB" value="Бэк-офис" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="420" width="1000" height="260" as="geometry" />
        </mxCell>
        <mxCell id="s" value="Начало" style="ellipse;fillColor=#10b981;" vertex="1" parent="laneA">
          <mxGeometry x="80" y="120" width="50" height="50" as="geometry" />
        </mxCell>
        <mxCell id="t1" value="Проверка 5 мин" style="rounded=1;" vertex="1" parent="laneA">
          <mxGeometry x="220" y="105" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="t2" value="Оформление 3 мин" style="rounded=1;" vertex="1" parent="laneB">
          <mxGeometry x="220" y="100" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="db" value="IABS" style="shape=datastore;" vertex="1" parent="laneA">
          <mxGeometry x="640" y="60" width="80" height="60" as="geometry" />
        </mxCell>
        <mxCell id="f1" edge="1" source="s" target="t1" parent="laneA" />
        <mxCell id="f2" edge="1" source="t1" target="t2" parent="1">
          <mxGeometry relative="1" as="geometry">
            <Array as="points"><mxPoint x="500" y="145" /><mxPoint x="500" y="540" /></Array>
          </mxGeometry>
        </mxCell>
        <mxCell id="a1" style="dashed=1;" edge="1" source="db" target="t1" parent="laneA" />
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


def _on_polyline(point, route, tol=4.0):
    """Проходит ли ломаная через точку — не обязательно своей вершиной."""
    px, py = point
    for (x1, y1), (x2, y2) in zip(route, route[1:]):
        if not (min(x1, x2) - tol <= px <= max(x1, x2) + tol):
            continue
        if not (min(y1, y2) - tol <= py <= max(y1, y2) + tol):
            continue
        if abs(x1 - x2) < 0.5 and abs(px - x1) <= tol:
            return True
        if abs(y1 - y2) < 0.5 and abs(py - y1) <= tol:
            return True
    return False


def _shapes(xml: str):
    root = ET.fromstring(xml.encode('utf-8'))
    out = {}
    for shape in root.iter(f'{{{DI}}}BPMNShape'):
        bounds = shape.find(f'{{{DC}}}Bounds')
        if bounds is not None:
            out[shape.get('bpmnElement')] = tuple(
                float(bounds.get(k)) for k in ('x', 'y', 'width', 'height'))
    return root, out


class LaneStackTest(unittest.TestCase):
    """Дорожки обязаны замощать пул: щель и нахлёст импортёр правит по-своему."""

    def setUp(self):
        self.process = parse_drawio_xml(GAPPED, 'lanes.drawio')

    def test_source_map_really_has_a_gap(self):
        # Без этого тест ничего не проверяет: щель должна быть в исходнике.
        lanes = sorted(self.process.lanes, key=lambda l: l.geometry.y)
        self.assertGreater(
            lanes[1].geometry.y - (lanes[0].geometry.y + lanes[0].geometry.height), 100)

    def test_lanes_become_contiguous(self):
        stacked, _ = build_lane_stack(self.process)
        for above, below in zip(stacked, stacked[1:]):
            self.assertEqual(below.geometry.y, above.geometry.y + above.geometry.height)

    def test_pool_is_exactly_the_union_of_its_lanes(self):
        stacked, _ = build_lane_stack(self.process)
        x, y, w, h = pool_bounds(stacked)
        self.assertEqual(y, stacked[0].geometry.y)
        self.assertEqual(y + h, stacked[-1].geometry.y + stacked[-1].geometry.height)
        self.assertEqual(h, sum(l.geometry.height for l in stacked))

    def test_content_moves_with_its_lane(self):
        # Сдвинув дорожку и оставив шаги на месте, мы получили бы карту хуже
        # исходной: шаги оказались бы в чужих дорожках.
        moved = stack_lanes(self.process)
        lane_of = {l.id: l for l in moved.lanes}
        for node in moved.nodes:
            if not node.laneId:
                continue
            lane = lane_of[node.laneId]
            self.assertGreaterEqual(node.geometry.y, lane.geometry.y - 1)
            self.assertLessEqual(
                node.geometry.y + node.geometry.height,
                lane.geometry.y + lane.geometry.height + 1,
                node.name)

    def test_bends_move_with_their_band(self):
        # Излом, нарисованный в нижней дорожке, обязан уехать вместе с ней:
        # иначе связь пойдёт мимо своей же фигуры.
        before = {e.id: [(p.x, p.y) for p in e.points] for e in self.process.edges}
        moved = stack_lanes(self.process)
        after = {e.id: [(p.x, p.y) for p in e.points] for e in moved.edges}
        bent = [eid for eid, pts in before.items() if len(pts) >= 2]
        self.assertTrue(bent, 'фикстура должна содержать связь с изломами')
        for eid in bent:
            self.assertNotEqual(before[eid], after[eid], 'изломы не переехали')

    def test_lanes_tile_the_pool_in_the_exported_di(self):
        _root, shapes = _shapes(generate_bpmn_xml(self.process))
        root = ET.fromstring(generate_bpmn_xml(self.process).encode('utf-8'))
        lanes = [shapes[l.get('id')] for l in root.iter(f'{{{BPMN}}}lane')
                 if l.get('id') in shapes]
        lanes.sort(key=lambda b: b[1])
        self.assertGreaterEqual(len(lanes), 2)
        for above, below in zip(lanes, lanes[1:]):
            self.assertAlmostEqual(below[1], above[1] + above[3], delta=1)
        pools = [shapes[p.get('id')] for p in root.iter(f'{{{BPMN}}}participant')
                 if p.get('id') in shapes]
        self.assertTrue(pools)
        self.assertAlmostEqual(pools[0][1], lanes[0][1], delta=1)
        self.assertAlmostEqual(pools[0][1] + pools[0][3], lanes[-1][1] + lanes[-1][3], delta=1)

    def test_already_stacked_map_is_returned_untouched(self):
        once = stack_lanes(self.process)
        twice = stack_lanes(once)
        self.assertIs(twice, once, 'повторная укладка не должна копировать модель')


#: Полоса клиента без единого шага сверху, под ней — дорожки банка со щелью.
#: Пунктир от шага уходит вверх и кончается ровно на нижней грани полосы клиента.
CLIENT_BAND = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="client" value="Mijoz" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="40" width="1200" height="80" as="geometry" />
        </mxCell>
        <mxCell id="front" value="Фронт-офис" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="200" width="1200" height="200" as="geometry" />
        </mxCell>
        <mxCell id="archive" value="Архив" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="420" width="1200" height="60" as="geometry" />
        </mxCell>
        <mxCell id="back" value="Бэк-офис" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="500" width="1200" height="200" as="geometry" />
        </mxCell>
        <mxCell id="t1" value="Приём заявки 5 мин" style="rounded=1;" vertex="1" parent="front">
          <mxGeometry x="220" y="60" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="t2" value="Оформление 3 мин" style="rounded=1;" vertex="1" parent="back">
          <mxGeometry x="220" y="60" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="f1" edge="1" source="t1" target="t2" parent="1" />
        <mxCell id="touch" style="dashed=1;dashPattern=8 8;" edge="1" source="t1" parent="1">
          <mxGeometry relative="1" as="geometry">
            <mxPoint x="310" y="120" as="targetPoint" />
          </mxGeometry>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


class ClientBandStackTest(unittest.TestCase):
    """Полоса внешнего участника — не дорожка пула: она не примыкает к нему."""

    def setUp(self):
        self.process = parse_drawio_xml(CLIENT_BAND, 'client.drawio')
        self.lane_of = {l.name: l for l in self.process.lanes}

    def test_gap_next_to_an_empty_band_survives(self):
        # Замощать пул обязаны его дорожки. Полоса без шагов — внешняя сторона,
        # в выгрузке это отдельный пул, и щель, оставленная аналитиком, — часть
        # раскладки. Пока щель убиралась, полосы слипались в сплошной столбец.
        stacked, _ = build_lane_stack(self.process)
        by_name = {l.name: l for l in stacked}
        client, front = by_name['Mijoz'], by_name['Фронт-офис']
        self.assertEqual(client.geometry.y, 40)
        self.assertGreater(front.geometry.y, client.geometry.y + client.geometry.height)

    def test_bank_lanes_still_tile(self):
        # «Архив» пуст, но лежит ВНУТРИ пула — значит это дорожка, а не участник,
        # и щель перед ней убирается: разрыв между дорожками импортёр правит
        # по-своему, и часть карты оказывается вне всяких дорожек.
        stacked, _ = build_lane_stack(self.process)
        inner = [l for l in stacked if l.name != 'Mijoz']
        inner.sort(key=lambda l: l.geometry.y)
        self.assertEqual([l.name for l in inner], ['Фронт-офис', 'Архив', 'Бэк-офис'])
        for above, below in zip(inner, inner[1:]):
            self.assertEqual(below.geometry.y, above.geometry.y + above.geometry.height)

    def test_point_on_a_lane_edge_stays_on_that_edge(self):
        # Точка ровно на нижней грани полосы не попадала ни в одну полосу
        # переноса и уезжала со сдвигом ПОСЛЕДНЕЙ дорожки карты: свободный конец
        # пунктира к клиенту улетал на сотни пикселей выше схемы, и связь
        # выглядела оборванной.
        _stacked, remap = build_lane_stack(self.process)
        client = self.lane_of['Mijoz']
        edge_y = client.geometry.y + client.geometry.height
        self.assertEqual(remap(edge_y), edge_y)

    def test_touchpoint_end_stays_inside_the_map(self):
        moved = stack_lanes(self.process)
        top = min(l.geometry.y for l in moved.lanes)
        touch = next(e for e in moved.edges if e.id == 'touch')
        end = touch.targetPoint or touch.sourcePoint
        self.assertIsNotNone(end, 'фикстура должна нести свободный конец')
        self.assertGreaterEqual(end.y, top, 'конец пунктира ушёл выше всей карты')


#: Три участника В РЯД над пулом — так подписаны карты августовской партии.
#:
#: Каждый нарисован на своей ширине и на одной высоте с остальными, а пунктиры
#: от шагов банка кончаются точками ровно на их нижней грани.
PARTICIPANT_ROW = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <mxCell id="p_client" value="Mijoz" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="-160" width="600" height="120" as="geometry" />
    </mxCell>
    <mxCell id="p_bank" value="Markaziy bank" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="700" y="-160" width="400" height="120" as="geometry" />
    </mxCell>
    <mxCell id="p_org" value="Ijarachi" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="1200" y="-160" width="300" height="120" as="geometry" />
    </mxCell>

    <mxCell id="front" value="Фронт-офис" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="160" width="1800" height="200" as="geometry" />
    </mxCell>
    <mxCell id="back" value="Бэк-офис" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="400" width="1800" height="200" as="geometry" />
    </mxCell>

    <mxCell id="t1" value="Приём заявки 5 мин" style="rounded=1;" vertex="1" parent="front">
      <mxGeometry x="200" y="60" width="180" height="80" as="geometry" />
    </mxCell>
    <mxCell id="t2" value="Проверка 3 мин" style="rounded=1;" vertex="1" parent="front">
      <mxGeometry x="820" y="60" width="180" height="80" as="geometry" />
    </mxCell>
    <mxCell id="t3" value="Оформление 3 мин" style="rounded=1;" vertex="1" parent="back">
      <mxGeometry x="1600" y="60" width="180" height="80" as="geometry" />
    </mxCell>

    <mxCell id="gw" value="Kelishildimi?" style="shape=mxgraph.bpmn.gateway2;html=1;gwType=exclusive;" vertex="1" parent="front">
      <mxGeometry x="1300" y="75" width="50" height="50" as="geometry" />
    </mxCell>

    <mxCell id="f1" edge="1" source="t1" target="t2" parent="1" />
    <mxCell id="f2" edge="1" source="t2" target="gw" parent="1" />
    <mxCell id="f3" value="Ha" edge="1" source="gw" target="t3" parent="1" />

    <mxCell id="m_client" style="dashed=1;dashPattern=8 8;" edge="1" source="t1" parent="1">
      <mxGeometry relative="1" as="geometry">
        <mxPoint x="290" y="-40" as="targetPoint" />
      </mxGeometry>
    </mxCell>
    <mxCell id="m_bank" style="dashed=1;dashPattern=8 8;" edge="1" source="t2" parent="1">
      <mxGeometry relative="1" as="geometry">
        <mxPoint x="910" y="-40" as="targetPoint" />
      </mxGeometry>
    </mxCell>
    <mxCell id="m_org" style="dashed=1;dashPattern=8 8;" edge="1" source="p_org" target="t3" parent="1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>

    <mxCell id="m_to_gw" style="dashed=1;dashPattern=8 8;" edge="1" source="p_org" target="gw" parent="1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="m_from_gw" style="dashed=1;dashPattern=8 8;" edge="1" source="gw" target="p_client" parent="1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""


class ParticipantRowTest(unittest.TestCase):
    """Участники, нарисованные в ряд, остаются в ряду и держат свои связи."""

    def setUp(self):
        self.process = parse_drawio_xml(PARTICIPANT_ROW, 'row.drawio')
        self.drawn = {l.name: (l.geometry.x, l.geometry.y) for l in self.process.lanes}

    def test_participants_keep_the_place_the_analyst_drew(self):
        # Пока полосы участников шли в общей стопке, три рамки с одной высоты
        # раскладывало столбиком, а пул уезжал вниз на их суммарную высоту.
        moved = stack_lanes(self.process)
        for lane in moved.lanes:
            if lane.name in ('Mijoz', 'Markaziy bank', 'Ijarachi'):
                self.assertEqual(
                    (lane.geometry.x, lane.geometry.y), self.drawn[lane.name],
                    f'полоса «{lane.name}» съехала с нарисованного места',
                )

    def test_pool_does_not_drift_down(self):
        moved = stack_lanes(self.process)
        front = next(l for l in moved.lanes if l.name == 'Фронт-офис')
        self.assertEqual(front.geometry.y, 160)

    def test_touchpoints_stay_on_their_own_participant(self):
        moved = stack_lanes(self.process)
        lane_of = {l.id: l for l in moved.lanes}
        checked = 0
        for edge in moved.edges:
            if edge.kind != 'messageFlow':
                continue
            lane = lane_of.get(edge.sourceId or '') or lane_of.get(edge.targetId or '')
            end = edge.targetPoint if edge.sourceId not in lane_of else edge.sourcePoint
            if lane is None or end is None:
                continue
            checked += 1
            self.assertGreaterEqual(end.y, lane.geometry.y - 1)
            self.assertLessEqual(end.y, lane.geometry.y + lane.geometry.height + 1)
        self.assertTrue(checked, 'фикстура должна нести свободные концы пунктиров')

    def test_exported_message_flows_touch_their_participant(self):
        root = ET.fromstring(generate_bpmn_xml(self.process))
        bounds = {}
        for shape in root.iter(f'{{{DI}}}BPMNShape'):
            box = shape.find(f'{{{DC}}}Bounds')
            if box is not None:
                bounds[shape.get('bpmnElement')] = (
                    float(box.get('x')), float(box.get('y')),
                    float(box.get('width')), float(box.get('height')),
                )
        flows = {
            flow.get('id'): (flow.get('sourceRef'), flow.get('targetRef'))
            for flow in root.iter(f'{{{BPMN}}}messageFlow')
        }
        self.assertTrue(flows, 'выгрузка обязана нести обмен с участниками')
        participants = {p.get('id') for p in root.iter(f'{{{BPMN}}}participant')}
        checked = 0
        for edge in root.iter(f'{{{DI}}}BPMNEdge'):
            ends = flows.get(edge.get('bpmnElement'))
            if not ends:
                continue
            points = [(float(p.get('x')), float(p.get('y')))
                      for p in edge.findall('{http://www.omg.org/spec/DD/20100524/DI}waypoint')]
            for ref, point in ((ends[0], points[0]), (ends[1], points[-1])):
                if ref not in participants or ref not in bounds:
                    continue
                x, y, w, h = bounds[ref]
                checked += 1
                self.assertTrue(
                    x - 1 <= point[0] <= x + w + 1 and y - 1 <= point[1] <= y + h + 1,
                    f'связь начинается в пустоте: {point} мимо рамки участника {(x, y, w, h)}',
                )
        self.assertTrue(checked, 'ни одна связь не проверена')


class RouteReversalTest(unittest.TestCase):
    """Ломаная связи не должна возвращаться по себе.

    Трассировка складывает маршрут из уса, обхода и причала, и на общей прямой
    их концы шли вразнобой: линия уходила вправо на двадцать пикселей,
    возвращалась на шестнадцать и снова шла вправо. Нового отрезка это не
    рисует, но у пунктира сбивается фаза — на коротких связях «листочек ↔ шаг»
    вместо ровной цепочки точек выходила рваная полоса. По 22 картам банка так
    были испорчены 1358 связей из 5239.
    """

    def test_a_run_along_one_line_keeps_only_its_ends(self):
        from app.services.edge_routing import _collapse_collinear_runs

        stutter = [(910.0, 1256.0), (930.0, 1256.0), (914.0, 1256.0), (934.0, 1256.0)]
        self.assertEqual(_collapse_collinear_runs(stutter), [(910.0, 1256.0), (934.0, 1256.0)])

    def test_a_real_detour_is_left_alone(self):
        from app.services.edge_routing import _collapse_collinear_runs

        detour = [(0.0, 0.0), (0.0, -40.0), (60.0, -40.0), (60.0, 0.0)]
        self.assertEqual(_collapse_collinear_runs(detour), detour)

    def test_a_sheet_beside_its_step_gets_a_straight_dash(self):
        # Геометрия из «AVGUST-1.29»: лист над шагом, выход снизу листа, вход
        # слева шага, один излом ниже обоих концов. Маршрут уходил вниз к
        # излому и возвращался наверх ко входу — по пунктиру это видно как
        # рябь вместо ровной цепочки точек.
        sheet = ProcessNode(id='doc', name='Документ', type='dataObject',
                            geometry=Geometry(x=359, y=-427, width=25, height=33))
        step = ProcessNode(id='task', name='1. Bankning barcha', type='userTask',
                           geometry=Geometry(x=400, y=-422, width=139, height=126))
        link = ProcessEdge(id='a', kind='association', sourceId='doc', targetId='task',
                           exitX=0.5, exitY=1.0, entryX=0.0, entryY=0.25, dashed=True,
                           points=[ProcessEdgePoint(x=370, y=-370)])
        route = orthogonal_waypoints(link, sheet, step)
        for a, b, c in zip(route, route[1:], route[2:]):
            first = (b[0] - a[0], b[1] - a[1])
            second = (c[0] - b[0], c[1] - b[1])
            if first == (0, 0) or second == (0, 0):
                continue
            straight = abs(first[0] * second[1] - first[1] * second[0]) < 0.5
            backwards = first[0] * second[0] + first[1] * second[1] < 0
            self.assertFalse(straight and backwards, f'связь ходит туда-обратно: {route}')


class OffFlowReportTest(unittest.TestCase):
    """Замечание обязано говорить правду о том, что видно на карте.

    У шлюза «Kelishildimi?» в фикстуре есть пунктиры к двум участникам, но нет
    ни одного перехода. Отчёт называл такую фигуру «не соединена ни с чем» и
    советовал убрать её с карты — сотрудник видел красную строку рядом с
    ромбом, к которому подходят две линии. На 22 картах банка таких фигур 11.
    """

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(PARTICIPANT_ROW, 'row.drawio')
        cls.by_code = {}
        for issue in cls.process.validation:
            cls.by_code.setdefault(issue.code, []).append(issue)

    def test_a_shape_with_contacts_is_not_called_unconnected(self):
        for issue in self.by_code.get('isolated_node', []):
            self.assertNotIn('gw', issue.nodeIds or [], issue.message)

    def test_it_is_reported_as_standing_outside_the_flow(self):
        # Шлюз в фикстуре включён в поток, поэтому замечания быть не должно;
        # проверяем сам разбор случая на модели, собранной вручную.
        from app.models.process import Geometry, ProcessEdge, ProcessNode
        from app.services.diagnostics import collect_import_diagnostics

        gateway = ProcessNode(id='gw', name='Kelishildimi?', type='exclusiveGateway',
                              geometry=Geometry(x=0, y=0, width=50, height=50))
        lane = ProcessNode(id='client', name='Mijoz', type='lane',
                           geometry=Geometry(x=0, y=0, width=400, height=80))
        contact = ProcessEdge(id='m1', kind='messageFlow', sourceId='client', targetId='gw')
        issues = collect_import_diagnostics([gateway], [lane], [contact])
        codes = {i.code for i in issues}
        self.assertIn('off_flow_node', codes)
        self.assertNotIn('isolated_node', codes)
        text = next(i for i in issues if i.code == 'off_flow_node').message
        self.assertIn('обмен с внешним участником', text)
        self.assertNotIn('не соединена ни с чем', text)

    def test_a_truly_forgotten_shape_is_still_called_unconnected(self):
        from app.models.process import Geometry, ProcessNode
        from app.services.diagnostics import collect_import_diagnostics

        lost = ProcessNode(id='lost', name='Забытый шаг', type='userTask',
                           geometry=Geometry(x=0, y=0, width=120, height=60))
        codes = {i.code for i in collect_import_diagnostics([lost], [], [])}
        self.assertIn('isolated_node', codes)
        self.assertNotIn('off_flow_node', codes)


class GatewayContactTest(unittest.TestCase):
    """Пунктир от полосы участника к шлюзу — связь, а не оформление.

    Аналитик ведёт линию от полосы клиента прямо к ромбу: ответ участника и
    есть развилка. Разбор сверялся со списком InteractionNode из спецификации
    BPMN, где шлюзов нет, и такая линия становилась оформительской — из обеих
    выгрузок она пропадала молча. На 22 картах банка так терялись 84 пунктира.
    """

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(PARTICIPANT_ROW, 'row.drawio')
        cls.by_id = {e.id: e for e in cls.process.edges}
        cls.xml = generate_bpmn_xml(cls.process)
        cls.root = ET.fromstring(cls.xml)

    def test_dash_to_a_gateway_is_a_contact_not_decoration(self):
        for edge_id in ('m_to_gw', 'm_from_gw'):
            self.assertIn(edge_id, self.by_id, f'пунктир {edge_id} потерян при разборе')
            self.assertEqual(self.by_id[edge_id].kind, 'messageFlow', edge_id)

    def test_no_link_disappears_between_the_model_and_the_file(self):
        exportable = [e for e in self.process.edges if e.kind != 'annotationLine']
        drawn = self.xml.count('<bpmndi:BPMNEdge ')
        self.assertEqual(
            drawn, len(exportable),
            f'связей в модели {len(exportable)}, линий в .bpmn {drawn} — часть пропала молча')

    def test_gateway_contact_leaves_the_file_as_an_association(self):
        # Концом messageFlow шлюз спецификация не признаёт, поэтому связь идёт
        # ассоциацией — тем же приёмом, что и пунктир к хранилищу.
        message_refs = {
            (f.get('sourceRef'), f.get('targetRef'))
            for f in self.root.iter(f'{{{BPMN}}}messageFlow')
        }
        assoc_refs = {
            (a.get('sourceRef'), a.get('targetRef'))
            for a in self.root.iter(f'{{{BPMN}}}association')
        }
        participants = {p.get('id') for p in self.root.iter(f'{{{BPMN}}}participant')}
        touching = {pair for pair in assoc_refs if participants & set(pair)}
        self.assertEqual(len(touching), 2, f'ассоциации к участникам: {touching}')
        self.assertFalse(
            {pair for pair in message_refs if participants & set(pair)} & touching,
            'связь со шлюзом не должна остаться messageFlow',
        )

    def test_the_contact_still_touches_the_participant_frame(self):
        bounds = {}
        for shape in self.root.iter(f'{{{DI}}}BPMNShape'):
            box = shape.find(f'{{{DC}}}Bounds')
            if box is not None:
                bounds[shape.get('bpmnElement')] = (
                    float(box.get('x')), float(box.get('y')),
                    float(box.get('width')), float(box.get('height')),
                )
        refs = {}
        for tag in ('messageFlow', 'association'):
            for el in self.root.iter(f'{{{BPMN}}}{tag}'):
                refs[el.get('id')] = (el.get('sourceRef'), el.get('targetRef'))
        participants = {p.get('id') for p in self.root.iter(f'{{{BPMN}}}participant')}
        checked = 0
        for edge in self.root.iter(f'{{{DI}}}BPMNEdge'):
            ends = refs.get(edge.get('bpmnElement'))
            if not ends:
                continue
            points = [(float(p.get('x')), float(p.get('y')))
                      for p in edge.findall('{http://www.omg.org/spec/DD/20100524/DI}waypoint')]
            for ref, point in ((ends[0], points[0]), (ends[1], points[-1])):
                if ref not in participants or ref not in bounds:
                    continue
                x, y, w, h = bounds[ref]
                checked += 1
                self.assertTrue(
                    x - 1 <= point[0] <= x + w + 1 and y - 1 <= point[1] <= y + h + 1,
                    f'связь начинается в пустоте: {point} мимо рамки участника {(x, y, w, h)}',
                )
        self.assertGreaterEqual(checked, 2)


class VanishedLinkTest(unittest.TestCase):
    """Связь, обещанная разбором, обязана дойти до выгрузки.

    Пустая дорожка ВНУТРИ пула («Архив») внешним участником быть не может:
    вынести её из пула значило бы пул разорвать, и ``split_external_lanes``
    оставляет её дорожкой. Разбор же считал внешней любую полосу без шагов и
    помечал пунктир к ней ``messageFlow``. Выгрузка участника под такую связь
    не находила и выбрасывала её молча: на холсте линия есть, в .bpmn её нет,
    и сотрудник видит это только глазами в чужом просмотрщике.

    Сторону теперь выбирает одна и та же функция. Пунктир от шага к ЧУЖОЙ
    дорожке — контакт с ней, внешняя она или нет: в .bpmn он идёт ассоциацией
    к дорожке, в .pmm — к точке на её границе. Молча пропасть он не может.
    """

    MAP = CLIENT_BAND.replace(
        '      </root>',
        '        <mxCell id="to_archive" style="dashed=1;dashPattern=8 8;" edge="1" source="t1" parent="1">\n'
        '          <mxGeometry relative="1" as="geometry">\n'
        '            <mxPoint x="310" y="450" as="targetPoint" />\n'
        '          </mxGeometry>\n'
        '        </mxCell>\n'
        '      </root>')

    def setUp(self):
        self.process = parse_drawio_xml(self.MAP, 'archive.drawio')
        self.xml = generate_bpmn_xml(self.process)

    def test_the_fixture_carries_a_dash_into_the_inner_empty_lane(self):
        """Без этой линии проверять было бы нечего."""
        edge = next((e for e in self.process.edges if e.id == 'to_archive'), None)
        self.assertIsNotNone(edge, 'пунктир к «Архиву» не разобрался')
        self.assertTrue(edge.dashed)

    def test_a_dash_into_an_inner_lane_is_a_contact_not_a_message_flow(self):
        edge = next(e for e in self.process.edges if e.id == 'to_archive')
        self.assertEqual((edge.kind, edge.targetId), ('messageFlow', 'archive'))
        self.assertIn('<bpmn:association id="to_archive" sourceRef="t1" targetRef="archive"', self.xml)
        self.assertNotIn('<bpmn:messageFlow id="to_archive"', self.xml,
                         'дорожка внутри пула участником не станет — messageFlow к ней нельзя')

    def test_no_link_disappears_between_the_model_and_the_file(self):
        exportable = [e for e in self.process.edges if e.kind != 'annotationLine']
        drawn = self.xml.count('<bpmndi:BPMNEdge ')
        self.assertEqual(
            drawn, len(exportable),
            f'связей в модели {len(exportable)}, линий в .bpmn {drawn} — часть пропала молча')


class DataAssociationTest(unittest.TestCase):
    """Связь шага с хранилищем обязана доехать до PIX."""

    def setUp(self):
        self.process = parse_drawio_xml(GAPPED, 'lanes.drawio')
        self.xml = generate_bpmn_xml(self.process)

    def test_link_to_the_store_is_a_data_association(self):
        """Обычную ассоциацию к базе импорт PIX выбрасывает молча.

        Проверено на PIX банка (PIX Процессы 2.3.2): из ``bpmn:association``
        «база → шаг» на карту не попало ничего, а ``dataInputAssociation`` и
        ``dataOutputAssociation`` пришли пунктирными связями. Наконечник у них
        есть, и выключить его из файла нельзя, — но без них связи нет вовсе.
        """
        self.assertIn('<bpmn:dataInputAssociation', self.xml)
        self.assertNotIn('associationDirection="None"', self.xml)

    def test_round_trip_keeps_the_link(self):
        # Собственную выгрузку платформа обязана прочитать обратно: иначе при
        # повторном импорте со всех шагов отваливаются системы и документы.
        reopened = parse_bpmn_xml(self.xml, 'lanes.bpmn')
        kinds = [e.kind for e in reopened.edges]
        self.assertIn('association', kinds)
        stores = {n.id for n in reopened.nodes if n.type == 'dataStore'}
        self.assertTrue(stores)
        linked = {e.sourceId for e in reopened.edges if e.kind == 'association'}
        linked |= {e.targetId for e in reopened.edges if e.kind == 'association'}
        self.assertTrue(stores & linked, 'хранилище потеряло связь при обратном чтении')


class ObstacleRoutingTest(unittest.TestCase):
    """Линия обходит чужую фигуру, а не проходит сквозь неё."""

    def _pair(self):
        left = ProcessNode(id='a', name='A', type='userTask',
                           geometry=Geometry(x=0, y=0, width=100, height=60))
        right = ProcessNode(id='b', name='B', type='userTask',
                            geometry=Geometry(x=400, y=0, width=100, height=60))
        return left, right

    def test_direct_route_is_kept_when_nothing_blocks(self):
        left, right = self._pair()
        edge = ProcessEdge(id='e', sourceId='a', targetId='b')
        route = orthogonal_waypoints(edge, left, right, None, build_obstacles([left, right]))
        self.assertEqual(len(route), 2, f'прямой путь свободен, а получили {route}')

    def test_route_goes_around_a_shape_in_the_way(self):
        left, right = self._pair()
        wall = ProcessNode(id='w', name='W', type='userTask',
                           geometry=Geometry(x=200, y=-40, width=100, height=140))
        edge = ProcessEdge(id='e', sourceId='a', targetId='b')
        obstacles = build_obstacles([left, right, wall])
        route = orthogonal_waypoints(edge, left, right, None, obstacles)
        self.assertEqual(obstacles.hits(route, ('a', 'b')), 0,
                         f'линия всё ещё идёт сквозь фигуру: {route}')

    def test_analyst_bends_are_translated_as_drawn(self):
        """Изломы аналитика — его решение, как обвести схему, и мы их не трогаем.

        Проверяем не «точка есть среди waypoint», а «линия через неё проходит»:
        излом, попавший в середину прямого участка, вершиной быть не обязан.
        """
        left = ProcessNode(id='a', name='A', type='userTask',
                           geometry=Geometry(x=0, y=0, width=100, height=60))
        right = ProcessNode(id='b', name='B', type='userTask',
                            geometry=Geometry(x=400, y=300, width=100, height=60))
        edge = ProcessEdge(id='e', sourceId='a', targetId='b',
                           points=[{'x': 250, 'y': 30}, {'x': 250, 'y': 330}])
        route = orthogonal_waypoints(edge, left, right)
        for bend in ((250, 30), (250, 330)):
            self.assertTrue(_on_polyline(bend, route),
                            f'излом {bend} потерялся: {route}')

    def test_a_turn_back_bend_is_not_simplified_away(self):
        # Разворот (сосед по одну сторону и по другую совпадают) внутренней
        # точкой прямого отрезка не является: это вершина обхода.
        left, right = self._pair()
        edge = ProcessEdge(id='e', sourceId='a', targetId='b',
                           points=[{'x': 250, 'y': -140}])
        route = orthogonal_waypoints(edge, left, right)
        self.assertTrue(any(y <= -130 for _x, y in route),
                        f'вершина обхода стёрта: {route}')

    def test_a_shape_owns_its_badge(self):
        # Значок длительности принадлежит шагу: связь, идущая в этот шаг,
        # проходит рядом со значком по праву и пересечением не считается.
        obstacles = Obstacles(
            [('duration:a', (0.0, 60.0, 48.0, 48.0))], {'duration:a': 'a'})
        route = [(24.0, 0.0), (24.0, 120.0)]
        self.assertEqual(obstacles.hits(route, ('a',)), 0)
        self.assertEqual(obstacles.hits(route, ('zzz',)), 1)


class PierceToleranceTest(unittest.TestCase):
    """Насколько глубоко линии позволено зайти в фигуру."""

    def test_a_line_just_inside_a_shape_counts_as_a_pierce(self):
        """Допуск — на округление, а не на «немного внутри».

        Проверка сжимала рамку на весь запас обхода (10 px) и потому не
        замечала линию, врезавшуюся в фигуру на девять пикселей: у значка
        времени в 48 px «неприкосновенной» оставалась середина 28×28.
        Трассировщик считал такие маршруты чистыми и обход не искал.
        """
        box = (0.0, 0.0, 48.0, 48.0)
        # Линия в пяти пикселях от края — уже внутри фигуры.
        self.assertTrue(er._segment_in_box((-20.0, 5.0), (80.0, 5.0), box))
        # Ровно по грани — ещё нет.
        self.assertFalse(er._segment_in_box((-20.0, 0.0), (80.0, 0.0), box))

    def test_clearance_is_wider_than_tolerance(self):
        # Обходим с запасом, а засчитываем пересечение по факту.
        self.assertGreater(er._CLEARANCE, er._PIERCE_TOLERANCE)


class AstarRoutingTest(unittest.TestCase):
    """Поиск пути там, где заготовок не хватает."""

    def _wall_of_three(self):
        left = ProcessNode(id='a', name='A', type='userTask',
                           geometry=Geometry(x=0, y=0, width=100, height=60))
        right = ProcessNode(id='b', name='B', type='userTask',
                            geometry=Geometry(x=600, y=0, width=100, height=60))
        # Сплошная стена из трёх фигур между ними: ни одно колено не пройдёт.
        wall = [
            ProcessNode(id=f'w{i}', name='W', type='userTask',
                        geometry=Geometry(x=200 + i * 110, y=-160, width=100, height=380))
            for i in range(3)
        ]
        return left, right, wall

    def test_route_goes_around_a_wall(self):
        left, right, wall = self._wall_of_three()
        obstacles = build_obstacles([left, right, *wall])
        edge = ProcessEdge(id='e', sourceId='a', targetId='b')
        route = orthogonal_waypoints(edge, left, right, None, obstacles)
        self.assertEqual(obstacles.hits(route, ('a', 'b')), 0,
                         f'линия прошла сквозь стену: {route}')
        self.assertGreater(len(route), 2, 'обход не может быть прямой линией')

    def test_route_stays_orthogonal(self):
        left, right, wall = self._wall_of_three()
        edge = ProcessEdge(id='e', sourceId='a', targetId='b')
        route = orthogonal_waypoints(edge, left, right, None,
                                     build_obstacles([left, right, *wall]))
        for (x1, y1), (x2, y2) in zip(route, route[1:]):
            self.assertTrue(abs(x2 - x1) < 0.5 or abs(y2 - y1) < 0.5,
                            f'косой отрезок в обходе: {route}')

    def test_analyst_bends_give_way_to_a_pierced_shape(self):
        """Изломы аналитика повторяем, пока линия никого не задевает.

        Линия поперёк чужого шага читается как ошибка независимо от того, кто
        её так нарисовал, поэтому маршрут перестраивается.
        """
        left, right, wall = self._wall_of_three()
        obstacles = build_obstacles([left, right, *wall])
        edge = ProcessEdge(id='e', sourceId='a', targetId='b',
                           points=[{'x': 350, 'y': 30}])
        route = orthogonal_waypoints(edge, left, right, None, obstacles)
        self.assertEqual(obstacles.hits(route, ('a', 'b')), 0,
                         f'излом протащил линию сквозь фигуру: {route}')


class StubDirectionTest(unittest.TestCase):
    """Ус не должен уходить в сторону, противоположную цели."""

    def test_anchor_from_the_style_does_not_turn_the_line_back(self):
        # В стиле draw.io написано «выходить вправо», а цель слева: без
        # проверки линия делала ус вправо, разворачивалась и шла назад по себе
        # же — прямо через фигуру, из которой только что вышла.
        source = ProcessNode(id='a', name='A', type='userTask',
                             geometry=Geometry(x=400, y=0, width=100, height=60))
        target = ProcessNode(id='b', name='B', type='userTask',
                             geometry=Geometry(x=0, y=0, width=100, height=60))
        edge = ProcessEdge(id='e', sourceId='a', targetId='b', exitX=1.0, exitY=0.5)
        route = orthogonal_waypoints(edge, source, target)
        self.assertLessEqual(route[1][0], route[0][0] + 0.5,
                             f'ус ушёл от цели: {route}')

    def test_a_genuine_side_anchor_is_respected(self):
        # Цель сбоку, а не позади: якорь аналитика остаётся в силе.
        source = ProcessNode(id='a', name='A', type='userTask',
                             geometry=Geometry(x=0, y=0, width=100, height=60))
        target = ProcessNode(id='b', name='B', type='userTask',
                             geometry=Geometry(x=400, y=0, width=100, height=60))
        edge = ProcessEdge(id='e', sourceId='a', targetId='b', exitX=1.0, exitY=0.5)
        route = orthogonal_waypoints(edge, source, target)
        self.assertGreater(route[1][0], route[0][0], f'ус развернулся зря: {route}')


class CorridorTest(unittest.TestCase):
    """Две связи не должны лечь одна поверх другой."""

    def test_overlap_is_measured_by_length(self):
        corridors = Corridors()
        corridors.occupy([(0.0, 100.0), (200.0, 100.0)])
        # Полное совпадение.
        self.assertAlmostEqual(corridors.overlap([(50.0, 100.0), (150.0, 100.0)]), 100.0)
        # Соседняя линия — уже не совпадение.
        self.assertEqual(corridors.overlap([(50.0, 116.0), (150.0, 116.0)]), 0.0)
        # Пересечение под прямым углом наложением не является.
        self.assertEqual(corridors.overlap([(100.0, 0.0), (100.0, 200.0)]), 0.0)

    def test_second_line_prefers_a_free_corridor(self):
        # Один и тот же пролёт, две связи: вторая обязана уйти в сторону.
        top = ProcessNode(id='a', name='A', type='userTask',
                          geometry=Geometry(x=0, y=0, width=100, height=200))
        bottom = ProcessNode(id='b', name='B', type='userTask',
                             geometry=Geometry(x=400, y=0, width=100, height=200))
        obstacles = build_obstacles([top, bottom])
        corridors = Corridors()
        first = orthogonal_waypoints(
            ProcessEdge(id='e1', sourceId='a', targetId='b'), top, bottom, None,
            obstacles, corridors)
        corridors.occupy(first)
        second = orthogonal_waypoints(
            ProcessEdge(id='e2', sourceId='a', targetId='b',
                        points=[{'x': 250, 'y': 260}]),
            top, bottom, None, obstacles, corridors)
        self.assertLess(corridors.overlap(second), sum(
            abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(second, second[1:])),
            'вторая связь целиком легла на первую')



class DockSpreadTest(unittest.TestCase):
    """Причалы, сошедшиеся в одной точке грани, разводятся вдоль неё.

    Середина грани — место по умолчанию для обоих концов, поэтому входящая и
    исходящая связи шага садятся на неё вместе и дальше идут одним коридором:
    в студии вместо двух связей видна одна.
    """

    def _column(self):
        """Шлюз сверху, шаг под ним, ещё один шаг ниже — как на карте банка."""
        gate = ProcessNode(id='g', name='Условие', type='exclusiveGateway',
                           geometry=Geometry(x=0, y=0, width=60, height=60))
        step = ProcessNode(id='s', name='Шаг', type='userTask',
                           geometry=Geometry(x=200, y=600, width=150, height=100))
        far = ProcessNode(id='f', name='Далее', type='userTask',
                          geometry=Geometry(x=600, y=0, width=220, height=100))
        return gate, step, far

    def _links(self):
        gate, step, far = self._column()
        # Оба конца просятся на верхнюю грань шага: вход задан аналитиком,
        # выход платформа ставит по преобладающей оси — туда же.
        into = ProcessEdge(id='in', sourceId='g', targetId='s', entryX=0.5, entryY=0.0)
        out = ProcessEdge(id='out', sourceId='s', targetId='f', entryX=0.5, entryY=1.0)
        return [(into, gate, step), (out, step, far)], into, out, gate, step, far

    def test_two_edges_do_not_share_one_dock(self):
        links, into, out, gate, step, far = self._links()
        plain_in = orthogonal_waypoints(into, gate, step)
        plain_out = orthogonal_waypoints(out, step, far)
        self.assertEqual(plain_in[-1], plain_out[0],
                         'заготовка теста должна ловить именно слипшийся причал')

        anchors = er.spread_dock_anchors(links)
        spread_in = orthogonal_waypoints(into, gate, step, None, None, None, anchors.get('in'))
        spread_out = orthogonal_waypoints(out, step, far, None, None, None, anchors.get('out'))
        gap = abs(spread_in[-1][0] - spread_out[0][0])
        self.assertGreaterEqual(gap, er.ANCHOR_MIN_GAP,
                                f'причалы всё ещё рядом: {spread_in[-1]} и {spread_out[0]}')

    def test_the_dock_stays_on_its_own_face(self):
        links, into, out, _gate, step, far = self._links()
        anchors = er.spread_dock_anchors(links)
        route = orthogonal_waypoints(out, step, far, None, None, None, anchors.get('out'))
        x, y = route[0]
        self.assertEqual(y, step.geometry.y, f'причал съехал с верхней грани: {route[0]}')
        self.assertTrue(step.geometry.x <= x <= step.geometry.x + step.geometry.width,
                        f'причал уехал за пределы грани: {route[0]}')

    def test_the_anchor_drawn_by_the_analyst_keeps_its_place(self):
        """Двигаем тот причал, что поставила платформа, а не тот, что задан в стиле."""
        links, into, _out, gate, step, _far = self._links()
        anchors = er.spread_dock_anchors(links)
        route = orthogonal_waypoints(into, gate, step, None, None, None, anchors.get('in'))
        self.assertEqual(route[-1], (step.geometry.x + step.geometry.width / 2,
                                     step.geometry.y),
                         f'сдвинули причал, заданный аналитиком: {route[-1]}')

    def test_a_lonely_edge_is_left_alone(self):
        gate, step, _far = self._column()
        edge = ProcessEdge(id='in', sourceId='g', targetId='s', entryX=0.5, entryY=0.0)
        self.assertEqual(er.spread_dock_anchors([(edge, gate, step)]), {},
                         'единственную связь разводить не с кем')


class DurationBadgeTest(unittest.TestCase):
    """Часы шага объезжают соседа, к которому шаг стоит вплотную."""

    def _steps(self, gap: int):
        """Два шага друг под другом: нижний сдвинут вправо, как на карте банка."""
        upper = ProcessNode(id='u', name='Шаг 1', type='userTask', slaMinutes=20,
                            slaMeasured=True,
                            geometry=Geometry(x=0, y=0, width=200, height=80))
        lower = ProcessNode(id='l', name='Шаг 2', type='userTask',
                            geometry=Geometry(x=150, y=80 + gap, width=200, height=80))
        return upper, lower

    def _badge(self, upper, neighbours):
        marker = duration_markers([upper], {'u': 'U'}, {}, set(), neighbours)[0]
        return marker_box(marker)

    def test_badge_keeps_the_usual_corner_when_nothing_is_near(self):
        upper, _lower = self._steps(400)
        box = self._badge(upper, [upper])
        self.assertEqual(box[0] + EVENT_SIDE // 2, upper.geometry.width - 20,
                         f'часы уехали с правого нижнего угла: {box}')

    def test_badge_moves_off_a_neighbour_standing_close(self):
        upper, lower = self._steps(4)
        box = self._badge(upper, [upper, lower])
        near = lower.geometry
        overlaps = not (box[0] + box[2] <= near.x or near.x + near.width <= box[0]
                        or box[1] + box[3] <= near.y or near.y + near.height <= box[1])
        self.assertFalse(overlaps, f'часы легли на соседний шаг: {box}')

    def test_badge_stays_on_the_border_of_its_own_step(self):
        upper, lower = self._steps(4)
        box = self._badge(upper, [upper, lower])
        cx, cy = box[0] + EVENT_SIDE // 2, box[1] + EVENT_SIDE // 2
        geo = upper.geometry
        on_side = (
            cx in (geo.x, geo.x + geo.width) and geo.y <= cy <= geo.y + geo.height
            or cy in (geo.y, geo.y + geo.height) and geo.x <= cx <= geo.x + geo.width
        )
        self.assertTrue(on_side, f'часы оторвались от своего шага: {box}')


class OwnShapeTest(unittest.TestCase):
    """Связь не имеет права идти сквозь собственные шаг или событие.

    Концы связи исключены из проверки препятствий — причал стоит на грани, и
    без исключения любая линия считалась бы пробившей свои же фигуры. Но
    исключение было сплошным, и трассировщик спокойно вёл линию НАСКВОЗЬ
    собственный шаг: на двенадцати картах банка так шли 68 связей из 1787.
    """

    def _wide_pair(self):
        # Цель стоит по диагонали, поэтому колено соблазнительно провести
        # через середину источника.
        left = ProcessNode(id='a', name='A', type='userTask',
                           geometry=Geometry(x=0, y=0, width=300, height=80))
        right = ProcessNode(id='b', name='B', type='userTask',
                            geometry=Geometry(x=120, y=400, width=200, height=80))
        return left, right

    def test_route_does_not_cross_its_own_source(self):
        src, tgt = self._wide_pair()
        # Аналитик вывел связь из ЛЕВОЙ грани, хотя цель ниже и правее.
        edge = ProcessEdge(id='e', sourceId='a', targetId='b', exitX=0.0, exitY=0.5)
        obstacles = build_obstacles([src, tgt])
        route = orthogonal_waypoints(edge, src, tgt, None, obstacles)
        inner = (src.geometry.x + 8, src.geometry.y + 8,
                 src.geometry.width - 16, src.geometry.height - 16)
        for a, b in zip(route, route[1:]):
            self.assertFalse(er._segment_in_box(a, b, inner),
                             f'линия прошла сквозь собственный шаг: {route}')

    def test_a_shape_still_lets_the_line_leave_its_edge(self):
        """Полоса вдоль грани связи по-прежнему доступна: причал на ней и стоит."""
        src, tgt = self._wide_pair()
        edge = ProcessEdge(id='e', sourceId='a', targetId='b')
        route = orthogonal_waypoints(edge, src, tgt, None, build_obstacles([src, tgt]))
        self.assertGreaterEqual(len(route), 2, 'маршрут не построился вовсе')


class SwallowedBendTest(unittest.TestCase):
    """Излом, оказавшийся внутри выросшей фигуры, выносится наружу.

    Изломы аналитик расставлял по исходной геометрии draw.io, а канонизация
    раздвигает шаг под его подпись: излом, лежавший в двадцати пикселях правее
    шага, после расширения оказывается внутри него, и линия возвращается
    сквозь собственную фигуру.
    """

    def test_bend_inside_the_source_is_pushed_past_its_face(self):
        src = ProcessNode(id='a', name='A', type='userTask',
                          geometry=Geometry(x=0, y=0, width=260, height=80))
        tgt = ProcessNode(id='b', name='B', type='userTask',
                          geometry=Geometry(x=500, y=300, width=200, height=80))
        # Излом на x=210 лежал бы правее исходной фигуры шириной 160 и попал
        # внутрь после расширения до 260.
        edge = ProcessEdge(id='e', sourceId='a', targetId='b', exitX=1.0, exitY=0.5,
                           points=[{'x': 210, 'y': 40}])
        route = orthogonal_waypoints(edge, src, tgt)
        for x, _y in route:
            self.assertGreaterEqual(
                x, src.geometry.x + src.geometry.width - er._EPS,
                f'ломаная вернулась внутрь своей фигуры: {route}')

    def test_a_bend_outside_the_shape_is_left_alone(self):
        src = ProcessNode(id='a', name='A', type='userTask',
                          geometry=Geometry(x=0, y=0, width=100, height=60))
        tgt = ProcessNode(id='b', name='B', type='userTask',
                          geometry=Geometry(x=400, y=300, width=100, height=60))
        edge = ProcessEdge(id='e', sourceId='a', targetId='b',
                           points=[{'x': 250, 'y': 30}, {'x': 250, 'y': 330}])
        route = orthogonal_waypoints(edge, src, tgt)
        for bend in ((250, 30), (250, 330)):
            self.assertTrue(_on_polyline(bend, route),
                            f'излом аналитика подвинут без нужды: {route}')


class BandLabelTest(unittest.TestCase):
    """Название дорожки не вылезает из заголовочной полосы.

    Импортёр печатает его повёрнутым в полосе шириной 30 px, а длиной строки
    служит высота дорожки. Число 30 зашито в отрисовщике, поэтому длинное
    название приходится укорачивать — иначе оно расползается за пределы полосы
    поверх соседних фигур.
    """

    LONG = ("Korporativ mijoz uchun import akkreditivlar ochish va uning "
            "operatsiyalari bilan ishlash biznes-jarayoni")

    def test_short_name_is_left_as_is(self):
        self.assertEqual(band_label('Korporativ markaz direktori', 160),
                         'Korporativ markaz direktori')

    def test_long_name_is_trimmed_to_the_band(self):
        shown = band_label(self.LONG, 80)
        self.assertTrue(shown.endswith('…'), shown)
        self.assertLessEqual(text_width(shown), (80 - 8) * 2,
                             f'подпись всё ещё не влезает в полосу: {shown!r}')
        self.assertTrue(self.LONG.startswith(shown[:-1].rstrip()), shown)

    def test_a_tall_lane_keeps_the_whole_name(self):
        """Чем выше дорожка, тем длиннее строка: обрезать незачем."""
        self.assertEqual(band_label(self.LONG, 900), self.LONG)

    def test_trim_falls_on_a_word_boundary(self):
        shown = band_label(self.LONG, 80)
        self.assertNotIn('  ', shown)
        self.assertFalse(shown[:-1].endswith(' '), shown)

    def test_full_name_stays_in_documentation(self):
        process = parse_drawio_xml(GAPPED, 'lanes.drawio')
        long_name = self.LONG
        process.lanes[0].name = long_name
        process.lanes[0].geometry.height = 80
        xml = generate_bpmn_xml(process)
        self.assertIn(f'<bpmn:documentation>{long_name}</bpmn:documentation>', xml)
        self.assertNotIn(f'name="{long_name}"', xml)


class ArtifactLabelTest(unittest.TestCase):
    """Подпись листа и цилиндра держится за свою фигуру.

    Аналитик в draw.io ставит её под фигурой, и выгрузка ставит туда же. Рамку
    при этом резервируем ту, которой подпись нарисует bpmn.io: свою ширину он
    из ``BPMNLabel`` не берёт.
    """

    def _sheet(self):
        return ProcessNode(
            id='d', name='Import shartnoma / loyihasi', type='dataObject',
            style='shape=mxgraph.bpmn.data2;labelPosition=center;verticalLabelPosition=bottom;',
            geometry=Geometry(x=200, y=200, width=30, height=45))

    def test_reserved_box_is_the_one_bpmn_io_draws(self):
        node = self._sheet()
        first = external_label_candidates(node)[0]
        self.assertEqual(first[2], MIN_EXTERNAL_LABEL_WIDTH,
                         f'bpmn.io верстает выносную подпись по рамке в 90 px, а не {first}')

    def test_first_candidate_sits_under_the_shape(self):
        node = self._sheet()
        x, y, w, h = external_label_candidates(node)[0]
        self.assertGreaterEqual(y, node.geometry.y + node.geometry.height)
        self.assertGreater(min(x + w, node.geometry.x + node.geometry.width) - max(x, node.geometry.x), 0,
                           'подпись должна стоять под самой фигурой')

    def test_shift_along_the_face_beats_the_other_side(self):
        """Сдвиг вдоль своей грани пробуется раньше, чем уход вбок."""
        node = self._sheet()
        cands = external_label_candidates(node)
        below = node.geometry.y + node.geometry.height
        centre = node.geometry.x + node.geometry.width / 2
        # Сдвинутая вдоль грани: всё ещё под фигурой, но не по центру.
        shifted = next(i for i, (x, y, w, h) in enumerate(cands)
                       if y >= below and abs(x + w / 2 - centre) > 4)
        # Ушедшая вбок: стоит сбоку от фигуры, а не под ней.
        aside = next(i for i, (x, y, w, h) in enumerate(cands)
                     if y + h > node.geometry.y and y < below
                     and x >= node.geometry.x + node.geometry.width)
        self.assertLess(shifted, aside)


BLANK_ARTIFACTS = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="lane" value="Devonxona" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="900" height="300" as="geometry" />
    </mxCell>
    <mxCell id="step" value="Xatni tasdiqlash" style="rounded=1;html=1;" vertex="1" parent="lane">
      <mxGeometry x="200" y="120" width="160" height="60" as="geometry" />
    </mxCell>
    <mxCell id="done" value="Xat yuboriladi" style="rounded=1;html=1;" vertex="1" parent="lane">
      <mxGeometry x="500" y="120" width="160" height="60" as="geometry" />
    </mxCell>
    <!-- Чистый лист: в draw.io на нём не написано ничего. -->
    <mxCell id="blank_doc" value="" style="shape=mxgraph.bpmn.data2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="240" y="30" width="30" height="45" as="geometry" />
    </mxCell>
    <!-- Подписанный лист рядом: его подпись обязана остаться. -->
    <mxCell id="named_doc" value="Dalolatnoma" style="shape=mxgraph.bpmn.data2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="540" y="30" width="30" height="45" as="geometry" />
    </mxCell>
    <mxCell id="a1" style="html=1;endArrow=none;dashed=1;" edge="1" parent="1" source="blank_doc" target="step">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="a2" style="html=1;endArrow=none;dashed=1;" edge="1" parent="1" source="named_doc" target="done">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f1" style="html=1;" edge="1" parent="1" source="step" target="done">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""


class BlankArtifactCaptionTest(unittest.TestCase):
    """На чистом листе из draw.io не должно появляться слово «Документ».

    Имя фигуре без подписи импорт придумывает сам — оно нужно регламенту и
    реестру, где шаг ищут по названию. На карту такое имя не идёт: в draw.io
    лист нарисован чистым, и «Документ» там — надпись, которой в эталоне нет.
    Для шлюзов это правило уже действовало, артефакты его обходили.
    """

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(BLANK_ARTIFACTS, 'blank.drawio')
        cls.by_id = {n.id: n for n in cls.process.nodes}
        cls.xml = generate_bpmn_xml(cls.process)

    def test_the_model_still_carries_a_name_for_the_registry(self):
        self.assertEqual(self.by_id['blank_doc'].name, 'Документ')

    def test_the_map_shows_no_invented_caption(self):
        self.assertEqual(map_label(self.by_id['blank_doc']), '')

    def test_the_export_leaves_the_blank_sheet_blank(self):
        self.assertNotIn('name="Документ"', self.xml)
        self.assertIn('<bpmn:dataObjectReference', self.xml)

    def test_a_real_caption_survives(self):
        self.assertEqual(map_label(self.by_id['named_doc']), 'Dalolatnoma')
        self.assertIn('name="Dalolatnoma"', self.xml)

    def test_a_step_keeps_its_generated_name(self):
        # У задачи пустой прямоугольник хуже условного имени: по нему шаг
        # находят в регламенте и в реестре PIX.
        node = ProcessNode(id='t', name='Операция STEP-01', type='userTask',
                           geometry=Geometry(x=0, y=0, width=100, height=60))
        self.assertEqual(map_label(node), 'Операция STEP-01')


if __name__ == '__main__':
    unittest.main()


PINNED_SHEETS = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="lane_a" value="Bank tarmoqlari" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="119" y="-480" width="1200" height="240" as="geometry" />
    </mxCell>

    <mxCell id="doc_1" style="shape=mxgraph.bpmn.data2;labelPosition=center;verticalLabelPosition=bottom;align=center;verticalAlign=top;size=15;html=1;" vertex="1" parent="1">
      <mxGeometry x="359" y="-427" width="25" height="33" as="geometry" />
    </mxCell>
    <mxCell id="task_1" value="1. Bankning barcha hududiy bo'linmalari va xizmat ko'rsatish tarmoqlari bo'yicha ma'lumotlarni yig'ish" style="shape=mxgraph.bpmn.task2;whiteSpace=wrap;rectStyle=rounded;size=10;taskMarker=user;fontSize=10;" vertex="1" parent="lane_a">
      <mxGeometry x="281" y="81" width="120" height="80" as="geometry" />
    </mxCell>

    <mxCell id="doc_2" style="shape=mxgraph.bpmn.data2;labelPosition=center;verticalLabelPosition=bottom;align=center;verticalAlign=top;size=15;html=1;" vertex="1" parent="1">
      <mxGeometry x="968" y="-434" width="25" height="33" as="geometry" />
    </mxCell>
    <mxCell id="task_2" value="4. Bir manzildan, boshqa bir manzilga ko'chirilgan tarmoq nomini, manzili, hududi, joylashuvi bo'yicha ma'lumotlarni shakillantirish" style="shape=mxgraph.bpmn.task2;whiteSpace=wrap;rectStyle=rounded;size=10;taskMarker=user;fontSize=10;" vertex="1" parent="lane_a">
      <mxGeometry x="881" y="81" width="120" height="80" as="geometry" />
    </mxCell>

    <mxCell id="flow_1" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="task_1" target="task_2">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="dash_1" style="edgeStyle=elbowEdgeStyle;html=1;dashed=1;dashPattern=1 4;endArrow=none;startArrow=none;rounded=0;exitX=0.5;exitY=1;exitDx=0;exitDy=0;entryX=0;entryY=0.25;entryDx=0;entryDy=0;" edge="1" parent="1" source="doc_1" target="task_1">
      <mxGeometry relative="1" as="geometry">
        <Array as="points"><mxPoint x="371" y="-370" /></Array>
      </mxGeometry>
    </mxCell>
    <mxCell id="dash_2" style="edgeStyle=elbowEdgeStyle;html=1;dashed=1;dashPattern=1 4;endArrow=none;startArrow=none;rounded=0;exitX=0.5;exitY=1;exitDx=0;exitDy=0;entryX=0;entryY=0.25;entryDx=0;entryDy=0;" edge="1" parent="1" source="doc_2" target="task_2">
      <mxGeometry relative="1" as="geometry">
        <Array as="points"><mxPoint x="980" y="-390" /></Array>
      </mxGeometry>
    </mxCell>
  </root>
</mxGraphModel>"""


class PinnedSheetTest(unittest.TestCase):
    """Документ у шага переезжает вместе с рамкой шага.

    Аналитик ставит листочек вплотную к верхнему левому углу шага, и пунктир
    идёт коротким коленом: вниз от низа листочка и вправо в левую грань.
    Подгонка подписи растит шаг ОТ ЦЕНТРА — левая грань уезжает влево, верхняя
    вверх. Листочек оставался на месте, и колено ломалось: рамка накрывала его
    собой, и связь шла в обход всего шага, либо точка входа оказывалась вровень
    с низом листочка, и от колена оставалась чёрточка в три пикселя. По десяти
    новым картам банка так было испорчено 650 связей из 1838.
    """

    def setUp(self):
        self.process = parse_drawio_xml(PINNED_SHEETS, 'pinned.drawio')
        self.nodes = {n.id: n for n in self.process.nodes}
        self.obstacles = er.build_obstacles(self.process.nodes)

    def _route(self, edge_id):
        edge = next(e for e in self.process.edges if e.id == edge_id)
        src, tgt = self.nodes[edge.sourceId], self.nodes[edge.targetId]
        return orthogonal_waypoints(edge, src, tgt, None, self.obstacles)

    def test_the_step_grew(self):
        # Без роста шага чинить нечего: фикстура обязана его вызывать.
        self.assertGreater(self.nodes['task_1'].geometry.height, 80)
        self.assertGreater(self.nodes['task_2'].geometry.width, 120)

    def test_sheet_stays_clear_of_the_step(self):
        for doc_id, task_id in (('doc_1', 'task_1'), ('doc_2', 'task_2')):
            doc, task = self.nodes[doc_id].geometry, self.nodes[task_id].geometry
            self.assertLessEqual(doc.x + doc.width, task.x,
                                 f'{doc_id} оказался под рамкой шага')

    def test_dash_is_the_drawio_elbow_with_visible_legs(self):
        # Эталон — draw.io: пунктир «локтем» рисуется его же алгоритмом
        # (``drawio_elbow_route`` сверен с исходником редактора).
        for edge_id in ('dash_1', 'dash_2'):
            route = self._route(edge_id)
            edge = next(e for e in self.process.edges if e.id == edge_id)
            drawn = er.drawio_elbow_route(edge, self.nodes[edge.sourceId], self.nodes[edge.targetId])
            self.assertEqual(route, er._collapse_collinear_runs(er._snap_to_pixel_grid(drawn)), edge_id)
            # Видимый отрезок — не короче MIN_SEGMENT: у самой студии короче 6 px
            # отрезков не бывает. Колено draw.io ставит посередине между
            # листочком и шагом, и плечи у него — половина этого зазора.
            legs = [abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(route, route[1:])]
            self.assertTrue(all(leg >= er.MIN_SEGMENT for leg in legs),
                            f'{edge_id}: колено вырождается, отрезки {legs}')
