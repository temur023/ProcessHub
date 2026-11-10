"""Время шага в свойствах фигуры карты PIX.

Панель свойств шага в Процессной студии показывала «000д 00ч 00м 00с» даже
там, где на карте стояли часы с цифрой: время жило только подписью, а свойства
фигуры платформа не писала вовсе. Здесь проверяется и разбор того, что пишут
аналитики в draw.io, и структура, которая из этого получается в файле.
"""
import io
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import Geometry, ProcessNode
from app.services.drawio_parser import parse_drawio_xml
from app.services.export_validation import validate_pmm_package
from app.services.pix_spec_checker import validate_pmm_for_pix
from app.services.pmm_exporter import (
    generate_pmm_zip,
    minutes_to_timespan,
    node_properties_xml,
    parse_duration_minutes,
    parse_time_to_timespan,
)

DRAWIO = """<mxfile host="app.diagrams.net">
  <diagram id="d1" name="Карта">
    <mxGraphModel>
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="s" value="Начало" style="ellipse;fillColor=#10b981;" vertex="1" parent="1">
          <mxGeometry x="60" y="60" width="50" height="50" as="geometry" />
        </mxCell>
        <mxCell id="t" value="Проверка документов" style="rounded=1;" vertex="1" parent="1">
          <mxGeometry x="200" y="50" width="200" height="80" as="geometry" />
        </mxCell>
        <mxCell id="badge" value="15 мин" style="shape=mxgraph.bpmn.timer_start;outlineConnect=0;" vertex="1" parent="1">
          <mxGeometry x="360" y="118" width="24" height="24" as="geometry" />
        </mxCell>
        <mxCell id="e1" edge="1" source="s" target="t" parent="1" />
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


def _map_root(process):
    package = zipfile.ZipFile(io.BytesIO(generate_pmm_zip(process)))
    part = next(n for n in package.namelist() if n.startswith('pm/maps/'))
    return ET.fromstring(package.read(part))


class DurationParsingTest(unittest.TestCase):
    """Разбор того, что аналитик пишет в подписи шага."""

    def test_bare_number_is_minutes(self):
        self.assertEqual(parse_time_to_timespan('15'), '00:15:00')

    def test_units_in_three_languages(self):
        for text in ('15m', '15 min', '15 мин', '15 daq'):
            self.assertEqual(parse_time_to_timespan(text), '00:15:00', text)
        for text in ('2h', '2 ч', '2 soat', '2 часа'):
            self.assertEqual(parse_time_to_timespan(text), '02:00:00', text)

    def test_fractional_values(self):
        self.assertEqual(parse_time_to_timespan('1.5h'), '01:30:00')
        self.assertEqual(parse_time_to_timespan('1,5 ч'), '01:30:00')

    def test_thousands_separator(self):
        # «1 440 min» аналитики пишут именно так — с неразрывным пробелом тоже.
        self.assertEqual(parse_time_to_timespan('1 440 мин'), '1.00:00:00')
        self.assertEqual(parse_time_to_timespan('1 440 мин'), '1.00:00:00')

    def test_compound_value_is_summed(self):
        self.assertEqual(parse_time_to_timespan('1 ч 30 мин'), '01:30:00')

    def test_unparsable_input_yields_nothing(self):
        # Тег со значением «мусор» в файл уходить не должен.
        for text in (None, '', '   ', 'abc', 'скоро', '3 shtuk', '0 мин', '-5'):
            self.assertIsNone(parse_time_to_timespan(text), repr(text))

    def test_unknown_unit_is_not_silently_taken_for_minutes(self):
        self.assertIsNone(parse_duration_minutes('3 shtuk'))
        self.assertEqual(parse_duration_minutes('3'), 3.0)


class TimeSpanFormatTest(unittest.TestCase):
    """Формат .NET TimeSpan, а не просто «часы:минуты:секунды»."""

    def test_plain_time_of_day(self):
        self.assertEqual(minutes_to_timespan(90), '01:30:00')
        self.assertEqual(minutes_to_timespan(1), '00:01:00')

    def test_seconds_survive(self):
        self.assertEqual(minutes_to_timespan(1.5), '00:01:30')

    def test_a_day_and_more_uses_the_day_field(self):
        # TimeSpan.Parse("24:00:00") в .NET падает: часы обязаны лежать в 0..23,
        # а сутки выносятся отдельным полем через точку.
        self.assertEqual(minutes_to_timespan(1440), '1.00:00:00')
        self.assertEqual(minutes_to_timespan(1440 + 90), '1.01:30:00')

    def test_nothing_for_empty_duration(self):
        for value in (None, 0, -5):
            self.assertIsNone(minutes_to_timespan(value))


class PropertiesBlockTest(unittest.TestCase):
    """Структура блока свойств внутри фигуры."""

    def test_block_is_omitted_when_there_is_no_time(self):
        node = ProcessNode(id='t', name='Шаг', type='userTask', slaMinutes=0,
                           waitMinutes=0, slaMeasured=True,
                           geometry=Geometry(x=0, y=0, width=160, height=80))
        self.assertEqual(node_properties_xml(node, '  '), [])

    def test_invented_time_never_reaches_the_properties(self):
        """Время, подставленное импортом, свойством шага не становится.

        Когда в подписи времени нет, импорт ставит правдоподобное значение по
        категории — для расчёта SLA это нужно. Но в панели свойств оно
        выглядит замером, которого никто не делал.
        """
        node = ProcessNode(id='t', name='Шаг', type='userTask', slaMinutes=60,
                           waitMinutes=0, slaMeasured=False,
                           geometry=Geometry(x=0, y=0, width=160, height=80))
        self.assertEqual(node_properties_xml(node, '  '), [])

    def test_step_time_is_the_system_process_time_in_seconds(self):
        """Формат — как в выгрузке самой PIX (PIX банка, 2026-09-22)."""
        node = ProcessNode(id='t', name='Шаг', type='userTask', slaMinutes=120,
                           waitMinutes=45, slaMeasured=True, geometry=Geometry(x=0, y=0, width=160, height=80))
        block = '\n'.join(node_properties_xml(node, '  '))
        self.assertIn('<prop name="Время процесса" inner_name="system_process_time" value="7200">', block)
        self.assertRegex(block, r'<PropertyId>[0-9a-f-]{36}</PropertyId>')
        # Старый блок студия отбрасывала молча — его в файле быть не должно.
        self.assertNotIn('<Properties>', block)

    def test_main_process_time_is_named_by_its_key(self):
        """Студия ищет шаблон по ``name`` и берёт первый с таким именем.

        «Время процесса» зовутся два шаблона — системный 21 и обычный 8, и
        значение под этим именем попадало только в системный: поле «Основные →
        Время процесса» оставалось пустым. Под именем ``vremya_protsessa`` оно
        доходит (PIX банка, 2026-09-24, карта уровня 3, проба P16).
        """
        node = ProcessNode(id='t', name='Шаг', type='userTask', slaMinutes=30,
                           waitMinutes=0, slaMeasured=True, geometry=Geometry(x=0, y=0, width=160, height=80))
        block = '\n'.join(node_properties_xml(node, '  '))
        self.assertIn('<prop name="vremya_protsessa" inner_name="vremya_protsessa" value="1800">', block)

    def test_property_id_is_stable(self):
        node = ProcessNode(id='t', name='Шаг', type='userTask', slaMinutes=15,
                           waitMinutes=0, slaMeasured=True, geometry=Geometry(x=0, y=0, width=160, height=80))
        self.assertEqual(node_properties_xml(node, '  '), node_properties_xml(node, '  '))

    def test_timer_between_steps_carries_its_time(self):
        """Панель таймера показывает «Время процесса» — туда и идёт его время.

        Без значения там стояло «000д 00ч 00м 00с», хотя на карте у таймера
        написано «15 min».
        """
        node = ProcessNode(id='w', name='Kutish vaqti 15 min', type='intermediateTimerEvent',
                           slaMinutes=15, waitMinutes=15, slaMeasured=True,
                           geometry=Geometry(x=0, y=0, width=40, height=40))
        names = re.findall(r'inner_name="([^"]+)" value="(\d+)"', '\n'.join(node_properties_xml(node, '  ')))
        self.assertEqual(names, [('system_process_time', '900')])

    def test_sub_process_gets_no_time_property(self):
        """К «Подпроцессу» шаблон времени в студии не привязан.

        Импорт отвечал «Шаблон свойств 'Время процесса' отвязан от
        'sub_process'. Использующие его свойства не будут импортированы»
        (PIX банка, 2026-09-24, карта уровня 7). Время подпроцесса на карте
        видно по его часам.
        """
        node = ProcessNode(id='p', name='Подпроцесс', type='subProcess', slaMinutes=30,
                           waitMinutes=15, slaMeasured=True,
                           geometry=Geometry(x=0, y=0, width=160, height=80))
        self.assertEqual(node_properties_xml(node, '  '), [])

    def test_other_events_get_no_property(self):
        node = ProcessNode(id='s', name='Start', type='startEvent', slaMinutes=15, slaMeasured=True,
                           geometry=Geometry(x=0, y=0, width=40, height=40))
        self.assertEqual(node_properties_xml(node, '  '), [])

    def test_names_come_from_the_studio_catalogue(self):
        """Ключ не из каталога панель свойств не показывает вовсе."""
        import xml.etree.ElementTree as ET
        from app.services.pmm_exporter import _CONFIGURATION_PATH
        catalogue = {
            t.get('name')
            for t in ET.parse(_CONFIGURATION_PATH).getroot().findall('propertyTemplate')
        }
        node = ProcessNode(id='t', name='Шаг', type='userTask', slaMinutes=15,
                           waitMinutes=45, slaMeasured=True, geometry=Geometry(x=0, y=0, width=160, height=80))
        names = re.findall(r'inner_name="([^"]+)"', '\n'.join(node_properties_xml(node, '  ')))
        self.assertEqual(names, ['system_process_time', 'vremya_protsessa', 'vremya_ozhidaniya'])
        self.assertLessEqual(set(names), catalogue)

    def test_wait_time_goes_to_its_own_template(self):
        """Ожидание — шаблон «Время ожидания»; PIX хранит его на любом уровне
        карты и показывает там, где шаблон привязан к «Задаче» (уровни 1–6)."""
        node = ProcessNode(id='t', name='Шаг', type='userTask', slaMinutes=60,
                           waitMinutes=45, slaMeasured=False,
                           geometry=Geometry(x=0, y=0, width=160, height=80))
        block = '\n'.join(node_properties_xml(node, '  '))
        self.assertIn('<prop name="Время ожидания" inner_name="vremya_ozhidaniya" value="2700">', block)
        self.assertNotIn('Время процесса', block, 'подставленное время выполнения не пишется')

class ExportedPackageTest(unittest.TestCase):
    """То же самое, но в собранном пакете."""

    def setUp(self):
        self.process = parse_drawio_xml(DRAWIO, 'time.drawio')
        self.root = _map_root(self.process)

    def test_step_carries_its_time(self):
        step = next(n for n in self.root.iter('node') if n.get('type') == 'task')
        values = {p.get('inner_name'): p.get('value') for p in step.findall('prop')}
        self.assertEqual(values, {'system_process_time': '900', 'vremya_protsessa': '900'})

    def test_shape_without_time_stays_self_closing(self):
        # Так пишет и сама студия: лишний открывающий тег на пустом блоке ни к
        # чему, а фигур без времени на карте большинство.
        for node in self.root.iter('node'):
            if node.get('type') == 'task':
                continue
            self.assertIsNone(node.find('prop'), node.get('label'))

    def test_properties_do_not_count_as_nested_shapes(self):
        # Вложенные фигуры разрешены только контейнерам: блок свойств не должен
        # приниматься проверкой за вложенную фигуру.
        payload = generate_pmm_zip(self.process)
        for check in (validate_pmm_package(payload), validate_pmm_for_pix(payload)):
            self.assertEqual([f'{p.code}: {p.message}' for p in check.errors], [],
                             check.format)


class TimeSpanValidationTest(unittest.TestCase):
    """Проверка выгрузки обязана поймать негодное значение времени до студии."""

    def _broken(self, value: str) -> bytes:
        """Тот же пакет, но у первого свойства подменено значение."""
        payload = generate_pmm_zip(parse_drawio_xml(DRAWIO, 'time.drawio'))
        source = zipfile.ZipFile(io.BytesIO(payload))
        part = next(n for n in source.namelist() if n.startswith('pm/maps/'))
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == part:
                    text = data.decode('utf-8')
                    head, sep, tail = text.partition('inner_name="system_process_time" value="')
                    _old, _, rest = tail.partition('"')
                    data = (head + sep + value + '"' + rest).encode('utf-8')
                target.writestr(item, data)
        return out.getvalue()

    def test_timespan_text_is_rejected(self):
        # Студия пишет секунды; «hh:mm:ss» — прежний формат, который она не читала.
        check = validate_pmm_for_pix(self._broken('02:00:00'))
        self.assertIn('pix_pmm_property_timespan', [p.code for p in check.errors])

    def test_garbage_value_is_rejected(self):
        check = validate_pmm_for_pix(self._broken('скоро'))
        self.assertIn('pix_pmm_property_timespan', [p.code for p in check.errors])

    def test_a_day_long_value_passes(self):
        check = validate_pmm_for_pix(self._broken('129600'))
        self.assertEqual([p.code for p in check.errors], [])



class UnknownPropertyNameTest(unittest.TestCase):
    """Ключ не из каталога — дефект, который иначе виден только глазами."""

    def test_shadow_attribute_is_reported(self):
        # Студия принимает такой ключ молча: файл открывается, значение
        # сохраняется, а поля в панели свойств просто нет.
        payload = generate_pmm_zip(parse_drawio_xml(DRAWIO, 'time.drawio'))
        source = zipfile.ZipFile(io.BytesIO(payload))
        part = next(n for n in source.namelist() if n.startswith('pm/maps/'))
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == part:
                    data = data.decode('utf-8').replace(
                        'inner_name="system_process_time"', 'inner_name="ProcessingTime"', 1
                    ).encode('utf-8')
                target.writestr(item, data)

        check = validate_pmm_for_pix(out.getvalue())
        codes = [p.code for p in check.errors]
        self.assertIn('pix_pmm_property_unknown', codes)
        self.assertTrue(any('ProcessingTime' in p.message for p in check.errors))



if __name__ == '__main__':
    unittest.main()
