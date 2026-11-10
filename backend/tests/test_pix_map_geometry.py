"""Как карта выглядит после импорта в Процессную студию.

Проверки здесь — про геометрию, а не про формат: файл может быть безупречен по
схеме и при этом открываться в студии кашей. Аналитик жаловался ровно на три
вещи, и каждой отвечает свой раздел: линии не доходят до фигур, подписи лежат
поверх линий, фигуры налезают друг на друга.

Эталон один и тот же — выгрузка самой PIX ``tests/fixtures/sap.pmm``: 66 фигур,
на каждый тип ровно один размер, самый короткий отрезок ломаной 8 px.
"""
import io
import os
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import Geometry, ProcessNode
from app.services.drawio_parser import parse_drawio_xml
import app.services.edge_routing as er
from app.services.bpmn_exporter import step_duration_text
from app.services.edge_routing import MIN_SEGMENT, orthogonal_waypoints
from app.services.pmm_exporter import (
    drawio_frames,
    generate_pmm_zip,
    label_position,
    map_label,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def _map_root(process):
    package = zipfile.ZipFile(io.BytesIO(generate_pmm_zip(process)))
    part = next(n for n in package.namelist() if n.startswith('pm/maps/'))
    return ET.fromstring(package.read(part))


def _boxes(root):
    """Абсолютные рамки всех фигур: дети дорожек лежат в относительных."""
    out = {}

    def walk(element, ox, oy):
        for child in element:
            if child.tag != 'node':
                continue
            x = float(child.get('x', 0)) + ox
            y = float(child.get('y', 0)) + oy
            out[child.get('id')] = {
                'box': (x, y, float(child.get('width', 0)), float(child.get('height', 0))),
                'type': child.get('type'),
                'label': child.get('label') or '',
            }
            walk(child, x, y)

    walk(root, 0.0, 0.0)
    return out


def _shown(label):
    """Подпись так, как её видно на карте: без неразрывных пробелов-отступов."""
    return (label or '').strip(' ').replace(' ', ' ')


def _waypoints(connector):
    return [
        (float(w.get('x')), float(w.get('y')))
        for w in sorted(connector.findall('waypoint'), key=lambda w: int(w.get('index', 0)))
    ]


def _gap(point, box):
    x, y = point
    bx, by, bw, bh = box
    dx = max(bx - x, 0.0, x - (bx + bw))
    dy = max(by - y, 0.0, y - (by + bh))
    return (dx * dx + dy * dy) ** 0.5


DRAWIO = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="lane" value="Операционный отдел" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="0" width="1400" height="400" as="geometry" />
        </mxCell>
        <mxCell id="s" value="Начало" style="ellipse;fillColor=#10b981;" vertex="1" parent="lane">
          <mxGeometry x="80" y="120" width="50" height="50" as="geometry" />
        </mxCell>
        <mxCell id="t1" value="Проверка документов 5 мин" style="rounded=1;" vertex="1" parent="lane">
          <mxGeometry x="220" y="105" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="g" value="Всё верно?" style="rhombus;" vertex="1" parent="lane">
          <mxGeometry x="480" y="115" width="50" height="50" as="geometry" />
        </mxCell>
        <mxCell id="t2" value="Оформление 10 мин" style="rounded=1;" vertex="1" parent="lane">
          <mxGeometry x="620" y="105" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="db" value="IABS" style="shape=cylinder3;" vertex="1" parent="lane">
          <mxGeometry x="300" y="200" width="80" height="40" as="geometry" />
        </mxCell>
        <mxCell id="e" value="Готово" style="ellipse;fillColor=#059669;" vertex="1" parent="lane">
          <mxGeometry x="900" y="120" width="50" height="50" as="geometry" />
        </mxCell>
        <mxCell id="f1" edge="1" source="s" target="t1" parent="lane" />
        <mxCell id="f2" edge="1" source="t1" target="g" parent="lane" />
        <mxCell id="f3" value="Да" edge="1" source="g" target="t2" parent="lane">
          <mxGeometry relative="1" as="geometry"><mxPoint as="offset" /></mxGeometry>
        </mxCell>
        <mxCell id="f4" edge="1" source="t2" target="e" parent="lane" />
        <mxCell id="a1" style="dashed=1;" edge="1" source="db" target="t1" parent="lane" />
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


class ShapeSizeTest(unittest.TestCase):
    """Фигура в студии того размера, каким её нарисовали в draw.io.

    Студия рисует шлюз и событие в объявленной рамке, а не своим шаблоном
    (PIX банка, 2026-09-24, проба P15: ромб 50 × 50 и таймер 40 × 40 так и
    легли, линии к ним — как в файле). Шаблон 60 px сдвигал конец каждой линии
    к шлюзу на 5 px: из-за него точно с draw.io совпадало 58 % линий, а не 91 %.
    """

    def setUp(self):
        self.process = parse_drawio_xml(DRAWIO, 'geometry.drawio')
        self.root = _map_root(self.process)
        self.boxes = _boxes(self.root)

    def test_events_and_gateways_keep_the_drawio_frame(self):
        expected = {
            'start_event_none': (50, 50),
            'end_event_none': (50, 50),
            'gateway_xor': (50, 50),
            # Часы шага — наш значок, в draw.io его нет: размер у него свой.
            'boundary_non_interrupting_event_timer': (48, 48),
        }
        seen = {}
        for item in self.boxes.values():
            if item['type'] in expected:
                seen.setdefault(item['type'], set()).add(
                    (int(item['box'][2]), int(item['box'][3])))
        for kind, size in expected.items():
            self.assertIn(kind, seen, f'{kind} не попал в карту')
            self.assertEqual(seen[kind], {size}, kind)

    def test_artifact_keeps_the_size_the_analyst_drew(self):
        """Цилиндр базы рисуется таким, каким его нарисовали.

        Приведение к 62 × 56 из выгрузки студии делало его вдвое крупнее
        нарисованного (в картах банка 30 × 25 … 50 × 36) и заметнее самого
        шага, к которому он относится. Подпись у артефакта печатается снаружи,
        так что места под текст внутри ему не нужно.
        """
        sizes = {
            (int(i['box'][2]), int(i['box'][3]))
            for i in self.boxes.values() if i['type'] == 'dataStorage'
        }
        self.assertEqual(sizes, {(80, 40)}, 'размер цилиндра из draw.io не сохранён')

    def test_task_keeps_the_size_the_analyst_drew(self):
        # Размер задачи несёт смысл — длину подписи; шаблоном он не задан.
        widths = {
            int(i['box'][2]) for i in self.boxes.values() if i['type'] == 'task'
        }
        self.assertIn(180, widths)


class ConnectorGeometryTest(unittest.TestCase):
    """Линия обязана начинаться и кончаться на фигуре и идти по осям."""

    def setUp(self):
        self.process = parse_drawio_xml(DRAWIO, 'geometry.drawio')
        self.root = _map_root(self.process)
        self.boxes = _boxes(self.root)

    def test_every_polyline_touches_both_shapes(self):
        for connector in self.root.findall('connector'):
            route = _waypoints(connector)
            if not route:
                continue
            source = self.boxes[connector.get('sourceNodeId')]['box']
            target = self.boxes[connector.get('targetNodeId')]['box']
            self.assertLessEqual(_gap(route[0], source), 1.0, connector.get('id'))
            self.assertLessEqual(_gap(route[-1], target), 1.0, connector.get('id'))

    def test_no_segment_is_diagonal(self):
        # Связи в PIX имеют тип step: косой отрезок в них выглядит поломкой.
        for connector in self.root.findall('connector'):
            route = _waypoints(connector)
            for (x1, y1), (x2, y2) in zip(route, route[1:]):
                self.assertTrue(
                    abs(x2 - x1) < 0.5 or abs(y2 - y1) < 0.5,
                    f'{connector.get("id")}: косой отрезок {(x1, y1)}→{(x2, y2)}',
                )

    def test_shapes_facing_each_other_are_joined_by_one_straight_line(self):
        by_id = {n.id: n for n in drawio_frames(self.process).nodes}
        edge = next(e for e in self.process.edges if e.sourceId == 't1' and e.targetId == 'g')
        route = orthogonal_waypoints(edge, by_id['t1'], by_id['g'])
        self.assertEqual(len(route), 2, f'ожидалась прямая, получено {route}')
        self.assertEqual(route[0][1], route[1][1])

    def test_no_micro_staircase_between_the_ends(self):
        for connector in self.root.findall('connector'):
            route = _waypoints(connector)
            for index, (a, b) in enumerate(zip(route, route[1:])):
                length = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
                if 0 < index < len(route) - 2:
                    self.assertGreaterEqual(
                        length, MIN_SEGMENT,
                        f'{connector.get("id")}: ступенька {length} px',
                    )


class LabelPlacementTest(unittest.TestCase):
    """Подпись связи должна стоять там, где её поставил аналитик."""

    def test_analyst_position_is_carried_over(self):
        from app.models.process import ProcessEdge

        # draw.io хранит долю вдоль связи в labelX: -1 — начало, +1 — конец.
        self.assertEqual(label_position(ProcessEdge(id='e', labelX=-1.0), None), 5)
        self.assertEqual(label_position(ProcessEdge(id='e', labelX=0.0), None), 50)
        self.assertEqual(label_position(ProcessEdge(id='e', labelX=1.0), None), 95)

    def test_gateway_branch_defaults_to_the_gateway_end(self):
        from app.models.process import ProcessEdge

        gateway = ProcessNode(id='g', name='', type='exclusiveGateway',
                              geometry=Geometry(x=0, y=0, width=60, height=60))
        # У шлюза от одной точки расходится несколько линий; их середины
        # попадают в гущу схемы, и «Ha» с «Yo'q» ложатся поверх фигур.
        self.assertEqual(label_position(ProcessEdge(id='e'), gateway), 20)

    def test_plain_connector_keeps_the_middle(self):
        from app.models.process import ProcessEdge

        task = ProcessNode(id='t', name='', type='userTask',
                           geometry=Geometry(x=0, y=0, width=180, height=80))
        self.assertEqual(label_position(ProcessEdge(id='e'), task), 50)

    def test_absolute_label_coordinate_is_ignored(self):
        from app.models.process import ProcessEdge

        # За пределами [-1, 1] это уже не доля, а абсолютная координата.
        self.assertEqual(label_position(ProcessEdge(id='e', labelX=340.0), None), 50)


class DrawioFramesTest(unittest.TestCase):
    """Фигуры на карте PIX стоят и имеют размер ровно как в draw.io.

    Импорт раздвигает шаг под подпись холста и отодвигает цилиндр; карта PIX
    берёт рамки draw.io (``drawioGeometry``), а подпись вмещает кеглем draw.io.
    """

    LONG = ' '.join(['Hujjatlarni tekshirish va tasdiqlash'] * 3)

    def setUp(self):
        drawio = DRAWIO.replace('Проверка документов 5 мин', self.LONG).replace(
            'value="Оформление 10 мин" style="rounded=1;"',
            'value="Оформление 10 мин" style="rounded=1;fontSize=10;"')
        self.process = parse_drawio_xml(drawio, 'frames.drawio')
        self.boxes = _boxes(_map_root(self.process))

    def _box(self, label_start):
        return next(i for i in self.boxes.values() if i['label'].startswith(label_start))

    def test_import_grew_the_step_but_pix_gets_the_drawio_frame(self):
        step = next(n for n in self.process.nodes if n.id == 't1')
        self.assertIsNotNone(step.drawioGeometry, 'импорт шаг не раздвигал — проверка не показательна')
        self.assertEqual(self._box('Hujjatlarni')['box'], (220.0, 105.0, 180.0, 80.0))

    def test_step_label_keeps_the_drawio_font_size(self):
        root = _map_root(self.process)
        sizes = {n.get('label'): n.get('font_size') for n in root.iter('node') if n.get('type') == 'task'}
        self.assertEqual(sizes['Оформление'], '10')
        self.assertEqual(sizes[next(k for k in sizes if k.startswith('Hujjatlarni'))], '12')

    def test_store_stays_where_the_analyst_put_it(self):
        store = next(n for n in self.process.nodes if n.id == 'db')
        frame = store.drawioGeometry or store.geometry
        self.assertEqual(self._box('IABS')['box'][:2], (float(frame.x), float(frame.y)))


TIMER_ON_LANE_BORDER = """<mxfile host="test"><diagram id="d" name="Map"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="up" value="Rahbar" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="900" height="160" as="geometry"/></mxCell>
  <mxCell id="down" value="Kotib" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
    <mxGeometry x="0" y="160" width="900" height="160" as="geometry"/></mxCell>
  <mxCell id="s42" value="42. Qarorni kiritish" style="rounded=1;" vertex="1" parent="up">
    <mxGeometry x="300" y="41" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="t" value="960 min" style="shape=mxgraph.bpmn.event;perimeter=ellipsePerimeter;aspect=fixed;outline=catching;symbol=timer;labelPosition=left;" vertex="1" parent="up">
    <mxGeometry x="340" y="141" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="s43" value="43. Tekshirish" style="rounded=1;" vertex="1" parent="down">
    <mxGeometry x="300" y="40" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="e1" style="edgeStyle=orthogonalEdgeStyle;exitX=0.5;exitY=1;exitDx=0;exitDy=0;entryX=0.5;entryY=0;entryDx=0;entryDy=0;entryPerimeter=0;" edge="1" parent="up" source="s42" target="t">
    <mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="e2" style="edgeStyle=orthogonalEdgeStyle;exitX=0.5;exitY=1;exitDx=0;exitDy=0;exitPerimeter=0;entryX=0.5;entryY=0;entryDx=0;entryDy=0;" edge="1" parent="up" source="t" target="s43">
    <mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>"""


class TimerOnLaneBorderTest(unittest.TestCase):
    """Таймер на черте между шагами соседних дорожек (1.54, шаги 42–43).

    В draw.io линии «шаг → таймер → шаг» — две короткие прямые. Таймер
    растягивался под минимум шага в рамку 80 px и уезжал с оси, а в PIX его
    вталкивало в нижнюю дорожку поверх шага 43 — линия заворачивала.
    """

    def setUp(self):
        self.process = parse_drawio_xml(TIMER_ON_LANE_BORDER, 'border.drawio')
        self.root = _map_root(self.process)

    def test_timer_keeps_its_drawio_frame(self):
        timer = next(n for n in self.process.nodes if n.id == 't')
        frame = timer.drawioGeometry or timer.geometry
        self.assertEqual((frame.x, frame.y, frame.width, frame.height), (340, 141, 40, 40))

    def test_lines_stay_short_and_straight(self):
        from app.services.pmm_exporter import _pix_id
        for edge_id in ('e1', 'e2'):
            connector = next(c for c in self.root.findall('connector') if c.get('id') == _pix_id(edge_id))
            points = _waypoints(connector)
            self.assertEqual(len(points), 2, points)
            self.assertEqual(points[0][0], points[1][0])
            self.assertLess(abs(points[1][1] - points[0][1]), 30, points)

    def test_timer_is_not_pushed_onto_the_next_step(self):
        boxes = _boxes(self.root)
        timer = next(i['box'] for i in boxes.values() if _shown(i['label']) == '960 min')
        step = next(i['box'] for i in boxes.values() if i['label'].startswith('43.'))
        self.assertLessEqual(timer[1] + timer[3], step[1])

    def test_timer_lies_in_the_lower_lane_so_pix_draws_it_on_top(self):
        # Студия кладёт дорожки слоями по порядку в файле (10, 20, …), детей —
        # на слой своей дорожки плюс один, а узлы уровня карты — на слой 1,
        # под все дорожки: там таймер выходил бледным (PIX банка, 2026-09-23).
        # Выступающего за край ребёнка студия не двигает, поэтому таймер
        # вкладывается в последнюю из задетых дорожек, как нарисован.
        lanes = [n for n in self.root.findall('node') if n.get('type') == 'horizontalRoad']
        timer_lane = next(lane for lane in lanes
                          if any(_shown(c.get('label')) == '960 min' for c in lane.findall('node')))
        self.assertIs(timer_lane, lanes[-1])
        timer = next(c for c in timer_lane.findall('node') if _shown(c.get('label')) == '960 min')
        self.assertEqual((timer.get('x'), timer.get('y')), ('340', '-19'))


class CylinderBesideTimerLabelTest(unittest.TestCase):
    """Имя цилиндра не ложится на время таймера (1.54: «EHA» на «960 min»)."""

    def test_cylinder_name_moves_off_the_timer_label(self):
        drawio = TIMER_ON_LANE_BORDER.replace(
            '</root>',
            '<mxCell id="db" value="EHA" style="shape=datastore;html=1;labelPosition=center;'
            'verticalLabelPosition=bottom;align=center;verticalAlign=top;" vertex="1" parent="down">'
            '<mxGeometry x="261" y="16" width="30" height="20" as="geometry"/></mxCell>'
            '<mxCell id="e3" style="edgeStyle=orthogonalEdgeStyle;dashed=1;endArrow=none;" edge="1" parent="down"'
            ' source="s43" target="db"><mxGeometry relative="1" as="geometry"/></mxCell></root>')
        root = _map_root(parse_drawio_xml(drawio, 'eha.drawio'))
        cylinder = next(n for n in root.iter('node') if n.get('label') == 'EHA')
        self.assertEqual(cylinder.get('labelPlacement'), 'Bottom')


class TextFrameOverLaneTest(unittest.TestCase):
    """Текстовая рамка поверх дорожки не должна уходить под её заливку."""

    def test_frame_is_nested_in_the_lane_it_lies_on(self):
        drawio = TIMER_ON_LANE_BORDER.replace(
            '</root>',
            '<mxCell id="note" value="Izoh" style="text;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="520" y="200" width="220" height="60" as="geometry"/></mxCell></root>')
        root = _map_root(parse_drawio_xml(drawio, 'note.drawio'))
        lane = next(n for n in root.findall('node') if n.get('label', '').startswith('Kotib'))
        frame = next(c for c in lane.findall('node') if c.get('label') == 'Izoh')
        self.assertEqual((frame.get('type'), frame.get('x'), frame.get('y')), ('emptyPool', '520', '40'))


class TextFrameBorderTest(unittest.TestCase):
    """Рамка у текста в студии — только там, где она есть в draw.io.

    Текст draw.io (стиль ``text``) рисуется без обводки и заливки, и длинная
    сноска свободно выходит за свою рамку: у «Sport maydoni» (1.54) 337
    символов в рамке 370 × 30. «Пустой пул» студии обводку рисует, и текст
    вылезал из неё. ``border_color``/``fill_color="transparent"`` студия
    принимает (PIX банка, 2026-09-24, проба P17: обводка и заливка прозрачны).
    """

    def _frame(self, style):
        drawio = TIMER_ON_LANE_BORDER.replace(
            '</root>',
            f'<mxCell id="note" value="Izoh" style="{style}" vertex="1" parent="1">'
            '<mxGeometry x="520" y="200" width="220" height="60" as="geometry"/></mxCell></root>')
        root = _map_root(parse_drawio_xml(drawio, 'note.drawio'))
        return next(n for n in root.iter('node') if n.get('label') == 'Izoh')

    def test_drawio_text_gets_no_border_and_no_fill(self):
        frame = self._frame('text;html=1;whiteSpace=wrap;strokeColor=none;fillColor=none;')
        self.assertEqual((frame.get('border_color'), frame.get('fill_color')), ('transparent', 'transparent'))

    def test_text_style_alone_means_no_border(self):
        # В теме draw.io стиль ``text`` сам по себе без обводки и заливки.
        frame = self._frame('text;html=1;')
        self.assertEqual((frame.get('border_color'), frame.get('fill_color')), ('transparent', 'transparent'))

    def test_drawn_border_is_kept(self):
        frame = self._frame('text;html=1;strokeColor=#000000;')
        self.assertIsNone(frame.get('border_color'))

    def test_default_drawio_font_size_is_written(self):
        # Без кегля студия печатает 14, draw.io — 12: текст раздувался.
        self.assertEqual(self._frame('text;html=1;').get('font_size'), '12')
        self.assertEqual(self._frame('text;html=1;fontSize=11;').get('font_size'), '11')


class LaneTitleFitsItsBandTest(unittest.TestCase):
    """Название дорожки видно целиком — кегль подбирается под полосу студии.

    Полоса с названием в PIX 30 px, это класс её отрисовщика, в файле ширины
    нет. Кегль дорожки студия берёт из ``font_size`` (PIX банка, 2026-09-25,
    проба P18: 14, 11, 10 и 9 легли в стиль), строки идут с интервалом 0,9
    кегля, лишнее обрезается. При 14 длинное название не влезало, и его
    приходилось резать «…» — на 10 картах так резалось 70 названий из 348.
    """

    LONG = "Ichki xavfsizlik departamenti direktori o'rinbosari (bank tizimlarini qo'llab-quvvatlash)"

    def _lane(self, name, height=160, style='swimlane;horizontal=0;startSize=80;'):
        drawio = f'''<mxfile host="test"><diagram id="d" name="Map"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="lane" value="{name}" style="{style}" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="900" height="{height}" as="geometry"/></mxCell>
  <mxCell id="t" value="Qadam" style="rounded=1;" vertex="1" parent="lane">
    <mxGeometry x="200" y="{height / 2 - 40}" width="120" height="80" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>'''
        root = _map_root(parse_drawio_xml(drawio, 'lane.drawio'))
        return next(n for n in root.iter('node') if n.get('type') == 'horizontalRoad')

    def test_long_name_is_shown_whole_in_a_smaller_font(self):
        from app.services.layout import wrapped_line_count
        lane = self._lane(self.LONG)
        self.assertEqual(lane.get('label'), self.LONG)
        size = int(lane.get('font_size'))
        self.assertLess(size, 12)
        self.assertLessEqual(wrapped_line_count(self.LONG, 152, size) * 0.9 * size, 30)

    def test_short_name_keeps_the_drawio_size(self):
        lane = self._lane('Kotib')
        self.assertEqual((lane.get('label'), lane.get('font_size')), ('Kotib', '12'))
        lane = self._lane('Kotib', style='swimlane;horizontal=0;fontSize=10;')
        self.assertEqual(lane.get('font_size'), '10')

    def test_name_too_long_even_at_the_smallest_size_is_cut(self):
        name = ' '.join([self.LONG] * 3)
        lane = self._lane(name, height=80)
        self.assertEqual(lane.get('font_size'), '8')
        self.assertTrue(lane.get('label').endswith('…'), lane.get('label'))


class StudioExportStaysValidTest(unittest.TestCase):
    """Эталон студии обязан проходить наши же правила геометрии."""

    def test_our_threshold_is_close_to_the_studios_own(self):
        package = zipfile.ZipFile(os.path.join(FIXTURES, 'sap.pmm'))
        root = ET.fromstring(package.read('pm/maps/sap.xml'))
        shortest = None
        for connector in root.findall('connector'):
            route = _waypoints(connector)
            for a, b in zip(route, route[1:]):
                length = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
                # Совпавшие точки студия пишет и сама (у неё они расходятся на
                # 5e-05 px): это не отрезок, а дубль, и порога он не касается.
                if length < 0.5:
                    continue
                shortest = length if shortest is None else min(shortest, length)
        # Отсюда и взят порог MIN_SEGMENT: у студии самый короткий настоящий
        # отрезок — 6 px, мы держим 8. Если эталон окажется заметно мельче,
        # порог придётся пересмотреть: он не должен перекладывать ломаную,
        # которую сама студия считает нормальной.
        self.assertIsNotNone(shortest)
        self.assertGreaterEqual(shortest, 6.0)
        self.assertLessEqual(MIN_SEGMENT - shortest, 4.0)


TIMER_ON_A_VERTICAL_FLOW = '''<mxfile host="test"><diagram id="d" name="Map"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="900" height="500" as="geometry"/></mxCell>
  <mxCell id="a" value="Qadam A" style="rounded=1;" vertex="1" parent="lane">
    <mxGeometry x="300" y="30" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="t" value="120 min" style="shape=mxgraph.bpmn.event;html=1;verticalLabelPosition=bottom;labelBackgroundColor=#ffffff;verticalAlign=top;align=center;perimeter=ellipsePerimeter;outlineConnect=0;aspect=fixed;outline=catching;symbol=timer;" vertex="1" parent="lane">
    <mxGeometry x="340" y="200" width="40" height="40" as="geometry"/></mxCell>
  <mxCell id="b" value="Qadam B" style="rounded=1;" vertex="1" parent="lane">
    <mxGeometry x="300" y="360" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="e1" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane" source="a" target="t"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="e2" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane" source="t" target="b"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>'''


class TimerLabelOffItsOwnLineTest(unittest.TestCase):
    """Время таймера не лежит на его же линии.

    В draw.io подпись часов стоит под кругом, на уходящей вниз линии, и
    читается благодаря белой подложке (``labelBackgroundColor``). У подписи в
    студии подложки нет (фон прозрачный), и линия шла прямо сквозь «120 min»
    — так у 193 таймеров из 233 на 10 картах.
    """

    def _timer(self, drawio):
        root = _map_root(parse_drawio_xml(drawio, 'timer.drawio'))
        return next(n for n in root.iter('node') if n.get('type') == 'intermediate_event_catch_timer')

    def test_label_moves_aside_when_the_line_leaves_downwards(self):
        self.assertIn(self._timer(TIMER_ON_A_VERTICAL_FLOW).get('labelPlacement'), ('Left', 'Right'))

    def test_side_label_is_padded_to_sit_next_to_the_circle(self):
        # Сбоку студия центрирует подпись в коробке 100 px, центр в 54 px от
        # круга: «60 min» стояло в 32 px. Неразрывные пробелы с дальней
        # стороны сдвигают видимый текст к кругу (пробел — 3,5 px при кегле
        # 14; PIX банка, 2026-09-25, пробы P19–P21).
        from app.services.layout import text_width
        timer = self._timer(TIMER_ON_A_VERTICAL_FLOW)
        label, side = timer.get('label'), timer.get('labelPlacement')
        visible = label.strip(' ')
        self.assertEqual(visible.replace(' ', ' '), '120 min')
        pad = len(label) - len(visible)
        self.assertTrue(label.startswith(' ') if side == 'Left' else label.endswith(' '))
        full = text_width('120 min', 14) + pad * 3.5
        gap = 54 - full / 2
        self.assertLessEqual(full, 100)
        self.assertTrue(4 <= gap <= 9, gap)

    def test_label_stays_below_when_the_line_runs_sideways(self):
        # Линия идёт слева направо — снизу подписи ничего не мешает, и она
        # стоит там же, где в draw.io.
        horizontal = TIMER_ON_A_VERTICAL_FLOW.replace(
            '<mxGeometry x="300" y="360" width="120" height="80"', '<mxGeometry x="600" y="180" width="120" height="80"').replace(
            '<mxGeometry x="300" y="30" width="120" height="80"', '<mxGeometry x="100" y="180" width="120" height="80"')
        self.assertIsNone(self._timer(horizontal).get('labelPlacement'))


POOL_LINK_LABEL = '''<mxfile host="test"><diagram id="d" name="Map"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="pool" value="Mijoz" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="900" height="100" as="geometry"/></mxCell>
  <mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
    <mxGeometry x="0" y="160" width="900" height="240" as="geometry"/></mxCell>
  <mxCell id="s" value="Hujjatni tayyorlash" style="rounded=1;" vertex="1" parent="lane">
    <mxGeometry x="300" y="90" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="m" value="Tayyorlangan hujjatlar mijozga beriladi" style="edgeStyle=orthogonalEdgeStyle;dashed=1;endArrow=block;exitX=0.5;exitY=0;entryX=0.4;entryY=1;" edge="1" parent="1" source="s" target="pool">
    <mxGeometry x="-0.6" relative="1" as="geometry"><mxPoint as="offset" x="0" y="-90"/></mxGeometry></mxCell>
</root></mxGraphModel></diagram></mxfile>'''


def _point_at_percent(points, percent):
    total = sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(points, points[1:]))
    goal, walked = total * percent / 100.0, 0.0
    for a, b in zip(points, points[1:]):
        step = abs(b[0] - a[0]) + abs(b[1] - a[1])
        if step and walked + step >= goal:
            t = (goal - walked) / step
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        walked += step
    return points[-1]


class EdgeLabelWhereDrawnTest(unittest.TestCase):
    """Подпись связи стоит там, где её поставил аналитик, с учётом смещения.

    Подписи пунктиров к колонке-участнику аналитик уводит в промежуток между
    колонкой и дорожками (смещение ``offset`` у подписи draw.io). Выгрузка
    брала только долю длины линии, и в PIX текст ложился внутрь дорожки,
    поверх шагов.
    """

    def test_pool_link_label_sits_in_the_gap_between_the_column_and_the_lanes(self):
        root = _map_root(parse_drawio_xml(POOL_LINK_LABEL, 'pool.drawio'))
        connector = next(c for c in root.findall('connector') if c.get('Text'))
        x, y = _point_at_percent(_waypoints(connector), float(connector.findtext('labelPosition')))
        self.assertTrue(100 <= y <= 160, (x, y))

    def test_detached_label_stays_where_it_was_dragged(self):
        # Подпись, оттащенную с линии, draw.io хранит отдельной ячейкой с
        # абсолютными координатами; разбор отдаёт её ближайшей связи.
        drawio = POOL_LINK_LABEL.replace(
            'value="Tayyorlangan hujjatlar mijozga beriladi" ', '').replace(
            '<mxGeometry x="-0.6" relative="1" as="geometry"><mxPoint as="offset" x="0" y="-90"/></mxGeometry>',
            '<mxGeometry relative="1" as="geometry"/>').replace(
            '</root>',
            '<mxCell id="lbl" value="XA" style="edgeLabel;html=1;" vertex="1" connectable="0" parent="1">'
            '<mxGeometry x="345" y="120" width="30" height="20" as="geometry"/></mxCell></root>')
        root = _map_root(parse_drawio_xml(drawio, 'pool.drawio'))
        connector = next(c for c in root.findall('connector') if c.get('Text'))
        x, y = _point_at_percent(_waypoints(connector), float(connector.findtext('labelPosition')))
        self.assertTrue(100 <= y <= 160, (connector.get('Text'), x, y))


class MapLabelTest(unittest.TestCase):
    """Подпись на карте студии должна повторять исходную карту draw.io."""

    def test_timer_first_seen_as_an_end_event_keeps_its_time_on_the_map(self):
        # В draw.io к часам между шагами привязана только входящая линия,
        # исходящая лишь начинается у круга. Импорт сначала видел конечное
        # событие («Завершение»), а притянув линию, делал из него таймер с
        # именем «Ожидание», которое на карту не идёт: время было в свойствах,
        # а на карте — голые часы (1.53, 1.3, AVGUST-1.28 — 20 таймеров).
        drawio = '''<mxfile host="test"><diagram id="d" name="Map"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="900" height="300" as="geometry"/></mxCell>
  <mxCell id="a" value="Qadam A" style="rounded=1;" vertex="1" parent="lane">
    <mxGeometry x="100" y="30" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="t" value="480 min" style="shape=mxgraph.bpmn.event;html=1;verticalLabelPosition=middle;verticalAlign=middle;align=right;perimeter=ellipsePerimeter;outlineConnect=0;aspect=fixed;outline=catching;symbol=timer;labelPosition=left;" vertex="1" parent="lane">
    <mxGeometry x="400" y="45" width="50" height="50" as="geometry"/></mxCell>
  <mxCell id="b" value="Qadam B" style="rounded=1;" vertex="1" parent="lane">
    <mxGeometry x="600" y="30" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="e1" edge="1" parent="lane" source="a" target="t"><mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="e2" edge="1" parent="lane" target="b"><mxGeometry relative="1" as="geometry"><mxPoint x="450" y="70" as="sourcePoint"/></mxGeometry></mxCell>
</root></mxGraphModel></diagram></mxfile>'''
        process = parse_drawio_xml(drawio, 'timer.drawio')
        timer = next(n for n in process.nodes if n.id == 't')
        self.assertEqual(timer.type, 'intermediateTimerEvent')
        self.assertEqual(map_label(timer), '480 min')
        root = _map_root(process)
        self.assertIn('480 min', [_shown(n.get('label')) for n in root.iter('node')])

    def test_generated_gateway_name_does_not_reach_the_map(self):
        # В draw.io безымянный шлюз нарисован пустым ромбом: вопрос стоит на
        # ветках. Подставленное «Условие» — надпись, которой в эталоне нет.
        gateway = ProcessNode(id='g', name='Условие', type='exclusiveGateway',
                              geometry=Geometry(x=0, y=0, width=60, height=60))
        self.assertEqual(map_label(gateway), '')

    def test_real_gateway_question_is_kept(self):
        gateway = ProcessNode(id='g', name="Hujjatlar to'liqmi?", type='exclusiveGateway',
                              geometry=Geometry(x=0, y=0, width=60, height=60))
        self.assertEqual(map_label(gateway), "Hujjatlar to'liqmi?")

    def test_generated_task_name_is_kept(self):
        # Пустой прямоугольник хуже условного имени: по нему шаг не найти ни в
        # регламенте, ни в отчёте о качестве импорта.
        task = ProcessNode(id='t', name='Операция STEP-07', type='userTask',
                           geometry=Geometry(x=0, y=0, width=160, height=80))
        self.assertEqual(map_label(task), 'Операция STEP-07')

    def test_waiting_event_carries_its_minutes(self):
        # В draw.io подпись набрана в две строки: «Kutish vaqti» и «15 min».
        # Импорт разбирает вторую строку в минуты и убирает из имени — на карте
        # студии оставалось голое «Kutish vaqti» без единой цифры.
        wait = ProcessNode(id='w', name='Kutish vaqti', type='intermediateTimerEvent',
                           slaMinutes=15, geometry=Geometry(x=0, y=0, width=48, height=48))
        self.assertEqual(map_label(wait), 'Kutish vaqti 15 мин')

    def test_trailing_colon_does_not_survive(self):
        wait = ProcessNode(id='w', name='Kutish vaqti :', type='intermediateTimerEvent',
                           slaMinutes=30, geometry=Geometry(x=0, y=0, width=48, height=48))
        self.assertEqual(map_label(wait), 'Kutish vaqti 30 мин')

    def test_waiting_event_that_already_shows_time_is_left_alone(self):
        wait = ProcessNode(id='w', name='2-3 kun', type='intermediateTimerEvent',
                           slaMinutes=30, geometry=Geometry(x=0, y=0, width=48, height=48))
        self.assertEqual(map_label(wait), '2-3 kun')


class DurationBadgePlacementTest(unittest.TestCase):
    """Значок длительности обязан быть виден целиком."""

    def test_badge_sits_entirely_below_the_step(self):
        # Раньше кружок сидел на нижней грани, и верхняя половина уходила под
        # заливку шага: в студии от значка была видна одна нижняя дуга.
        process = parse_drawio_xml(DRAWIO, 'geometry.drawio')
        boxes = _boxes(_map_root(process))
        badges = [i for i in boxes.values()
                  if i['type'] == 'boundary_non_interrupting_event_timer']
        steps = [i for i in boxes.values() if i['type'] == 'task']
        self.assertTrue(badges)
        for badge in badges:
            bx, by, bw, bh = badge['box']
            for step in steps:
                sx, sy, sw, sh = step['box']
                overlap_x = min(bx + bw, sx + sw) - max(bx, sx)
                overlap_y = min(by + bh, sy + sh) - max(by, sy)
                self.assertTrue(
                    overlap_x <= 0 or overlap_y <= 0,
                    f'значок «{badge["label"]}» заходит на шаг «{step["label"]}»')



class ClientTouchpointTest(unittest.TestCase):
    """Пунктиры к полосе клиента не должны сходиться в одну точку."""

    MAP = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="client" value="Mijoz" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="0" width="1400" height="120" as="geometry" />
        </mxCell>
        <mxCell id="lane" value="Фронт-офис" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
          <mxGeometry x="0" y="120" width="1400" height="300" as="geometry" />
        </mxCell>
        <mxCell id="t1" value="Приём заявки" style="rounded=1;" vertex="1" parent="lane">
          <mxGeometry x="200" y="80" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="t2" value="Выдача справки" style="rounded=1;" vertex="1" parent="lane">
          <mxGeometry x="800" y="80" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="m1" style="dashed=1;" edge="1" source="t1" target="client" parent="1" />
        <mxCell id="m2" style="dashed=1;" edge="1" source="t2" target="client" parent="1" />
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""

    def setUp(self):
        self.root = _map_root(parse_drawio_xml(self.MAP, 'client.drawio'))
        self.client = next(n for n in self.root.iter('node') if n.get('label') == 'Mijoz')
        self.links = [c for c in self.root.findall('connector')
                      if self.client.get('id') in (c.get('sourceNodeId'), c.get('targetNodeId'))]

    def _ends(self):
        return [(float(w[-1].get('x')), float(w[-1].get('y')))
                for w in (c.findall('waypoint') for c in self.links)]

    def test_lines_go_straight_into_the_client_row(self):
        # Конверты-посредники на карте аналитика не нарисованы; пунктир к
        # полосе PIX принимает прямо (PIX банка, 2026-09-22).
        self.assertEqual(len(self.links), 2)
        envelopes = [n for n in self.root.iter('node')
                     if n.get('type') == 'intermediate_event_catch_message']
        self.assertEqual(envelopes, [])

    def test_each_line_gets_its_own_point_of_the_row(self):
        # Без номера точки студия цепляла линию за середину полосы, и все
        # пунктиры к клиенту сходились в одну точку, сливаясь в жирную линию.
        self.assertEqual(len({c.get('targetPoint') for c in self.links}), 2)
        x, y = float(self.client.get('x')), float(self.client.get('y'))
        w, h = float(self.client.get('width')), float(self.client.get('height'))
        handles = {(x + w * f, y + h) for f in (0.25, 0.5, 0.75)}
        handles |= {(x, y + h * f) for f in (0.25, 0.5, 0.75)} | {(x + w, y + h * f) for f in (0.25, 0.5, 0.75)}
        for end in self._ends():
            self.assertIn(end, handles)

    def test_approaches_do_not_run_on_top_of_each_other(self):
        first, second = ([(float(p.get('x')), float(p.get('y'))) for p in c.findall('waypoint')]
                         for c in self.links)
        for a, b in zip(first, first[1:]):
            for c, d in zip(second, second[1:]):
                same_row = a[1] == b[1] == c[1] == d[1]
                same_column = a[0] == b[0] == c[0] == d[0]
                if same_row:
                    self.assertLessEqual(min(max(a[0], b[0]), max(c[0], d[0]))
                                         - max(min(a[0], b[0]), min(c[0], d[0])), 0)
                if same_column:
                    self.assertLessEqual(min(max(a[1], b[1]), max(c[1], d[1]))
                                         - max(min(a[1], b[1]), min(c[1], d[1])), 0)


class GatewayLabelPlacementTest(unittest.TestCase):
    """Вопрос шлюза стоит НАД ромбом, а не сбоку."""

    def test_label_goes_above_the_diamond(self):
        # Слева от ромба на плотной карте стоит предыдущий шаг, и вопрос
        # ложился прямо на его текст. Сверху свободно всегда: поток идёт по
        # горизонтали.
        process = parse_drawio_xml(DRAWIO, 'geometry.drawio')
        gateway = next(n for n in _map_root(process).iter('node')
                       if n.get('type') == 'gateway_xor')
        self.assertEqual(gateway.get('labelPlacement'), 'Top')



class ShapeClassificationTest(unittest.TestCase):
    """Фигуры, из-за которых шаги собирались в кучу."""

    MAP = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="t" value="Проверка документов" style="rounded=1;" vertex="1" parent="1">
          <mxGeometry x="200" y="200" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="note" value="Balansdan tashqari h/r ochish" style="shape=callout;whiteSpace=wrap;html=1;perimeter=calloutPerimeter;" vertex="1" parent="1">
          <mxGeometry x="200" y="60" width="200" height="80" as="geometry" />
        </mxCell>
        <mxCell id="icon" value="" style="strokeColor=none;fillColor=#505050;shape=mxgraph.office.devices.phone_traditional;" vertex="1" parent="1">
          <mxGeometry x="450" y="60" width="60" height="60" as="geometry" />
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""

    def setUp(self):
        self.process = parse_drawio_xml(self.MAP, 'shapes.drawio')
        self.by_id = {n.id: n for n in self.process.nodes}

    def test_callout_is_a_note_not_a_step(self):
        # Нераспознанная выноска становилась полноразмерной задачей и ложилась
        # поверх соседнего ряда — отсюда «куча» на карте студии.
        self.assertEqual(self.by_id['note'].type, 'textAnnotation')

    def test_office_clipart_is_not_a_step(self):
        # Иконка телефона из библиотеки Office превращалась в «Операцию
        # STEP-NN» с выдуманным временем.
        self.assertNotIn('icon', self.by_id)

    def test_no_phantom_steps_are_invented(self):
        names = [n.name for n in self.process.nodes]
        self.assertFalse([n for n in names if n.startswith('Операция STEP-')], names)


class MeasuredDurationTest(unittest.TestCase):
    """Время рисуется только там, где оно было в draw.io."""

    def _process(self, with_badge: bool):
        badge = ('<mxCell id="b" value="30 мин" style="shape=mxgraph.bpmn.timer_start;" '
                 'vertex="1" parent="1">'
                 '<mxGeometry x="360" y="268" width="24" height="24" as="geometry" />'
                 '</mxCell>') if with_badge else ''
        return parse_drawio_xml(
            ShapeClassificationTest.MAP.replace('</root>', badge + '</root>'), 'd.drawio')

    def test_step_without_a_clock_gets_no_badge(self):
        step = next(n for n in self._process(False).nodes if n.type in ('task', 'userTask'))
        self.assertGreater(step.slaMinutes or 0, 0, 'для SLA время всё же подставляется')
        self.assertFalse(step.slaMeasured)
        self.assertEqual(step_duration_text(step), '')

    def test_step_with_a_clock_keeps_it(self):
        step = next(n for n in self._process(True).nodes if n.type in ('task', 'userTask'))
        self.assertTrue(step.slaMeasured)
        self.assertEqual(step_duration_text(step), '30 мин')

    def test_map_shows_a_badge_only_for_a_measured_step(self):
        without = _map_root(self._process(False))
        with_clock = _map_root(self._process(True))
        badge = 'boundary_non_interrupting_event_timer'
        self.assertEqual([n for n in without.iter('node') if n.get('type') == badge], [])
        self.assertTrue([n for n in with_clock.iter('node') if n.get('type') == badge])


class TaskIconTest(unittest.TestCase):
    """У шага не должно быть значка в левом верхнем углу."""

    def test_every_action_is_a_flat_task(self):
        # Студия рисует у пользовательской задачи человечка, у сервисной —
        # шестерёнку, и рисует их поверх первых букв подписи: «1.Eksport…»
        # превращается в «👤Eksport…». В draw.io у шагов иконок нет.
        types = {n.get('type') for n in _map_root(
            parse_drawio_xml(DRAWIO, 'geometry.drawio')).iter('node')}
        self.assertIn('task', types)
        self.assertNotIn('userTask', types)
        self.assertNotIn('serviceTask', types)



class DocumentIconTest(unittest.TestCase):
    """Лист бумаги рядом с шагом — документ процесса, а не украшение."""

    MAP = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="t" value="Проверка документов" style="rounded=1;" vertex="1" parent="1">
          <mxGeometry x="200" y="200" width="180" height="80" as="geometry" />
        </mxCell>
        <mxCell id="doc" value="" style="strokeColor=none;fillColor=#505050;shape=mxgraph.office.concepts.documents;" vertex="1" parent="1">
          <mxGeometry x="260" y="100" width="28" height="34" as="geometry" />
        </mxCell>
        <mxCell id="phone" value="" style="strokeColor=none;fillColor=#505050;shape=mxgraph.office.devices.phone_traditional;" vertex="1" parent="1">
          <mxGeometry x="500" y="100" width="60" height="60" as="geometry" />
        </mxCell>
        <mxCell id="a1" style="dashed=1;" edge="1" source="doc" target="t" parent="1" />
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""

    def setUp(self):
        self.by_id = {n.id: n for n in parse_drawio_xml(self.MAP, 'doc.drawio').nodes}

    def test_document_survives(self):
        # Правило «вся библиотека Office — клипарт» съедало вместе с телефоном
        # и лист бумаги: на карте исчезал документ, который аналитик привязал
        # к шагу пунктиром.
        self.assertIn('doc', self.by_id)
        self.assertEqual(self.by_id['doc'].type, 'dataObject')

    def test_phone_is_still_dropped(self):
        self.assertNotIn('phone', self.by_id)

    def test_document_keeps_its_small_size(self):
        geo = self.by_id['doc'].geometry
        self.assertEqual((geo.width, geo.height), (28, 34))



class LabelSideTest(unittest.TestCase):
    """Подписи, которые студия печатает снаружи, не должны сталкиваться."""

    MAP = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="g1" value="Qo'shimcha hujjatlar lozimmi?" style="rhombus;" vertex="1" parent="1">
          <mxGeometry x="200" y="200" width="50" height="50" as="geometry" />
        </mxCell>
        <mxCell id="g2" value="Sanksiya mavjudmi?" style="rhombus;" vertex="1" parent="1">
          <mxGeometry x="280" y="200" width="50" height="50" as="geometry" />
        </mxCell>
        <mxCell id="f" edge="1" source="g1" target="g2" parent="1" />
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""

    def test_adjacent_gateways_get_opposite_sides(self):
        # Два соседних ромба с длинными вопросами клали подписи друг на друга:
        # места мы выбрать не можем, только сторону, и обе были «сверху».
        root = _map_root(parse_drawio_xml(self.MAP, 'labels.drawio'))
        sides = {
            n.get('label'): n.get('labelPlacement')
            for n in root.iter('node') if n.get('type') == 'gateway_xor'
        }
        self.assertEqual(len(sides), 2, sides)
        self.assertEqual(set(sides.values()), {'Top', 'Bottom'}, sides)

    def test_a_lone_gateway_keeps_the_label_above(self):
        # Сверху — как в draw.io; вниз уводим только при столкновении.
        lone = self.MAP.replace(
            '<mxCell id="g2" value="Sanksiya mavjudmi?" style="rhombus;" vertex="1" parent="1">\n'
            '          <mxGeometry x="280" y="200" width="50" height="50" as="geometry" />\n'
            '        </mxCell>', '').replace(
            '<mxCell id="f" edge="1" source="g1" target="g2" parent="1" />', '')
        root = _map_root(parse_drawio_xml(lone, 'lone.drawio'))
        gateway = next(n for n in root.iter('node') if n.get('type') == 'gateway_xor')
        self.assertEqual(gateway.get('labelPlacement'), 'Top')


class EventLabelSideTest(unittest.TestCase):
    """Время у часов ожидания печатается с той стороны, что и в draw.io.

    Под кругом его печатать нельзя: связь входит в часы сверху и выходит
    снизу, и время оказывается перечёркнутым линией. Аналитик уводит его вбок
    сам — ``labelPosition=left`` стоит у 103 таймеров из 127 на картах банка,
    ``right`` — ещё у десяти.
    """

    TIMER = ('shape=mxgraph.bpmn.event;html=1;verticalAlign=middle;align=right;'
             'perimeter=ellipsePerimeter;outlineConnect=0;aspect=fixed;'
             'outline=standard;symbol=timer;')

    MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <mxCell id="lane" value="Devonxona xizmati" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="1400" height="500" as="geometry" />
    </mxCell>
    <mxCell id="step" value="Xatni tasdiqlash" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="100" y="100" width="160" height="60" as="geometry" />
    </mxCell>
    <mxCell id="t_left" value="10 min" style="{t}verticalLabelPosition=middle;labelPosition=left;" vertex="1" parent="lane">
      <mxGeometry x="400" y="105" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="t_right" value="20 min" style="{t}verticalLabelPosition=middle;labelPosition=right;" vertex="1" parent="lane">
      <mxGeometry x="700" y="105" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="t_below" value="30 min" style="{t}verticalLabelPosition=bottom;" vertex="1" parent="lane">
      <mxGeometry x="1000" y="105" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="done" value="Xat yuboriladi" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="1150" y="100" width="160" height="60" as="geometry" />
    </mxCell>

    <mxCell id="f1" style="html=1;" edge="1" parent="1" source="step" target="t_left">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f2" style="html=1;" edge="1" parent="1" source="t_left" target="t_right">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f3" style="html=1;" edge="1" parent="1" source="t_right" target="t_below">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f4" style="html=1;" edge="1" parent="1" source="t_below" target="done">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""

    def _sides(self, source):
        root = _map_root(parse_drawio_xml(source, 'timers.drawio'))
        return {
            _shown(n.get('label')): n.get('labelPlacement')
            for n in root.iter('node')
            if n.get('type') == 'intermediate_event_catch_timer'
        }

    def test_the_analyst_chose_the_side(self):
        sides = self._sides(self.MAP.format(t=self.TIMER))
        self.assertEqual(sides.get('10 min'), 'Left', sides)
        self.assertEqual(sides.get('20 min'), 'Right', sides)

    def test_a_label_left_below_stays_below(self):
        """Снизу студия печатает подпись и без атрибута — не спорим с ней."""
        sides = self._sides(self.MAP.format(t=self.TIMER))
        self.assertIsNone(sides.get('30 min'), sides)

    def test_a_label_raised_above_the_circle_goes_up(self):
        raised = self.MAP.format(t=self.TIMER).replace(
            'verticalLabelPosition=bottom;', 'verticalLabelPosition=top;')
        self.assertEqual(self._sides(raised).get('30 min'), 'Top')


class DataLinkMarkerTest(unittest.TestCase):
    """Связь с базой данных приходит в PIX ассоциацией, а не переходом."""

    def test_association_uses_the_marker_pix_recognises(self):
        # Вид связи PIX выводит из сочетания «стиль + маркеры». Пунктир с
        # `line` в конце он не узнавал и рисовал сплошной стрелкой перехода;
        # ассоциация у него — dotted + line + arrowLine.
        root = _map_root(parse_drawio_xml(DRAWIO, 'geometry.drawio'))
        dotted = [c for c in root.findall('connector') if c.get('lineStyle') == 'dotted']
        self.assertTrue(dotted, 'в фикстуре нет связи с артефактом')
        for connector in dotted:
            self.assertEqual(connector.findtext('MarkerEnd'), 'arrowLine')


class UturnTest(unittest.TestCase):
    """Линия не должна уходить от цели и возвращаться."""

    def test_route_that_turns_back_is_rejected(self):
        self.assertTrue(er._doubles_back(
            [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (-60.0, 50.0)]))

    def test_the_rule_is_deliberately_coarse(self):
        """Обход «вверх, вбок, вниз» правило тоже считает разворотом.

        Отделять одно от другого пробовали: развороты выросли с 19 до 27, а
        линий сквозь фигуры стало больше — обход, за который никто не
        штрафует, трассировщик начинает выбирать и там, где он не нужен.
        Пересечение стоит дороже разворота, поэтому нужный обход правило не
        отменяет.
        """
        self.assertTrue(er._doubles_back(
            [(0.0, 0.0), (0.0, -80.0), (300.0, -80.0), (300.0, 0.0)]))

    def test_short_wobble_is_tolerated(self):
        self.assertFalse(er._doubles_back(
            [(0.0, 0.0), (20.0, 0.0), (20.0, 40.0), (0.0, 40.0), (0.0, 300.0)]))



if __name__ == '__main__':
    unittest.main()
