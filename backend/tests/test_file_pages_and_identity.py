"""Страницы файла, таблицы draw.io и постоянство идентификатора карты.

Проверяются четыре дефекта приёмки:

* страница-таблица («Tavsif» в «Akkreditiv ochish») разбиралась процессом из
  тридцати шагов и уходила в выгрузку;
* варианты процесса («To Be», «AS IS ўзгариш») уходили в файл основной карты
  её подпроцессами;
* подсказка о страницах говорила «на холст они не выводятся», хотя страницы
  открываются вкладками;
* разные карты с одним именем файла затирали друг друга, а код процесса
  менялся при каждом повторном импорте.
"""
import os
import re
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.bpmn_exporter import generate_bpmn_xml  # noqa: E402
from app.services.drawio_parser import is_variant_page_name, parse_drawio_xml  # noqa: E402
from app.services.pmm_exporter import generate_pmm_zip  # noqa: E402


def _flow_page(name: str, prefix: str, extra: str = '') -> str:
    """Страница с дорожкой, началом, шагом со временем и концом."""
    return f'''<diagram id="{prefix}" name="{name}"><mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="{prefix}_lane" value="Bank" style="swimlane;horizontal=0;startSize=30;" vertex="1" parent="1">
        <mxGeometry x="0" y="0" width="900" height="200" as="geometry"/></mxCell>
      <mxCell id="{prefix}_s" value="Start" style="shape=mxgraph.bpmn.event;symbol=general;outline=standard;" vertex="1" parent="{prefix}_lane">
        <mxGeometry x="40" y="80" width="40" height="40" as="geometry"/></mxCell>
      <mxCell id="{prefix}_t" value="Hisobni tekshirish 5 min" style="rounded=1;" vertex="1" parent="{prefix}_lane">
        <mxGeometry x="180" y="60" width="180" height="80" as="geometry"/></mxCell>
      <mxCell id="{prefix}_e" value="End" style="shape=mxgraph.bpmn.event;symbol=general;outline=end;" vertex="1" parent="{prefix}_lane">
        <mxGeometry x="440" y="80" width="40" height="40" as="geometry"/></mxCell>
      <mxCell id="{prefix}_f1" edge="1" parent="{prefix}_lane" source="{prefix}_s" target="{prefix}_t"><mxGeometry relative="1" as="geometry"/></mxCell>
      <mxCell id="{prefix}_f2" edge="1" parent="{prefix}_lane" source="{prefix}_t" target="{prefix}_e"><mxGeometry relative="1" as="geometry"/></mxCell>
      {extra}
    </root></mxGraphModel></diagram>'''


def _table(prefix: str, x: int = 0, y: int = 0) -> str:
    """Таблица draw.io 2×2 ровно в том виде, в каком её пишет редактор."""
    rows = []
    for r, cells in enumerate((('AS IS', '78 daqiqa'), ('TO BE', '41 daqiqa'))):
        row = f'{prefix}_r{r}'
        rows.append(
            f'<mxCell id="{row}" style="shape=tableRow;horizontal=0;startSize=0;swimlaneHead=0;swimlaneBody=0;" '
            f'vertex="1" parent="{prefix}_tbl"><mxGeometry y="{r * 60}" width="400" height="60" as="geometry"/></mxCell>')
        for c, text in enumerate(cells):
            rows.append(
                f'<mxCell id="{row}_c{c}" value="{text}" style="shape=partialRectangle;connectable=0;" '
                f'vertex="1" parent="{row}"><mxGeometry x="{c * 200}" width="200" height="60" as="geometry"/></mxCell>')
    return (
        f'<mxCell id="{prefix}_tbl" style="shape=table;startSize=0;container=1;childLayout=tableLayout;" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="400" height="120" as="geometry"/></mxCell>' + ''.join(rows))


def _table_page(name: str, prefix: str) -> str:
    return f'''<diagram id="{prefix}" name="{name}"><mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>{_table(prefix, 40, 40)}
    </root></mxGraphModel></diagram>'''


FILE = (
    '<mxfile host="test">'
    + _flow_page('As Is', 'main')
    + _flow_page('Konvertatsiya', 'part')
    + _flow_page('To Be', 'tobe')
    + _table_page('Tavsif', 'desc')
    + '</mxfile>'
)


class PageKindsTest(unittest.TestCase):
    """Часть процесса, вариант и таблица — три разные судьбы страницы."""

    def setUp(self):
        self.process = parse_drawio_xml(FILE, 'multi.drawio')

    def test_variant_marker_is_read_from_the_page_name(self):
        for name in ('As Is', 'AS-IS', 'X/р очиш To-Be', 'Янги х/р очиш AS IS', 'TOBE'):
            self.assertTrue(is_variant_page_name(name), name)
        for name in ('Konvertatsiya', 'JAMI', "TO'LOV", 'Oasis bank', 'Basis'):
            self.assertFalse(is_variant_page_name(name), name)

    def test_parts_variants_and_tables_are_told_apart(self):
        self.assertEqual([p.name for p in self.process.subProcesses], ['Konvertatsiya'])
        self.assertEqual([p.name for p in self.process.variants], ['To Be'])
        self.assertTrue(self.process.variants[0].nodes, 'вариант разобран пустым — содержимое потеряно')

    def test_variant_and_table_do_not_reach_the_exports(self):
        xml = generate_bpmn_xml(self.process)
        names = re.findall(r'<bpmn:process [^>]*name="([^"]*)"', xml)
        self.assertIn('Konvertatsiya', names)
        self.assertNotIn('To Be', names)
        self.assertNotIn('Tavsif', names)
        self.assertEqual(xml.count('<bpmndi:BPMNDiagram '), 2)
        import io
        import zipfile
        # В .pmm страницы едут одной картой; вариант и таблица в неё не попадают.
        package = zipfile.ZipFile(io.BytesIO(generate_pmm_zip(self.process)))
        maps = [n for n in package.namelist() if n.startswith('pm/maps/')]
        self.assertEqual(len(maps), 1, maps)
        labels = set(re.findall(r' label="([^"]*)"', package.read(maps[0]).decode('utf-8')))
        self.assertTrue({n.name for n in self.process.subProcesses[0].nodes if n.name} <= labels)
        self.assertFalse({n.name for n in self.process.variants[0].nodes if n.name} - {
            n.name for p in [self.process, *self.process.subProcesses] for n in p.nodes} & labels)

    def test_pages_note_says_what_happened_to_each_page(self):
        notes = [v for v in self.process.validation if v.code == 'pages_skipped']
        self.assertEqual(len(notes), 1)
        text = f'{notes[0].message} {notes[0].hint}'
        self.assertIn('«As Is»', notes[0].message)
        self.assertIn('«Konvertatsiya» — часть процесса: открывается вкладкой', text)
        self.assertIn('«To Be» — вариант процесса', text)
        self.assertIn('в выгрузку этой карты не идёт', text)
        self.assertIn('«Tavsif» — таблица', text)
        self.assertNotIn('не выводятся', text)

    def test_page_ids_live_inside_their_map(self):
        ids = [p.id for p in self.process.subProcesses + self.process.variants]
        self.assertTrue(all(i.startswith(self.process.id + '_p') for i in ids), ids)
        self.assertEqual(len(set(ids)), len(ids))


class TableOnProcessPageTest(unittest.TestCase):
    """Таблица на странице со схемой не становится шагами и не крадёт время."""

    def setUp(self):
        page = _flow_page('Map', 'm', extra=_table('t', 0, 400))
        self.process = parse_drawio_xml(f'<mxfile host="test">{page}</mxfile>', 'table.drawio')

    def test_table_cells_are_not_nodes(self):
        names = [n.name for n in self.process.nodes + self.process.lanes]
        for text in ('AS IS', 'TO BE', '78 daqiqa', '41 daqiqa'):
            self.assertNotIn(text, names)
        self.assertEqual(len(self.process.lanes), 1, 'строка таблицы сошла за дорожку')

    def test_table_is_reported(self):
        notes = [v for v in self.process.validation if v.code == 'table_skipped']
        self.assertEqual(len(notes), 1)
        self.assertIn('«AS IS»', notes[0].message)
        self.assertIn('ячеек в ней: 4', notes[0].message)

    def test_step_keeps_its_own_time(self):
        step = next(n for n in self.process.nodes if n.type in ('task', 'userTask', 'serviceTask'))
        self.assertEqual(step.slaMinutes, 5)


class ProcessIdentityTest(unittest.TestCase):
    """Ключ карты — имя файла вместе с содержимым; код — от ключа."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _post(self, xml: str, name: str):
        res = self.client.post('/api/v1/import/xml', json={'xml': xml, 'fileName': name})
        self.assertEqual(res.status_code, 200, res.text[:300])
        return res.json()

    def test_different_maps_with_one_name_do_not_overwrite_each_other(self):
        name = f'Pasted_{uuid.uuid4().hex[:6]}.drawio'
        first = self._post(f'<mxfile host="test">{_flow_page("A", "a")}</mxfile>', name)
        second = self._post(f'<mxfile host="test">{_flow_page("B", "b")}</mxfile>', name)
        self.assertNotEqual(first['id'], second['id'])
        stored = {p['id'] for p in self.client.get('/api/v1/processes/').json()}
        self.assertIn(first['id'], stored, 'первая карта затёрта второй')
        self.assertIn(second['id'], stored)

    def test_file_name_case_still_does_not_matter_for_the_same_map(self):
        xml = f'<mxfile host="test">{_flow_page("C", "c")}</mxfile>'
        stem = uuid.uuid4().hex[:6]
        self.assertEqual(self._post(xml, f'Kredit_{stem}.drawio')['id'],
                         self._post(xml, f'kredit_{stem}.DRAWIO')['id'])

    def test_passport_code_is_stable_across_reimports(self):
        xml = f'<mxfile host="test">{_flow_page("D", "d")}</mxfile>'
        name = f'stable_{uuid.uuid4().hex[:6]}.drawio'
        first, again = self._post(xml, name), self._post(xml, name)
        self.assertEqual(first['passport']['code'], again['passport']['code'])
        disposition = lambda pid: self.client.get(f'/api/v1/import/{pid}/export/bpmn').headers['content-disposition']
        self.assertEqual(disposition(first['id']), disposition(again['id']))


if __name__ == '__main__':
    unittest.main()
