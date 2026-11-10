"""Что импортёр восстанавливает с карты, а что обязан отбросить.

Фикстура повторяет приёмы, которыми аналитики банка пользуются в draw.io и
которые импорт раньше понимал неверно: подпись фигуры набрана отдельным
текстовым блоком рядом, заголовок дорожки — повёрнутым текстом внутри неё,
для наглядности рядом поставлена иконка из библиотеки, а время шага набрано
с разделителем тысяч и разрезано редактором посреди числа.
"""
import io
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import ARTIFACT_NODE_TYPES, NODE_TYPE_LABELS, Geometry, ProcessNode
from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.exporters import generate_event_log_csv, generate_regulation_csv
from app.services.pmm_exporter import bpmn_notation, generate_pmm_zip, pix_element, pix_type
from app.services.drawio_parser import (
    clean_label,
    unsupported_shape,
    duration_badge_minutes,
    duration_minutes,
    is_duration_badge_name,
    parse_bpmn_xml,
    parse_drawio_xml,
)

#: Пространства имён BPMN 2.0 — для разбора выгрузки в проверках ниже.
BPMN_NS = 'http://www.omg.org/spec/BPMN/20100524/MODEL'
DI_NS = 'http://www.omg.org/spec/BPMN/20100524/DI'
DC_NS = 'http://www.omg.org/spec/DD/20100524/DC'

#: Карта, где половина подписей живёт отдельно от своих фигур.
LOOSE_LABELS_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <!-- Дорожка-баннер: заголовок схемы, шагов внутри нет. -->
    <mxCell id="banner" value="" style="swimlane;html=1;startSize=20;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="1400" height="60" as="geometry" />
    </mxCell>
    <mxCell id="banner_text" value="Kredit shartnomasi (As-is)" style="text;html=1;strokeColor=none;fillColor=none;fontSize=26;" vertex="1" parent="banner">
      <mxGeometry x="400" y="15" width="500" height="30" as="geometry" />
    </mxCell>

    <!-- Настоящая дорожка: подписи нет, вместо неё повёрнутый текст внутри. -->
    <mxCell id="lane_a" value="+" style="swimlane;html=1;startSize=100;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="60" width="1400" height="400" as="geometry" />
    </mxCell>
    <mxCell id="lane_a_title" value="Korporativ markazi RM xizmati" style="text;html=1;rotation=270;fontStyle=1;" vertex="1" parent="lane_a">
      <mxGeometry x="-90" y="150" width="260" height="40" as="geometry" />
    </mxCell>

    <mxCell id="start_1" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane_a">
      <mxGeometry x="140" y="180" width="50" height="50" as="geometry" />
    </mxCell>

    <mxCell id="task_1" value="Hujjatlarni tekshirish&lt;div&gt;1 44&lt;span style=&quot;color:#000&quot;&gt;0 min&lt;/span&gt;&lt;/div&gt;" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="240" y="170" width="160" height="70" as="geometry" />
    </mxCell>

    <!-- Шлюз без подписи: вопрос набран текстом вплотную к ромбу. -->
    <mxCell id="gw_1" value="" style="shape=mxgraph.bpmn.gateway2;html=1;gwType=exclusive;" vertex="1" parent="lane_a">
      <mxGeometry x="470" y="180" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="gw_1_text" value="Barcha hujjatlar mavjudmi?" style="text;html=1;fontStyle=2;" vertex="1" parent="lane_a">
      <mxGeometry x="440" y="132" width="120" height="40" as="geometry" />
    </mxCell>

    <!-- Иконка из библиотеки: украшение, к которому кто-то подвёл линию. -->
    <mxCell id="clipart" value="" style="image;html=1;image=img/lib/allied_telesis/computer_and_terminals/VOIP_IP_phone.svg;" vertex="1" parent="lane_a">
      <mxGeometry x="600" y="300" width="60" height="60" as="geometry" />
    </mxCell>

    <!-- Сложный шлюз: в draw.io ромб со звёздочкой, не плюс. -->
    <mxCell id="gw_complex" value="" style="shape=mxgraph.bpmn.gateway2;html=1;gwType=complex;" vertex="1" parent="lane_a">
      <mxGeometry x="900" y="180" width="50" height="50" as="geometry" />
    </mxCell>

    <!-- Фигура не из набора BPMN: смысл платформе неизвестен. -->
    <mxCell id="alien" value="Chuqur tahlil" style="shape=mxgraph.azure.compute.virtual_machine;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="1020" y="170" width="120" height="70" as="geometry" />
    </mxCell>

    <!-- Шаг с составным номером: «84.1.» — это не «1.» -->
    <mxCell id="task_sub" value="84.1. Mavjud kreditlarni o'rganish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="1180" y="170" width="180" height="70" as="geometry" />
    </mxCell>

    <!-- Номер без завершающей точки и число, номером не являющееся. -->
    <mxCell id="task_deep" value="37.1.1 Muzokaralar olib borish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="1400" y="170" width="180" height="70" as="geometry" />
    </mxCell>
    <mxCell id="task_year" value="2026 yil hisobotini yopish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="1620" y="170" width="180" height="70" as="geometry" />
    </mxCell>

    <!-- Событие с входом и выходом: промежуточное, а не второй старт. -->
    <mxCell id="mid_1" value="Qo'mita qarori qabul qilindi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=throwing;" vertex="1" parent="lane_a">
      <mxGeometry x="600" y="180" width="50" height="50" as="geometry" />
    </mxCell>

    <!-- Одноимённый шаг: в регламенте такие строки не отличить друг от друга. -->
    <mxCell id="task_2" value="Hujjatlarni tekshirish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="700" y="170" width="160" height="70" as="geometry" />
    </mxCell>

    <mxCell id="end_1" value="Jarayon tugadi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=end;" vertex="1" parent="lane_a">
      <mxGeometry x="920" y="180" width="50" height="50" as="geometry" />
    </mxCell>

    <!-- Документ без подписи: имя набрано текстом под ним. -->
    <mxCell id="doc_1" value="" style="shape=mxgraph.bpmn.data2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="270" y="290" width="30" height="40" as="geometry" />
    </mxCell>
    <mxCell id="doc_1_text" value="Hujjatlar ro'yxati" style="text;html=1;strokeColor=none;fillColor=none;" vertex="1" parent="lane_a">
      <mxGeometry x="240" y="335" width="120" height="20" as="geometry" />
    </mxCell>

    <mxCell id="e1" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="start_1" target="task_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e2" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="task_1" target="gw_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e3" value="Ha" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="gw_1" target="mid_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e4" value="Yo'q" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="gw_1" target="end_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e5" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="mid_1" target="task_2"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e8" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="task_2" target="gw_complex"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e9" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="gw_complex" target="alien"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e10" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="gw_complex" target="task_sub"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e11" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="alien" target="end_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e12" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="task_sub" target="task_deep"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e13" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="task_deep" target="task_year"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e14" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane_a" source="task_year" target="end_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e6" style="html=1;dashed=1;" edge="1" parent="lane_a" source="doc_1" target="task_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="e7" style="html=1;dashed=1;" edge="1" parent="lane_a" source="clipart" target="mid_1"><mxGeometry relative="1" as="geometry" /></mxCell>
  </root>
</mxGraphModel>"""


def _pmm_map(process) -> ET.Element:
    """Разбор карты из пакета .pmm — того самого файла, который читает PIX."""
    package = zipfile.ZipFile(io.BytesIO(generate_pmm_zip(process)))
    name = next(n for n in package.namelist() if n.startswith('pm/maps/'))
    return ET.fromstring(package.read(name).decode('utf-8'))


class CleanLabelTests(unittest.TestCase):
    """Разметку draw.io снимаем так, чтобы не порвать слово и не порвать число."""

    def test_inline_tag_does_not_split_a_number(self):
        raw = '<div>Kutish vaqti</div><div>1 44<span style="color:#000">0 min</span></div>'
        self.assertEqual(clean_label(raw), 'Kutish vaqti 1 440 min')

    def test_inline_tag_does_not_split_a_word(self):
        self.assertEqual(clean_label('Loyi<span>ha</span> tahlili'), 'Loyiha tahlili')

    def test_block_tag_still_separates_lines(self):
        self.assertEqual(clean_label('Birinchi<br/>Ikkinchi'), 'Birinchi Ikkinchi')

    def test_thousands_separator_is_part_of_the_number(self):
        self.assertEqual(duration_minutes('Kutish vaqti 1 440 min'), 1440.0)
        self.assertEqual(duration_minutes('9 600 min'), 9600.0)
        self.assertEqual(duration_minutes('5 min'), 5.0)

    def test_uzbek_hours_and_days_are_minutes_too(self):
        # Карты набраны по-узбекски целиком, и время на них подписано не только
        # в минутах. Пока разбирались одни минуты, такой значок пропадал молча:
        # у шага оставались часы без цифры.
        self.assertEqual(duration_minutes('8 soat'), 480.0)
        self.assertEqual(duration_minutes('Kutish vaqti 4 soat'), 240.0)
        self.assertEqual(duration_minutes('1 kun'), 1440.0)
        self.assertEqual(duration_minutes('5 kungacha'), 7200.0)
        self.assertIsNone(duration_minutes("Hujjatlarni qabul qilish"))


class DurationBadgeUnitTests(unittest.TestCase):
    """Что считается значком часов, а что — обычной фигурой с числом."""

    TIMER = 'shape=mxgraph.bpmn.event;symbol=timer;html=1;'
    TASK = 'rounded=0;whiteSpace=wrap;html=1;'

    def test_bare_number_on_a_clock_face_is_minutes(self):
        # На циферблате аналитик нередко пишет одно число: «10» на часах —
        # это десять минут, другого прочтения у значка нет.
        self.assertEqual(duration_badge_minutes(self.TIMER, '10'), 10.0)
        self.assertEqual(duration_badge_minutes(self.TIMER, '0,5'), 0.5)

    def test_bare_number_outside_a_clock_is_not_a_duration(self):
        # В подписи шага число значит номер, сумму или количество.
        self.assertIsNone(duration_badge_minutes(self.TASK, '10'))

    def test_empty_and_dashed_clocks_carry_no_time(self):
        self.assertIsNone(duration_badge_minutes(self.TIMER, ''))
        self.assertIsNone(duration_badge_minutes(self.TIMER, '-'))

    def test_long_caption_with_minutes_is_a_step_not_a_badge(self):
        long_text = "Hujjatlarni tekshirish va rasmiylashtirish 15 min"
        self.assertIsNone(duration_badge_minutes(self.TASK, long_text))
        self.assertEqual(duration_badge_minutes(self.TASK, '15 min'), 15.0)


class LooseLabelTests(unittest.TestCase):
    """Подпись, набранная отдельным блоком, принадлежит соседней фигуре."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(LOOSE_LABELS_MAP, 'loose.drawio')
        cls.by_id = {n.id: n for n in cls.process.nodes}

    def test_text_overlay_names_the_shape_next_to_it(self):
        self.assertEqual(self.by_id['gw_1'].name, 'Barcha hujjatlar mavjudmi?')
        self.assertEqual(self.by_id['doc_1'].name, "Hujjatlar ro'yxati")

    def test_text_overlay_never_becomes_a_step(self):
        for overlay_id in ('gw_1_text', 'doc_1_text', 'lane_a_title', 'banner_text'):
            self.assertNotIn(overlay_id, self.by_id, f'{overlay_id} попал на карту как фигура')

    def test_rotated_text_titles_its_lane(self):
        lane = next(l for l in self.process.lanes if l.id == 'lane_a')
        self.assertEqual(lane.name, 'Korporativ markazi RM xizmati')

    def test_banner_lane_without_steps_is_still_pruned(self):
        # Заголовок схемы тоже лежит внутри пустой swimlane-рамки: если раздать
        # заголовки до чистки, шапка останется на карте как подразделение.
        self.assertNotIn('banner', {l.id for l in self.process.lanes})

    def test_clipart_is_not_a_process_step(self):
        self.assertNotIn('clipart', self.by_id)

    def test_event_with_incoming_and_outgoing_is_intermediate(self):
        self.assertEqual(self.by_id['mid_1'].type, 'intermediateMessageEvent')
        starts = [n for n in self.process.nodes if n.type == 'startEvent']
        self.assertEqual([n.id for n in starts], ['start_1'])

    def test_duration_with_thousands_separator_reaches_the_step(self):
        task = self.by_id['task_1']
        self.assertEqual(task.slaMinutes, 1440)
        self.assertEqual(task.name, 'Hujjatlarni tekshirish')

    def test_step_number_reaches_every_export(self):
        # Номер шага — часть его названия, поэтому он обязан быть виден и в
        # регламенте, и в BPMN, и в журнале событий: по нему сверяют карту с
        # Методикой. Отдельной колонки для номера в выгрузках нет.
        numbered = self.by_id['task_sub'].name
        self.assertIn(numbered, generate_regulation_csv(self.process))
        self.assertIn('84.1.', generate_bpmn_xml(self.process))
        self.assertIn(numbered, generate_event_log_csv(self.process))

    def test_group_diagnostic_lists_every_shape_it_counts(self):
        # Замечание без адресата бесполезно: по строке отчёта холст обязан
        # подсветить те самые фигуры, о которых она говорит.
        duplicate = next(
            v for v in self.process.validation if v.code == 'duplicate_step_name'
        )
        self.assertEqual(sorted(duplicate.nodeIds or []), ['task_1', 'task_2'])

    def test_every_diagnostic_points_at_shapes_that_exist(self):
        for issue in self.process.validation:
            for node_id in issue.nodeIds or []:
                self.assertIn(node_id, self.by_id, f'{issue.code} ведёт в пустоту')


#: Карта, где содержание работ участника набрано текстом внутри его полосы.
#:
#: Так подписана полоса «Sport maydoni» в «1.54»: у неё есть имя, шагов внутри
#: нет, а чем занят спортивный комплекс — написано абзацем поверх полосы.
LANE_NOTE_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <mxCell id="partner" value="Sport maydoni" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="-160" width="800" height="80" as="geometry" />
    </mxCell>
    <mxCell id="lane_note" value="Xodimlarni sog'lom turmush tarzini olib borishi uchun sharoitlar yaratib berish va musobaqalarga tayyorgarlik ko'rish" style="text;html=1;strokeColor=none;fillColor=none;whiteSpace=wrap;align=center;" vertex="1" parent="1">
      <mxGeometry x="240" y="-130" width="300" height="30" as="geometry" />
    </mxCell>

    <mxCell id="bank" value="Sport bo'limi" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="80" width="800" height="200" as="geometry" />
    </mxCell>
    <mxCell id="start_1" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="bank">
      <mxGeometry x="60" y="80" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="task_1" value="Musobaqani tashkil etish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="bank">
      <mxGeometry x="200" y="70" width="180" height="70" as="geometry" />
    </mxCell>
    <mxCell id="end_1" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;outline=end;" vertex="1" parent="bank">
      <mxGeometry x="480" y="80" width="50" height="50" as="geometry" />
    </mxCell>

    <mxCell id="f1" edge="1" parent="bank" source="start_1" target="task_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f2" edge="1" parent="bank" source="task_1" target="end_1"><mxGeometry relative="1" as="geometry" /></mxCell>
  </root>
</mxGraphModel>"""


class LaneNoteTests(unittest.TestCase):
    """Текст, набранный внутри дорожки, остаётся на карте и в обеих выгрузках.

    Тем же приёмом, каким подписывают безымянную фигуру, аналитик пишет, чем
    занята полоса внешнего участника. Ничьей подписью такой абзац не становится
    и пропадал из модели: в bpmn.io и в студии полоса выходила пустой, хотя в
    draw.io в ней написан текст.
    """

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(LANE_NOTE_MAP, 'lane_note.drawio')
        cls.by_id = {n.id: n for n in cls.process.nodes}

    def test_the_text_survives_the_import(self):
        note = self.by_id.get('lane_note')
        self.assertIsNotNone(note, 'текст внутри дорожки потерян при разборе')
        self.assertEqual(note.type, 'textAnnotation')
        self.assertIn("sog'lom turmush", note.name)

    def test_the_text_belongs_to_the_lane_it_was_written_in(self):
        self.assertEqual(self.by_id['lane_note'].laneName, 'Sport maydoni')

    def test_the_text_is_not_a_step_of_the_regulation(self):
        steps = [n for n in self.process.nodes if n.type in ('task', 'userTask', 'serviceTask')]
        self.assertEqual([n.id for n in steps], ['task_1'])

    def test_the_text_reaches_the_bpmn(self):
        self.assertIn("sog&apos;lom turmush", generate_bpmn_xml(self.process))

    def test_the_text_reaches_the_pmm(self):
        labels = [n.get('label') or '' for n in _pmm_map(self.process).iter('node')]
        self.assertTrue(
            any("sog'lom turmush" in text for text in labels),
            'текста нет в карте .pmm — в студии полоса останется пустой',
        )

    def test_the_note_belongs_to_the_participant_process(self):
        # Импортёр вкладывает фигуру в тот пул, которому принадлежит её процесс.
        # Примечание из полосы участника, отданное процессу банка, оказывалось
        # под чужим пулом: в bpmn.io текст просвечивал сквозь его заливку.
        root = ET.fromstring(generate_bpmn_xml(self.process))
        owner = {
            proc.get('name'): [a.get('id') for a in proc.iter(f'{{{BPMN_NS}}}textAnnotation')]
            for proc in root.iter(f'{{{BPMN_NS}}}process')
        }
        self.assertIn('lane_note', owner.get('Sport maydoni', []))

    def test_the_note_stays_inside_the_participant_frame(self):
        root = ET.fromstring(generate_bpmn_xml(self.process))
        bounds = {}
        for shape in root.iter(f'{{{DI_NS}}}BPMNShape'):
            box = shape.find(f'{{{DC_NS}}}Bounds')
            if box is not None:
                bounds[shape.get('bpmnElement')] = tuple(
                    float(box.get(k)) for k in ('x', 'y', 'width', 'height'))
        pool = next(p.get('id') for p in root.iter(f'{{{BPMN_NS}}}participant')
                    if p.get('name') == 'Sport maydoni')
        px, py, pw, ph = bounds[pool]
        nx, ny, nw, nh = bounds['lane_note']
        self.assertTrue(
            px <= nx and py <= ny and nx + nw <= px + pw and ny + nh <= py + ph,
            f'примечание {(nx, ny, nw, nh)} вылезает за полосу {(px, py, pw, ph)}',
        )


DRAGGED_BRANCH_LABELS = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <mxCell id="lane" value="Devonxona" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="1700" y="-500" width="1200" height="620" as="geometry" />
    </mxCell>
    <mxCell id="step9" value="9. Hisobotni taqdim qilish" style="rounded=1;html=1;" vertex="1" parent="1">
      <mxGeometry x="1840" y="-430" width="120" height="60" as="geometry" />
    </mxCell>
    <mxCell id="step12" value="12. Xizmat yozishmani kelishish" style="rounded=1;html=1;" vertex="1" parent="1">
      <mxGeometry x="2200" y="-40" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="gw" value="Kelishildimi va imzolandimi?" style="rhombus;html=1;" vertex="1" parent="1">
      <mxGeometry x="2376" y="-25" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="gw2" value="" style="rhombus;html=1;" vertex="1" parent="1">
      <mxGeometry x="2550" y="-335" width="50" height="50" as="geometry" />
    </mxCell>

    <mxCell id="e_in" style="html=1;" edge="1" parent="1" source="step12" target="gw">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="e_up" style="html=1;" edge="1" parent="1" source="gw" target="step9">
      <mxGeometry relative="1" as="geometry">
        <Array as="points"><mxPoint x="2400" y="-439" /><mxPoint x="1890" y="-439" /></Array>
      </mxGeometry>
    </mxCell>
    <mxCell id="e_right" style="html=1;" edge="1" parent="1" source="gw" target="gw2">
      <mxGeometry relative="1" as="geometry">
        <Array as="points"><mxPoint x="2540" y="0" /></Array>
      </mxGeometry>
    </mxCell>

    <!-- Подписи веток аналитик оттащил с линий: draw.io переложил их на слой,
         и ни relative, ни родителя-связи у них не осталось. -->
    <mxCell id="lbl_yoq" value="YO&apos;Q" style="edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];" vertex="1" connectable="0" parent="1">
      <mxGeometry x="2399" y="-37" as="geometry" />
    </mxCell>
    <mxCell id="lbl_xa" value="XA" style="edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];" vertex="1" connectable="0" parent="1">
      <mxGeometry x="2452" y="-1" as="geometry" />
    </mxCell>
  </root>
</mxGraphModel>"""


class DraggedBranchLabelTests(unittest.TestCase):
    """Подпись ветки, оттащенная с линии, всё равно достаётся своей связи.

    Аналитик сдвигает «XA» и «YO'Q» мышью, чтобы они не лежали на линии, и
    draw.io перекладывает ячейку со связи на слой: остаётся стиль ``edgeLabel``
    и абсолютные координаты. Импорт складывал такую подпись в справочник по
    ``parent``, а там уже не связь, а слой, — и обе ветки шлюза «Kelishildimi
    va imzolandimi?» приходили на карту без подписей.

    Вторая половина проверки — какой именно связи подпись достанется. Место
    определяется расстоянием до самой ломаной: «YO'Q» стоит вплотную к линии,
    уходящей от шлюза вверх, хотя середина этого длинного пути далеко.
    """

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(DRAGGED_BRANCH_LABELS, 'dragged.drawio')
        cls.by_edge = {e.id: e for e in cls.process.edges}

    def test_both_branches_get_their_label(self):
        self.assertEqual(self.by_edge['e_up'].name, "YO'Q")
        self.assertEqual(self.by_edge['e_right'].name, 'XA')

    def test_the_label_does_not_land_on_the_incoming_link(self):
        # Связь «шаг 12 -> шлюз» короткая, и её середина ближе к «YO'Q», чем
        # середина длинной ветки: по центру тяжести подпись доставалась ей.
        self.assertEqual(self.by_edge['e_in'].name, '')

    def test_the_label_cell_never_becomes_a_shape(self):
        ids = {n.id for n in self.process.nodes}
        self.assertNotIn('lbl_yoq', ids)
        self.assertNotIn('lbl_xa', ids)


#: Карта, где значок из библиотеки лежит поверх шага и запирает его подпись.
#:
#: Так нарисованы шаги 36, 38 и 39 в «1.54»: иконка телефона с подписью
#: «telegram» стоит на углу шага, и расширяться шагу некуда.
PICTOGRAM_OVER_STEP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="lane_a" value="Sport bo&apos;limi" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="1200" height="240" as="geometry" />
    </mxCell>
    <mxCell id="start_1" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane_a">
      <mxGeometry x="60" y="90" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="task_long" value="36. Musobaqada xodimlarni ishtirokini ta&apos;minlash uchun xodimlarni o&apos;yinlar jadvali, vaqti, sport maydoni manzili haqida xabardor qilish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_a">
      <mxGeometry x="300" y="80" width="120" height="80" as="geometry" />
    </mxCell>
    <mxCell id="pictogram" value="telegram" style="shape=mxgraph.signs.tech.cell_phone;html=1;fillColor=#000000;" vertex="1" parent="lane_a">
      <mxGeometry x="267" y="43" width="80" height="40" as="geometry" />
    </mxCell>
    <mxCell id="end_1" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;outline=end;" vertex="1" parent="lane_a">
      <mxGeometry x="700" y="90" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="f1" edge="1" parent="lane_a" source="start_1" target="task_long"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f2" edge="1" parent="lane_a" source="task_long" target="end_1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f3" style="dashed=1;endArrow=none;" edge="1" parent="lane_a" source="pictogram" target="task_long"><mxGeometry relative="1" as="geometry" /></mxCell>
  </root>
</mxGraphModel>"""


class PictogramOverStepTests(unittest.TestCase):
    """Значок из библиотеки не шаг и не должен запирать чужую подпись."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(PICTOGRAM_OVER_STEP, 'pictogram.drawio')
        cls.by_id = {n.id: n for n in cls.process.nodes}

    def test_a_labelled_pictogram_is_not_a_step(self):
        self.assertNotIn('pictogram', self.by_id, 'иконка попала в регламент как операция')

    def test_the_label_fits_the_shape_again(self):
        from app.services.layout import label_height

        step = self.by_id['task_long']
        self.assertLessEqual(
            label_height(step.name, step.geometry.width, True),
            step.geometry.height + 0.5,
            f'подпись не помещается: рамка {step.geometry.width}x{step.geometry.height}',
        )

    def test_the_step_grew_instead_of_the_text_spilling_out(self):
        step = self.by_id['task_long']
        self.assertGreater(step.geometry.height, 80)


class UnknownShapeTests(unittest.TestCase):
    """Фигура, смысла которой платформа не знает, не проходит молча."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(LOOSE_LABELS_MAP, 'loose.drawio')
        cls.by_id = {n.id: n for n in cls.process.nodes}
        cls.by_code = {v.code: v for v in cls.process.validation}

    def test_complex_gateway_is_not_a_parallel_one(self):
        # В draw.io это ромб со звёздочкой, а не с плюсом: у параллельного
        # шлюза срабатывают все ветки, у сложного условие задаётся отдельно.
        self.assertEqual(self.by_id['gw_complex'].type, 'complexGateway')

    def test_unknown_shape_raises_a_warning_naming_the_figure(self):
        issue = self.by_code['unsupported_shape']
        self.assertEqual(issue.level, 'warning')
        self.assertIn('mxgraph.azure.compute.virtual_machine', issue.message)
        self.assertIn('Chuqur tahlil', issue.message)
        self.assertEqual(issue.nodeIds, ['alien'])

    def test_unknown_shape_still_reaches_the_map(self):
        # Выбросить незнакомую фигуру нельзя: на ней держится ветка процесса.
        self.assertIn('alien', self.by_id)

    def test_known_bpmn_shapes_raise_nothing(self):
        for node_id in ('task_1', 'gw_1', 'gw_complex', 'start_1', 'end_1', 'doc_1'):
            self.assertIsNone(
                unsupported_shape(self.by_id[node_id].style),
                f'{node_id} объявлен нераспознанным, хотя это обычная фигура BPMN',
            )

    def test_skipped_clipart_is_reported(self):
        issue = self.by_code['clipart_skipped']
        self.assertEqual(issue.level, 'info')
        self.assertIn('1', issue.message)

    def test_compound_step_number_stays_whole(self):
        # Номер шага остаётся и в названии (так он написан на карте draw.io),
        # и в коде — целиком: «84.1» — это не «1», и не тот же шаг, что «84».
        step = self.by_id['task_sub']
        self.assertEqual(step.code, 'STEP-84.1')
        self.assertEqual(step.name, "84.1. Mavjud kreditlarni o'rganish")

    def test_number_without_trailing_dot_is_still_a_number(self):
        step = self.by_id['task_deep']
        self.assertEqual(step.code, 'STEP-37.1.1')
        self.assertEqual(step.name, '37.1.1 Muzokaralar olib borish')

    def test_plain_number_in_the_text_is_not_a_step_number(self):
        # «2026 yil» — это год в названии, а не шаг №2026.
        step = self.by_id['task_year']
        self.assertNotEqual(step.code, 'STEP-2026')
        self.assertEqual(step.name, '2026 yil hisobotini yopish')


#: Файл BPMN с изломанной связью — так её пишет и наша выгрузка, и студия PIX.
BENT_EDGE_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
    xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
    xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="d1" targetNamespace="urn:sqb">
  <bpmn:process id="P1" name="Bent" processType="Private" isExecutable="true">
    <bpmn:task id="A" name="Birinchi" />
    <bpmn:task id="B" name="Ikkinchi" />
    <bpmn:sequenceFlow id="F1" sourceRef="A" targetRef="B" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="D">
    <bpmndi:BPMNPlane id="PL" bpmnElement="P1">
      <bpmndi:BPMNShape id="A_di" bpmnElement="A">
        <dc:Bounds x="100" y="100" width="120" height="60" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="B_di" bpmnElement="B">
        <dc:Bounds x="400" y="300" width="120" height="60" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="F1_di" bpmnElement="F1">
        <di:waypoint x="220" y="130" />
        <di:waypoint x="300" y="130" />
        <di:waypoint x="300" y="330" />
        <di:waypoint x="400" y="330" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>"""


class BpmnWaypointTests(unittest.TestCase):
    """Ход линии, записанный в BPMNDI, — это работа автора карты."""

    def test_bends_are_read_back_without_the_two_ends(self):
        # Крайние точки — причалы на гранях фигур, их считает трассировщик.
        # Ценны промежуточные: ими задан ход линии. Без них файл, открытый и
        # выгруженный заново, перекладывал все связи с нуля.
        process = parse_bpmn_xml(BENT_EDGE_BPMN, 'bent.bpmn')
        edge = next(e for e in process.edges if e.id == 'F1')
        self.assertEqual([(p.x, p.y) for p in edge.points], [(300, 130), (300, 330)])


def _center_inside_some_lane(node, lanes) -> bool:
    """Центр фигуры попадает внутрь какой-нибудь дорожки."""
    cx = node.geometry.x + node.geometry.width / 2
    cy = node.geometry.y + node.geometry.height / 2
    return any(
        l.geometry.x <= cx <= l.geometry.x + l.geometry.width
        and l.geometry.y <= cy < l.geometry.y + l.geometry.height
        for l in lanes
    )


class BpmnRoundTripTests(unittest.TestCase):
    """Выгрузка, открытая заново, обязана быть той же картой."""

    @classmethod
    def setUpClass(cls):
        cls.source = parse_drawio_xml(LOOSE_LABELS_MAP, 'loose.drawio')
        cls.reopened = parse_bpmn_xml(generate_bpmn_xml(cls.source), 'loose.bpmn')

    def test_artifacts_survive_the_round_trip(self):
        before = {n.name for n in self.source.nodes if n.type in ARTIFACT_NODE_TYPES}
        after = {n.name for n in self.reopened.nodes if n.type in ARTIFACT_NODE_TYPES}
        self.assertEqual(before, after)

    def test_associations_survive_the_round_trip(self):
        self.assertTrue(any(e.kind == 'association' for e in self.reopened.edges))

    def test_artifact_keeps_its_lane_after_the_round_trip(self):
        # Дорожка в BPMN перечисляет только узлы потока: документ и цилиндр
        # в ``flowNodeRef`` не записать. Дорожку им возвращаем по геометрии —
        # иначе в перечитанной карте у артефактов нет подразделения, и
        # «Исполнитель» в регламенте по ней пустой.
        inside = [
            n for n in self.reopened.nodes
            if n.type in ARTIFACT_NODE_TYPES and _center_inside_some_lane(n, self.reopened.lanes)
        ]
        self.assertTrue(inside, 'фикстура должна класть артефакты внутрь дорожек')
        self.assertEqual(
            [n.name for n in inside if not n.laneId], [],
            'артефакт внутри дорожки остался без неё',
        )

    def test_duration_badge_does_not_become_a_node(self):
        # Экспорт вешает время шага граничным таймером ради Процессной студии;
        # при обратном чтении он обязан раствориться обратно в ST шага.
        self.assertEqual(len(self.reopened.nodes), len(self.source.nodes))
        self.assertTrue(is_duration_badge_name('1 ч · ожидание 30 мин'))
        self.assertFalse(is_duration_badge_name("Qo'mita qarori"))

    def test_step_time_and_role_come_back_from_documentation(self):
        step = next(n for n in self.reopened.nodes if n.id == 'task_1')
        self.assertEqual(step.slaMinutes, 1440)
        self.assertEqual(step.role, 'Korporativ markazi RM xizmati')

    def test_passport_sla_matches_the_source(self):
        self.assertEqual(
            self.reopened.passport.targetSlaHours,
            self.source.passport.targetSlaHours,
        )


#: Карта с линией, замкнутой на саму фигуру: draw.io такое рисует молча.
SELF_LOOP_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="lane" value="Ofis" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="900" height="300" as="geometry" />
    </mxCell>
    <mxCell id="s" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane">
      <mxGeometry x="60" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="t" value="Hujjatlarni tekshirish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="200" y="110" width="160" height="70" as="geometry" />
    </mxCell>
    <mxCell id="e" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=end;" vertex="1" parent="lane">
      <mxGeometry x="460" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="f1" edge="1" parent="lane" source="s" target="t"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f2" edge="1" parent="lane" source="t" target="e"><mxGeometry relative="1" as="geometry" /></mxCell>
    <!-- Обе точки висят в пустоте рядом с одним и тем же шагом. -->
    <mxCell id="loose" style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="lane">
      <mxGeometry relative="1" as="geometry">
        <mxPoint x="215" y="150" as="sourcePoint" />
        <mxPoint x="250" y="150" as="targetPoint" />
      </mxGeometry>
    </mxCell>
    <!-- Явная петля: аналитик замкнул связь на ту же фигуру. -->
    <mxCell id="loop" edge="1" parent="lane" source="t" target="t"><mxGeometry relative="1" as="geometry" /></mxCell>
  </root>
</mxGraphModel>"""


class SelfLoopTests(unittest.TestCase):
    """Связь фигуры с самой собой не должна доехать до PIX.

    Процессная студия из-за одной такой линии отказывается открыть карту
    целиком: «Connector source and target node cannot be the same».
    """

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(SELF_LOOP_MAP, 'loop.drawio')

    def test_loose_ends_do_not_snap_onto_the_same_shape(self):
        # Оба конца линии висят в пустоте рядом с одним шагом: раньше
        # притягивались к нему же и давали петлю.
        snapped = [
            e for e in self.process.edges
            if e.id == 'loose' and e.sourceId == e.targetId
        ]
        self.assertEqual(snapped, [])

    def test_explicit_loop_is_reported_as_an_error(self):
        issue = next(v for v in self.process.validation if v.code == 'self_loop')
        self.assertEqual(issue.level, 'error')
        self.assertEqual(issue.nodeIds, ['t'])

    def test_bpmn_export_has_no_self_referencing_flow(self):
        xml = generate_bpmn_xml(self.process)
        pairs = re.findall(r'sourceRef="([^"]+)"\s+targetRef="([^"]+)"', xml)
        self.assertTrue(pairs)
        self.assertEqual([p for p in pairs if p[0] == p[1]], [])

    def test_pmm_export_has_no_self_referencing_connector(self):
        root = _pmm_map(self.process)
        pairs = [
            (c.get('sourceNodeId'), c.get('targetNodeId'))
            for c in root.iter('connector')
        ]
        self.assertTrue(pairs)
        self.assertEqual([p for p in pairs if p[0] == p[1]], [])


class PixNotationTests(unittest.TestCase):
    """Пакет .pmm должен говорить со студией её же словарём."""

    @classmethod
    def setUpClass(cls):
        cls.name, cls.elements = bpmn_notation()
        cls.process = parse_drawio_xml(LOOSE_LABELS_MAP, 'loose.drawio')

    def test_notation_name_is_written_the_way_the_studio_writes_it(self):
        # Каталог объявляет нотацию как «BPMN», а сама студия пишет в карту
        # «bpmn» (tests/fixtures/sap.pmm) — регистр она не различает. Держимся
        # её написания: расходиться с эталоном на первом атрибуте карты незачем.
        notation = _pmm_map(self.process).get('notation')
        self.assertEqual(notation, 'bpmn')
        self.assertEqual(notation.lower(), self.name.lower())

    def test_every_node_type_exists_in_the_notation(self):
        used = {n.get('type') for n in _pmm_map(self.process).iter('node')}
        self.assertTrue(used)
        self.assertEqual(used - self.elements, set())

    def test_every_type_the_exporter_can_produce_is_known(self):
        # Незнакомый тип валит импорт всего пакета, поэтому проверяем не то,
        # что попало в конкретную карту, а весь словарь экспортёра.
        for node_type in NODE_TYPE_LABELS:
            if node_type == 'lane':
                continue
            probe = ProcessNode(id='probe', name='probe', type=node_type, geometry=Geometry())
            self.assertIn(pix_type(probe), self.elements, node_type)

    def test_complex_gateway_keeps_its_own_element(self):
        probe = ProcessNode(id='gw', name='gw', type='complexGateway', geometry=Geometry())
        self.assertEqual(pix_type(probe), 'gateway_complex')

    def test_unknown_type_falls_back_to_a_known_one(self):
        self.assertEqual(pix_element('no_such_element'), 'task')


#: Карта с двумя дефектами, на которые жалуются аналитики: развилка, у которой
#: подписана одна ветка из двух, и фигура, забытая на холсте без связей.
HALF_LABELLED_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />
    <mxCell id="lane" value="RM" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="1200" height="400" as="geometry" />
    </mxCell>
    <mxCell id="s" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane">
      <mxGeometry x="60" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="gw" value="Bank tariflari mijozni qoniqtirdimi?" style="shape=mxgraph.bpmn.gateway2;html=1;gwType=exclusive;" vertex="1" parent="lane">
      <mxGeometry x="200" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="ok" value="Hujjatlar toplamini taqdim etish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="340" y="110" width="160" height="70" as="geometry" />
    </mxCell>
    <mxCell id="no" value="Rad etildi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=end;" vertex="1" parent="lane">
      <mxGeometry x="340" y="260" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="done" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=end;" vertex="1" parent="lane">
      <mxGeometry x="600" y="120" width="50" height="50" as="geometry" />
    </mxCell>

    <!-- Развилка на три ветки: смысл третьей платформа угадывать не вправе. -->
    <mxCell id="gw3" value="Qaysi yo'nalish?" style="shape=mxgraph.bpmn.gateway2;html=1;gwType=exclusive;" vertex="1" parent="lane">
      <mxGeometry x="760" y="120" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="w1" value="A" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="880" y="40" width="90" height="50" as="geometry" />
    </mxCell>
    <mxCell id="w2" value="B" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="880" y="120" width="90" height="50" as="geometry" />
    </mxCell>
    <mxCell id="w3" value="C" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="880" y="200" width="90" height="50" as="geometry" />
    </mxCell>

    <!-- Забытая на холсте фигура: ни входящих связей, ни исходящих. -->
    <mxCell id="orphan" value="Avtomat sms xabarnoma yuborish" style="label;whiteSpace=wrap;html=1;image=img/clipart/Gear_128x128.png;fillColor=#d5e8d4;strokeColor=#82b366;" vertex="1" parent="lane">
      <mxGeometry x="200" y="300" width="160" height="60" as="geometry" />
    </mxCell>

    <mxCell id="f1" edge="1" parent="lane" source="s" target="gw"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f2" edge="1" parent="lane" source="gw" target="ok"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f3" value="Yo'q" edge="1" parent="lane" source="gw" target="no"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f4" edge="1" parent="lane" source="ok" target="done"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f5" edge="1" parent="lane" source="done" target="gw3"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f6" value="A" edge="1" parent="lane" source="gw3" target="w1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f7" edge="1" parent="lane" source="gw3" target="w2"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f8" edge="1" parent="lane" source="gw3" target="w3"><mxGeometry relative="1" as="geometry" /></mxCell>
  </root>
</mxGraphModel>"""


class GatewayBranchCompletionTests(unittest.TestCase):
    """Ветка «да» рядом с подписанной «нет» — единственное однозначное чтение."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(HALF_LABELLED_MAP, 'half.drawio')
        cls.by_code = {}
        for issue in cls.process.validation:
            cls.by_code.setdefault(issue.code, []).append(issue)
        cls.edges = {e.id: e for e in cls.process.edges}

    def test_opposite_condition_is_filled_in(self):
        # PIX BPM не автоматизирует ветку без условия, а на рисунке смысл
        # второй ветки очевиден из первой.
        self.assertEqual(self.edges['f2'].name, 'Ha')
        self.assertEqual(self.edges['f2'].condition, 'Ha')

    def test_completion_is_reported_not_hidden(self):
        issue = self.by_code['gateway_branch_completed'][0]
        self.assertEqual(issue.level, 'warning')
        self.assertIn('«Ha»', issue.message)
        self.assertEqual(issue.nodeId, 'gw')

    def test_binary_gateway_is_no_longer_an_error(self):
        unlabeled = self.by_code.get('gateway_branch_unlabeled', [])
        self.assertNotIn('gw', [i.nodeId for i in unlabeled])

    def test_three_way_gateway_is_not_guessed(self):
        # Угадать смысл третьей ветки нельзя: это остаётся ошибкой.
        self.assertIsNone(self.edges['f7'].name or None)
        unlabeled = self.by_code['gateway_branch_unlabeled']
        self.assertIn('gw3', [i.nodeId for i in unlabeled])

    def test_lonely_shape_is_reported_once(self):
        # Фигура без единой связи — не разрыв цепочки, а забытая фигура:
        # одно понятное сообщение вместо двух половинчатых.
        issue = self.by_code['isolated_node'][0]
        self.assertEqual(issue.level, 'error')
        self.assertEqual(issue.nodeId, 'orphan')
        self.assertNotIn('orphan', [i.nodeId for i in self.by_code.get('no_incoming', [])])
        self.assertNotIn('orphan', [i.nodeId for i in self.by_code.get('no_outgoing', [])])

    def test_completed_condition_reaches_the_exports(self):
        # Подставленное условие бесполезно, если оно не доехало до файла: PIX
        # читает ветку именно из выгрузки, а не с холста.
        self.assertIn('<bpmn:conditionExpression', generate_bpmn_xml(self.process))
        self.assertIn('name="Ha"', generate_bpmn_xml(self.process))
        texts = {c.get('Text') for c in _pmm_map(self.process).iter('connector')}
        self.assertIn('Ha', texts)


class PastedMarkupTest(unittest.TestCase):
    """Подпись, вставленная копированием с веб-страницы.

    Аналитик копирует формулировку шага прямо из браузера, и в draw.io вместе с
    текстом приезжает разметка. Пока в атрибутах не встречалось ``>``, наивное
    снятие тегов работало. Современная вёрстка ставит ``>`` внутрь значения
    (классы Tailwind вида ``[&>*]:pointer-events-auto``), разбор обрывался
    посреди атрибута, и в Процессную студию уезжало полстраницы классов и
    data-атрибутов вместо названия шага.
    """

    def test_attribute_value_may_contain_a_closing_bracket(self):
        raw = (
            '<div class="markdown [&>*]:pointer-events-auto '
            'scroll-mt-[calc(var(--header-height)+min(200px,max(70px,20svh)))]" '
            'dir="auto" data-turn-id="e47b03b0-e67f-4c94-beed-7c6823c93123" '
            'data-testid="conversation-turn-21" data-turn="user">'
            'Avizlovchi bank ko\u2018rsatmalariga muvofiq hujjatlarni kiritish</div>'
        )
        self.assertEqual(
            clean_label(raw),
            'Avizlovchi bank ko\u2018rsatmalariga muvofiq hujjatlarni kiritish',
        )

    def test_single_quoted_attribute_too(self):
        raw = "<span style='font-family:&quot;Arial&quot;' data-x='a>b'>Hisob raqam</span>"
        self.assertEqual(clean_label(raw), 'Hisob raqam')

    def test_inline_tag_still_does_not_split_a_number(self):
        # Прежнее поведение обязано сохраниться: редактор режет подпись тегом
        # <span> посреди числа, и пробел на этом месте ломал время шага.
        self.assertEqual(clean_label('1 <span style="color:#000">440</span> min'), '1 440 min')

    def test_block_tag_still_becomes_a_space(self):
        self.assertEqual(clean_label('Loyiha<br/>ikkinchi qator'), 'Loyiha ikkinchi qator')

    def test_comment_is_dropped_whole(self):
        self.assertEqual(clean_label('<!-- a > b -->Hujjat'), 'Hujjat')

    def test_pasted_editor_clipboard_is_cut_off(self):
        """Копируя фигуры, draw.io кладёт в буфер их модель целиком.

        При вставке в текстовое поле она приезжает туда как есть, обычно в
        процентной кодировке: настоящая подпись остаётся в начале строки, а
        дальше тянется полотно разметки. На карте студии оно разворачивалось
        колонкой мусора через полсхемы.
        """
        raw = ("Xabarnoma to'liq %3CmxGraphModel%3E%3Croot%3E"
               "%3CmxCell%20id%3D%220%22%2F%3E%3CmxCell%20id%3D%221%22%2F%3E")
        self.assertEqual(clean_label(raw), "Xabarnoma to'liq")

    def test_unencoded_clipboard_is_cut_off_too(self):
        raw = 'Hujjatlar<mxGraphModel><root><mxCell id="0"/></root></mxGraphModel>'
        self.assertEqual(clean_label(raw), 'Hujjatlar')

    def test_label_that_is_only_clipboard_becomes_empty(self):
        self.assertEqual(clean_label('%3CmxGraphModel%3E%3Croot%3E%3C%2Froot%3E'), '')

    def test_markup_does_not_reach_the_exports(self):
        node = ProcessNode(
            id='pasted', name=clean_label(
                '<div class="[&>*]:pointer-events-auto" data-turn="user">Hujjatni tekshirish</div>'),
            type='userTask', geometry=Geometry(x=0, y=0, width=160, height=60))
        self.assertEqual(node.name, 'Hujjatni tekshirish')
        self.assertNotIn('pointer-events', node.name)


#: Карта, где у пунктира от системы конец не привязан к фигуре.
#:
#: Так нарисованы все карты августовской партии: цилиндр «CROBS» стоит над
#: шагом, а линия от него кончается точкой в паре пикселей от рамки шага. Рамка
#: дорожки эту точку тоже накрывает, и притяжка выбирала дорожку — связь
#: становилась оформлением и в выгрузку не шла.
FREE_END_OVER_A_STEP = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <mxCell id="lane_bank" value="Bankimiz xodimlari" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="200" width="1200" height="200" as="geometry" />
    </mxCell>
    <mxCell id="lane_client" value="Mijoz" style="swimlane;html=1;horizontal=0;startSize=40;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="1200" height="120" as="geometry" />
    </mxCell>

    <mxCell id="start_1" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane_bank">
      <mxGeometry x="60" y="80" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="task_1" value="Arizani tekshirish" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane_bank">
      <mxGeometry x="200" y="70" width="160" height="70" as="geometry" />
    </mxCell>
    <mxCell id="end_1" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;outline=end;" vertex="1" parent="lane_bank">
      <mxGeometry x="460" y="80" width="50" height="50" as="geometry" />
    </mxCell>
    <mxCell id="ds_crobs" value="CROBS" style="shape=datastore;html=1;" vertex="1" parent="1">
      <mxGeometry x="255" y="230" width="30" height="20" as="geometry" />
    </mxCell>

    <mxCell id="f1" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_bank" source="start_1" target="task_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f2" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane_bank" source="task_1" target="end_1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>

    <mxCell id="assoc" style="dashed=1;endArrow=none;" edge="1" parent="1" source="ds_crobs">
      <mxGeometry relative="1" as="geometry">
        <mxPoint x="270" y="264" as="targetPoint" />
      </mxGeometry>
    </mxCell>
    <mxCell id="to_client" style="dashed=1;endArrow=open;" edge="1" parent="1" source="task_1">
      <mxGeometry relative="1" as="geometry">
        <mxPoint x="280" y="60" as="targetPoint" />
      </mxGeometry>
    </mxCell>
  </root>
</mxGraphModel>"""


class FreeEndpointSnapTests(unittest.TestCase):
    """Свободный конец линии держится за шаг, а не за дорожку под ним."""

    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(FREE_END_OVER_A_STEP, 'free_end.drawio')
        cls.edges = {e.id: e for e in cls.process.edges}

    def test_dashed_line_from_a_system_reaches_the_step(self):
        edge = self.edges['assoc']
        self.assertEqual(edge.targetId, 'task_1')
        self.assertEqual(edge.kind, 'association')

    def test_the_step_learns_which_system_it_works_in(self):
        task = next(n for n in self.process.nodes if n.id == 'task_1')
        self.assertEqual(task.system, 'CROBS')

    def test_line_to_an_empty_participant_lane_still_holds_the_lane(self):
        # У полосы внешнего участника шагов внутри нет, и притягивать пунктир
        # больше не к чему: он обязан остаться обменом сообщениями.
        edge = self.edges['to_client']
        self.assertEqual(edge.targetId, 'lane_client')
        self.assertEqual(edge.kind, 'messageFlow')


if __name__ == '__main__':
    unittest.main()
