"""Читаемость карты после импорта и отчёт о её качестве сотруднику.

Фикстура повторяет то, на что жалуются аналитики после выгрузки в bpmn.io:
таймер нарисован прямоугольной рамкой (значок часов растягивается в эллипс),
подпись шага не помещается в фигуру и уезжает под маркер задачи, хранилище
данных лежит поверх шага, а у шлюза не подписана ветка.
"""
import os
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import Geometry, ProcessNode
from app.services.drawio_parser import parse_drawio_xml
from app.services.bpmn_exporter import band_label, generate_bpmn_xml
from app.services.layout import (
    LINE_HEIGHT,
    MIN_EXTERNAL_LABEL_WIDTH,
    external_label_candidates,
    label_height,
    label_size,
    normalize_layout,
    wrapped_line_count,
)

BPMN_NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
}

MESSY_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="lane_a" value="Ofis" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="1400" height="360" as="geometry" />
    </mxCell>

    <mxCell id="start_1" value="Boshlanish" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=general;outline=standard;verticalLabelPosition=bottom;verticalAlign=top;" vertex="1" parent="lane_a">
      <mxGeometry x="60" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="task_long" value="Mijozga hisob raqam ochilganligi to'g'risida EHA dasturida xat rasmiylashtirish va jo'natish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="180" y="105" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="ds_iabs" value="IABS" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;verticalAlign=bottom;" vertex="1" parent="lane_a">
      <mxGeometry x="200" y="120" width="60" height="40" as="geometry" />
    </mxCell>
    <mxCell id="timer_wide" value="10 min" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=timer;outline=catching;aspect=fixed;labelPosition=left;align=right;verticalLabelPosition=middle;verticalAlign=middle;" vertex="1" parent="lane_a">
      <mxGeometry x="420" y="120" width="80" height="50" as="geometry" />
    </mxCell>
    <mxCell id="gw_1" value="Hujjatlar to'liqmi?" style="shape=mxgraph.bpmn.gateway2;rhombus;html=1;gwType=exclusive;verticalLabelPosition=top;verticalAlign=bottom;" vertex="1" parent="lane_a">
      <mxGeometry x="600" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="task_b" value="Rad javob berish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="760" y="105" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="end_1" value="Tugadi" style="shape=mxgraph.bpmn.event;ellipse;html=1;symbol=terminate2;outline=end;" vertex="1" parent="lane_a">
      <mxGeometry x="980" y="120" width="50" height="50" as="geometry" />
    </mxCell>

    <mxCell id="f1" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="start_1" target="task_long">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f2" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="task_long" target="timer_wide">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f3" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="timer_wide" target="gw_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f4" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="gw_1" target="task_b">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f5" value="To'liq" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="gw_1" target="end_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f6" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="task_b" target="end_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="a1" style="dashed=1;endArrow=open;" edge="1" parent="lane_a" source="ds_iabs" target="task_long">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""


class TextMetricsTest(unittest.TestCase):
    """Оценка размеров подписи — основа всей подгонки фигур."""

    def test_wrapping_grows_with_narrower_box(self):
        text = "Mijozga hisob raqam ochilganligi to'g'risida xat rasmiylashtirish"
        self.assertGreater(wrapped_line_count(text, 80), wrapped_line_count(text, 240))

    def test_marker_reserves_room_above_and_below(self):
        # Маркер задачи занимает левый верхний угол; подпись центрируется, и без
        # запаса первая строка уезжает под маркер.
        with_marker = label_height('Qisqa', 120, True)
        without = label_height('Qisqa', 120, False)
        self.assertGreaterEqual(with_marker - without, 30)

    def test_short_label_gets_a_snug_box(self):
        # Рамка в 90 px под «To'liq» — это 55 px лишнего места, из-за которых
        # подпись ветки ложилась на соседний шаг.
        width, _ = label_size("To'liq")
        self.assertLess(width, 60)


class LayoutNormalizationTest(unittest.TestCase):
    """Геометрия карты после импорта должна быть читаемой."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(MESSY_MAP, "messy.drawio")
        cls.by_id = {n.id: n for n in cls.process.nodes}

    def test_event_frame_becomes_square(self):
        # 80×50 в draw.io — это круг 50 px и место под подпись слева; импортёр
        # BPMN растянул бы круг в эллипс вместе со значком часов.
        timer = self.by_id["timer_wide"]
        self.assertEqual(timer.geometry.width, timer.geometry.height)
        self.assertEqual(timer.geometry.width, 50)

    def test_event_stays_centred_on_its_original_spot(self):
        timer = self.by_id["timer_wide"]
        # Исходный центр: x 460 + дорожка 40 = 500, y 145 + 40 = 185.
        self.assertEqual(timer.geometry.x + timer.geometry.width / 2, 500)
        self.assertEqual(timer.geometry.y + timer.geometry.height / 2, 185)

    def test_long_label_fits_into_its_shape(self):
        for node in self.process.nodes:
            if node.type not in ('task', 'userTask', 'serviceTask', 'subProcess'):
                continue
            need = label_height(node.name, node.geometry.width, True)
            self.assertLessEqual(need, node.geometry.height + 0.5, node.name)

    def test_artifact_no_longer_lies_on_the_step(self):
        store = self.by_id["ds_iabs"]
        task = self.by_id["task_long"]
        sg, tg = store.geometry, task.geometry
        overlap_x = min(sg.x + sg.width, tg.x + tg.width) - max(sg.x, tg.x)
        overlap_y = min(sg.y + sg.height, tg.y + tg.height) - max(sg.y, tg.y)
        self.assertFalse(overlap_x > 0 and overlap_y > 0, 'цилиндр перекрывает подпись шага')

    def test_nothing_escapes_its_lane(self):
        lane = self.process.lanes[0]
        lg = lane.geometry
        for node in self.process.nodes:
            if node.laneId != lane.id:
                continue
            g = node.geometry
            self.assertGreaterEqual(g.x, lg.x, node.name)
            self.assertGreaterEqual(g.y, lg.y, node.name)
            self.assertLessEqual(g.x + g.width, lg.x + lg.width, node.name)
            self.assertLessEqual(g.y + g.height, lg.y + lg.height, node.name)

    def test_normalization_is_idempotent(self):
        before = [(n.id, n.geometry.model_dump()) for n in self.process.nodes]
        normalize_layout(self.process.nodes, self.process.lanes)
        after = [(n.id, n.geometry.model_dump()) for n in self.process.nodes]
        self.assertEqual(before, after)


class ExternalLabelPlacementTest(unittest.TestCase):
    """Выносные подписи ставим туда, куда их поставил аналитик."""

    @staticmethod
    def _node(style):
        return ProcessNode(
            id='n', name='Ожидание 10 мин', type='intermediateTimerEvent',
            geometry=Geometry(x=200, y=200, width=50, height=50), style=style,
        )

    def test_draw_io_label_position_wins(self):
        # У таймера подпись уведена влево, чтобы не лечь на вертикальную связь.
        left = external_label_candidates(self._node('labelPosition=left;align=right;'))[0]
        self.assertLess(left[0] + left[2], 200)

        above = external_label_candidates(self._node('verticalLabelPosition=top;'))[0]
        self.assertLess(above[1] + above[3], 200)

        below = external_label_candidates(self._node(''))[0]
        self.assertGreater(below[1], 250)

    def test_reserved_box_matches_what_bpmn_io_draws(self):
        """Резерв под подпись обязан совпадать с нарисованной рамкой.

        Ширину из ``BPMNLabel`` bpmn.io не читает: длинный вопрос шлюза он в
        любом случае сложит по своей рамке в 90 px. Пока резервировалась
        другая, проверка пересечений считала не ту рамку — подпись вставала
        «на свободное место» и накрывала соседний шаг.
        """
        node = ProcessNode(
            id='gw', type='exclusiveGateway',
            name="Milliy va xorijiy ro'yxatga mos kelishi FISH va tug'.sana (AML/CFT)",
            geometry=Geometry(x=0, y=0, width=50, height=50), style='',
        )
        boxes = external_label_candidates(node)
        self.assertEqual({box[2] for box in boxes}, {MIN_EXTERNAL_LABEL_WIDTH})
        lines = wrapped_line_count(node.name, MIN_EXTERNAL_LABEL_WIDTH)
        self.assertEqual({box[3] for box in boxes}, {int(round(lines * LINE_HEIGHT + 4))})


class BpmnLabelGeometryTest(unittest.TestCase):
    """Импортёр не должен сам решать, где стоять подписи."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(MESSY_MAP, "messy.drawio")
        cls.root = ET.fromstring(generate_bpmn_xml(cls.process))
        cls.plane = cls.root.find(".//bpmndi:BPMNPlane", BPMN_NS)

    def _bounds(self, el):
        b = el.find("dc:Bounds", BPMN_NS)
        return tuple(float(b.get(k)) for k in ("x", "y", "width", "height"))

    def test_events_and_gateways_carry_label_bounds(self):
        proc = self.root.find("bpmn:process", BPMN_NS)
        external = {
            el.get("id") for el in proc
            if el.tag.split("}")[-1] in (
                "startEvent", "endEvent", "intermediateCatchEvent",
                "exclusiveGateway", "dataStoreReference",
            )
        }
        self.assertTrue(external)
        for shape in self.plane.findall("bpmndi:BPMNShape", BPMN_NS):
            if shape.get("bpmnElement") in external:
                self.assertIsNotNone(
                    shape.find("bpmndi:BPMNLabel", BPMN_NS),
                    shape.get("bpmnElement"),
                )

    def test_no_label_is_buried_under_a_shape(self):
        shapes = []
        labels = []
        for shape in self.plane.findall("bpmndi:BPMNShape", BPMN_NS):
            element = shape.get("bpmnElement")
            if not element.startswith("Participant") and not element.endswith("lane_a"):
                shapes.append(self._bounds(shape))
            label = shape.find("bpmndi:BPMNLabel", BPMN_NS)
            if label is not None:
                labels.append(self._bounds(label))
        for edge in self.plane.findall("bpmndi:BPMNEdge", BPMN_NS):
            label = edge.find("bpmndi:BPMNLabel", BPMN_NS)
            if label is not None:
                labels.append(self._bounds(label))
        self.assertTrue(labels)

        for lx, ly, lw, lh in labels:
            covered = 0.0
            for sx, sy, sw, sh in shapes:
                dx = min(lx + lw, sx + sw) - max(lx, sx)
                dy = min(ly + lh, sy + sh) - max(ly, sy)
                if dx > 0 and dy > 0:
                    covered = max(covered, dx * dy / (lw * lh))
            self.assertLessEqual(covered, 0.5, f'подпись {(lx, ly, lw, lh)} скрыта фигурой')


class ImportDiagnosticsTest(unittest.TestCase):
    """Сотрудник должен увидеть, где карта неполна и что платформа достроила."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(MESSY_MAP, "messy.drawio")
        cls.by_code = {}
        for issue in cls.process.validation:
            cls.by_code.setdefault(issue.code, []).append(issue)

    def test_every_issue_is_addressed_and_actionable(self):
        self.assertTrue(self.process.validation)
        for issue in self.process.validation:
            self.assertTrue(issue.code, issue.message)
            self.assertTrue(issue.message)
            if issue.level in ('error', 'warning'):
                self.assertTrue(issue.hint, f'{issue.code}: нет подсказки, что делать')

    def test_second_branch_of_a_binary_gateway_is_completed(self):
        # У развилки «да/нет» аналитик подписывает одну ветку: на рисунке и так
        # понятно, что вторая — противоположная. PIX так не умеет, поэтому
        # условие подставляется, а сотруднику об этом говорится.
        self.assertNotIn('gateway_branch_unlabeled', self.by_code)
        issue = self.by_code['gateway_branch_completed'][0]
        self.assertEqual(issue.level, 'warning')
        self.assertIn("To'liq emas", issue.message)

    def test_completed_condition_reaches_the_branch(self):
        branch = next(e for e in self.process.edges if e.id == 'f4')
        self.assertEqual(branch.name, "To'liq emas")
        self.assertEqual(branch.condition, "To'liq emas")

    def test_step_without_a_timer_badge_is_reported(self):
        self.assertIn('no_step_time', self.by_code)
        issue = self.by_code['no_step_time'][0]
        self.assertIn('подставлено', issue.message)
        self.assertTrue(issue.nodeId, 'замечание должно вести к конкретному шагу')

    def test_repairs_are_reported_as_info(self):
        for code in ('geometry_squared', 'geometry_fitted', 'geometry_moved'):
            self.assertIn(code, self.by_code, code)
            self.assertEqual(self.by_code[code][0].level, 'info')

    def test_timer_name_is_not_reported_as_missing_caption(self):
        # «Ожидание 10 мин» — это длительность таймера, а не отсутствие подписи.
        generated = self.by_code.get('generated_name', [])
        self.assertFalse(
            [i for i in generated if 'Ожидание' in i.message],
            'подпись таймера не должна считаться дефектом',
        )


#: Карта с заголовком над пулом и с обоими видами шлюзов.
#:
#: Так подписаны все десять карт из августовской партии: название процесса
#: набрано широкой рамкой ВНЕ дорожек, ни с чем не соединённой. Параллельный
#: шлюз там же и ветки у него не подписаны — у шлюза «И» условия и не бывает.
CAPTION_AND_GATEWAYS_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <mxCell id="title" value="Bank tarmog'ini ochish jarayoni" style="rounded=1;dashed=1;whiteSpace=wrap;html=1;fontSize=72;" vertex="1" parent="1">
      <mxGeometry x="40" y="-300" width="2880" height="160" as="geometry" />
    </mxCell>

    <mxCell id="lane_a" value="MXD xizmati" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="40" y="40" width="1400" height="360" as="geometry" />
    </mxCell>

    <mxCell id="start_1" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane_a">
      <mxGeometry x="60" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="gw_and" value="" style="shape=mxgraph.bpmn.gateway2;html=1;gwType=parallel;symbol=none;" vertex="1" parent="lane_a">
      <mxGeometry x="180" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="task_a" value="Xizmat yozishmani kelishish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="320" y="60" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="task_b" value="Xizmat yozishmani imzolash" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="320" y="200" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="gw_xor" value="Kelishildimi?" style="shape=mxgraph.bpmn.gateway2;html=1;gwType=exclusive;" vertex="1" parent="lane_a">
      <mxGeometry x="540" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="task_c" value="Hujjatni qaytarish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="680" y="200" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="end_1" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;outline=end;" vertex="1" parent="lane_a">
      <mxGeometry x="900" y="120" width="50" height="50" as="geometry" />
    </mxCell>

    <mxCell id="f1" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="start_1" target="gw_and">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f2" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="gw_and" target="task_a">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f3" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="gw_and" target="task_b">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f4" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="task_a" target="gw_xor">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f5" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="task_b" target="gw_xor">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f6" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="gw_xor" target="end_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f7" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="gw_xor" target="task_c">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f8" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_a" source="task_c" target="end_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""


class DiagramCaptionTest(unittest.TestCase):
    """Название процесса над пулом — подпись схемы, а не её первый шаг."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(CAPTION_AND_GATEWAYS_MAP, "caption.drawio")
        cls.by_id = {n.id: n for n in cls.process.nodes}
        cls.by_code = {}
        for issue in cls.process.validation:
            cls.by_code.setdefault(issue.code, []).append(issue)

    def test_caption_is_not_a_step(self):
        self.assertEqual(self.by_id['title'].type, 'textAnnotation')
        self.assertIsNone(self.by_id['title'].code)

    def test_caption_stays_on_the_map_where_draw_io_drew_it(self):
        geo = self.by_id['title'].geometry
        self.assertEqual((geo.x, geo.y), (40, -300))
        self.assertEqual(geo.width, 2880)

    def test_caption_does_not_inflate_process_time(self):
        # Пока рамка считалась шагом, к сроку процесса прибавлялся её час.
        self.assertNotIn('title', {n.id for n in self.process.nodes if n.type == 'userTask'})

    def test_the_employee_is_told_the_caption_was_dropped(self):
        issue = self.by_code['caption_not_a_step'][0]
        self.assertEqual(issue.level, 'info')
        self.assertIn('title', issue.nodeIds or [])

    def test_step_inside_a_lane_is_never_taken_for_a_caption(self):
        self.assertEqual(self.by_id['task_a'].type, 'userTask')


class GatewayBranchLabelTest(unittest.TestCase):
    """Условие спрашиваем у развилки, а не у параллельного шлюза."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(CAPTION_AND_GATEWAYS_MAP, "caption.drawio")
        cls.by_code = {}
        for issue in cls.process.validation:
            cls.by_code.setdefault(issue.code, []).append(issue)

    def test_unlabeled_and_branches_are_not_an_error(self):
        # У шлюза «И» срабатывают все ветки: условия у них нет по нотации.
        unlabeled = self.by_code.get('gateway_branch_unlabeled', [])
        self.assertFalse(
            [i for i in unlabeled if i.nodeId == 'gw_and'],
            'ветки параллельного шлюза не требуют подписи',
        )

    def test_unlabeled_xor_branches_are_still_an_error(self):
        unlabeled = self.by_code.get('gateway_branch_unlabeled', [])
        self.assertTrue([i for i in unlabeled if i.nodeId == 'gw_xor'])


#: Пул, у которого половина первой дорожки сложена в отдельную рамку.
#:
#: Так сделано в «AVGUST-1.29»: рамка без имени и без высоты приткнута к низу
#: пула, а её содержимое лежит по координатам внутри настоящих дорожек.
STRAY_FRAME_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="pool" value="SQB" style="swimlane;html=1;horizontal=1;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="119" y="-520" width="1400" height="360" as="geometry" />
    </mxCell>
    <mxCell id="lane_top" value="MXD xizmati" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="pool">
      <mxGeometry y="40" width="1400" height="160" as="geometry" />
    </mxCell>
    <mxCell id="lane_low" value="MXD direktori" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="pool">
      <mxGeometry y="200" width="1400" height="160" as="geometry" />
    </mxCell>
    <mxCell id="frame" value="" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="pool">
      <mxGeometry y="360" width="1400" as="geometry" />
    </mxCell>

    <mxCell id="start_1" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="frame">
      <mxGeometry x="200" y="-280" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="task_a" value="Xizmat yozishmani kelishish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="frame">
      <mxGeometry x="340" y="-295" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="end_1" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;outline=end;" vertex="1" parent="lane_top">
      <mxGeometry x="560" y="55" width="50" height="50" as="geometry" />
    </mxCell>

    <mxCell id="f1" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="start_1" target="task_a">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f2" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="task_a" target="end_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""


class StrayLaneFrameTest(unittest.TestCase):
    """Рамка-подложка не должна превращаться в ещё одну дорожку.

    Полнота дорожки считалась по родителю ячейки, а исполнителя шага разбор
    берёт по тому, где фигура лежит на рисунке. Безымянная рамка без высоты
    выглядела полной, оставалась на карте как «Дорожка N» и приезжала в
    выгрузку пустой полосой, которой в draw.io нет.
    """

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(STRAY_FRAME_MAP, "stray.drawio")

    def test_only_the_drawn_lanes_survive(self):
        self.assertEqual(
            sorted(lane.name for lane in self.process.lanes),
            ['MXD direktori', 'MXD xizmati'],
        )

    def test_no_shape_is_lost_with_the_frame(self):
        self.assertEqual(
            sorted(n.id for n in self.process.nodes if n.type != 'lane'),
            ['end_1', 'start_1', 'task_a'],
        )

    def test_content_of_the_frame_joins_the_lane_it_lies_in(self):
        by_id = {n.id: n for n in self.process.nodes}
        top = next(l for l in self.process.lanes if l.name == 'MXD xizmati')
        for node_id in ('start_1', 'task_a'):
            self.assertEqual(by_id[node_id].laneId, top.id, node_id)


class BandLabelTest(unittest.TestCase):
    """Название дорожки обязано уместиться в её заголовочную полосу.

    Полоса — 30 px, строка при кегле 12 — 14.4 px: две влезают, третья уже нет.
    Отрисовщик переносит подпись жадно по словам, поэтому мерка «общая ширина
    не больше двух длин строки» врёт: длинное слово уезжает на новую строку
    целиком, и подпись раскладывается в три строки. Так вылезала за полосу
    дорожка «Bank tarmoqlarini muvofiqlashtirish xizmati bosh menejer (MXD)».
    """

    LANE_HEIGHT = 160.0

    def _lines(self, text):
        return wrapped_line_count(text, max(self.LANE_HEIGHT - 8.0, 24.0))

    def test_long_name_is_cut_to_two_lines(self):
        name = "Bank tarmoqlarini muvofiqlashtirish xizmati bosh menejer (MXD)"
        self.assertEqual(self._lines(name), 3, 'фикстура должна не помещаться')
        self.assertLessEqual(self._lines(band_label(name, self.LANE_HEIGHT)), 2)

    def test_name_that_fits_is_left_whole(self):
        name = "MXD direktori o'rinbosari"
        self.assertEqual(band_label(name, self.LANE_HEIGHT), name)

    def test_one_long_word_is_cut_by_letters(self):
        name = 'Supercalifragilisticexpialidociousandthensomeverylongword'
        shown = band_label(name, self.LANE_HEIGHT)
        self.assertTrue(shown.endswith('…'))
        self.assertLessEqual(self._lines(shown), 2)

    def test_full_name_stays_in_the_export(self):
        name = "Bank tarmoqlarini muvofiqlashtirish xizmati bosh menejer (MXD)"
        process = parse_drawio_xml(
            STRAY_FRAME_MAP.replace('MXD xizmati', name), 'band.drawio')
        xml = generate_bpmn_xml(process)
        self.assertIn(f'<bpmn:documentation>{name}</bpmn:documentation>', xml)


#: Карта, где шагу с длинной подписью расти некуда: соседи стоят вплотную.
CRAMPED_LABEL_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="lane_a" value="Ofis" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="600" height="120" as="geometry" />
    </mxCell>
    <mxCell id="task_long" value="Mijozga hisob raqam ochilganligi to'g'risida EHA dasturida xat rasmiylashtirish va jo'natish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="20" y="20" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="task_b" value="Sosed" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="160" y="20" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="task_c" value="Sosed 2" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="300" y="20" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="f1" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="task_long" target="task_b">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f2" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="task_b" target="task_c">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""


class CrampedLabelTest(unittest.TestCase):
    """Отчёт не имеет права звать «расширенной» фигуру, где подпись не влезла.

    ``fit_labels`` растит фигуру, пока рядом есть место. Места может не быть
    вовсе — соседи стоят вплотную, — и тогда подпись остаётся за рамкой, а
    фигура всё равно попадала в список «расширены под подпись». Сотрудник
    читал, что дело сделано, и шёл дальше, хотя bpmn.io и Процессная студия
    обрежут этот текст по рамке. По двадцати двум картам банка так было
    записано 62 фигуры.
    """

    LONG = ("Mijozga hisob raqam ochilganligi to'g'risida EHA dasturida xat "
            "rasmiylashtirish va jo'natish")

    def setUp(self):
        def node(nid, name, x):
            return ProcessNode(
                id=nid, name=name, type='userTask', laneId='lane_a',
                geometry=Geometry(x=x, y=20, width=120, height=80),
            )
        self.lane = ProcessNode(
            id='lane_a', name='Ofis', type='lane',
            geometry=Geometry(x=0, y=0, width=600, height=120),
        )
        self.cramped = node('a', self.LONG, 20)
        self.nodes = [self.cramped, node('b', 'Sosed', 160), node('c', 'Sosed 2', 300)]
        self.report = normalize_layout(self.nodes, [self.lane])

    def test_shape_without_room_is_not_called_fitted(self):
        self.assertNotIn('a', self.report['fitted'])
        self.assertIn('a', self.report['cramped'])

    def test_the_label_really_does_not_fit(self):
        g = self.cramped.geometry
        self.assertGreater(label_height(self.LONG, g.width, True), g.height)

    def test_the_employee_is_warned(self):
        process = parse_drawio_xml(CRAMPED_LABEL_MAP, 'cramped.drawio')
        cramped = [v for v in process.validation if v.code == 'label_cramped']
        self.assertTrue(cramped, 'про непоместившуюся подпись обязаны сказать')
        self.assertEqual(cramped[0].level, 'warning')
        fitted = [v for v in process.validation if v.code == 'geometry_fitted']
        overlap = set(cramped[0].nodeIds or []) & set(fitted[0].nodeIds or [] if fitted else [])
        self.assertEqual(overlap, set(), 'одна фигура не может быть и расширенной, и тесной')


#: Пул, где две дорожки записаны потомками третьей, а нарисованы рядом с ней.
#:
#: Так лежат дорожки в «Xizmat avtotransportini» и «Tovarlar … logistikasi»:
#: «MXD guruh rahbari» числится потомком «MXD mas'ul xodimi» и нарисована
#: целиком ниже неё, а «Xizmat ko'rsatuvchi tashkilot» — целиком выше.
MISPARENTED_LANE_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="pool" value="SQB" style="swimlane;html=1;horizontal=1;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="1400" height="520" as="geometry" />
    </mxCell>
    <mxCell id="lane_high" value="Xizmat ko&apos;rsatuvchi tashkilot" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="lane_mid">
      <mxGeometry y="-160" width="1400" height="160" as="geometry" />
    </mxCell>
    <mxCell id="lane_mid" value="MXD mas&apos;ul xodimi" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="pool">
      <mxGeometry y="200" width="1400" height="160" as="geometry" />
    </mxCell>
    <mxCell id="lane_low" value="MXD guruh rahbari" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="lane_mid">
      <mxGeometry y="160" width="1400" height="160" as="geometry" />
    </mxCell>

    <mxCell id="start_1" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane_high">
      <mxGeometry x="120" y="55" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="task_mid" value="Bildirishnomani tayyorlash" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_mid">
      <mxGeometry x="300" y="40" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="task_low" value="Kelishish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_low">
      <mxGeometry x="300" y="40" width="120" height="80" as="geometry" />
    </mxCell>

    <mxCell id="f1" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="start_1" target="task_mid">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f2" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" source="task_mid" target="task_low">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""


class MisparentedLaneTest(unittest.TestCase):
    """Дорожка, записанная потомком соседней, остаётся дорожкой.

    Пулом считалась любая дорожка, внутри которой лежит другая, — и такая
    «дорожка-пул» с карты пропадала. На картах банка вложенность бывает
    мнимой: аналитик перетащил дорожку мышью, ссылка на родителя осталась, а
    нарисована она выше или ниже его рамки. Вместе с пропавшей дорожкой шаги
    теряли исполнителя, а раскладка сбивала их в кучу на чужой строке.
    """

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(MISPARENTED_LANE_MAP, 'misparented.drawio')

    def test_all_three_lanes_survive(self):
        self.assertEqual(
            sorted(lane.name for lane in self.process.lanes),
            ["MXD guruh rahbari", "MXD mas'ul xodimi", "Xizmat ko'rsatuvchi tashkilot"],
        )

    def test_every_step_keeps_its_owner(self):
        by_id = {n.id: n for n in self.process.nodes}
        lane_of = {lane.id: lane.name for lane in self.process.lanes}
        self.assertEqual(lane_of.get(by_id['start_1'].laneId), "Xizmat ko'rsatuvchi tashkilot")
        self.assertEqual(lane_of.get(by_id['task_mid'].laneId), "MXD mas'ul xodimi")
        self.assertEqual(lane_of.get(by_id['task_low'].laneId), 'MXD guruh rahbari')

    def test_steps_do_not_pile_up_in_the_export(self):
        root = ET.fromstring(generate_bpmn_xml(self.process).encode('utf-8'))
        boxes = {}
        for shape in root.iter(f'{{{BPMN_NS["bpmndi"]}}}BPMNShape'):
            bounds = shape.find(f'{{{BPMN_NS["dc"]}}}Bounds')
            boxes[shape.get('bpmnElement')] = (
                float(bounds.get('y')), float(bounds.get('height')))
        mid_y, mid_h = boxes['task_mid']
        low_y, _low_h = boxes['task_low']
        self.assertGreaterEqual(low_y, mid_y + mid_h, 'шаги соседних дорожек наложились')


if __name__ == "__main__":
    unittest.main()
