"""Многостраничный draw.io: страницы-подпроцессы и свёрнутые активности.

«Xizmat avtotransportini boshqarish jarayoni» — первая карта банка, где
страницы файла не варианты одного процесса (AS-IS / TO-BE), а его части:
покупка, страхование, ремонт, топливо, продажа и сводная «JAMI». Импорт брал
одну страницу и сообщал, что остальные пропущены, — из 1300 фигур в работу шло
150, а полная карта не попадала вовсе.

Отсюда три вещи, которые проверяет этот набор:

* основной становится сводная страница «JAMI», а не первая по счёту;
* остальные страницы разбираются подпроцессами и едут в выгрузку своими
  ``<bpmn:process>`` со своими схемами;
* фигура со значком подпроцесса (квадратик с плюсом, ``isLoopSub=1``) доезжает
  до выгрузки как ``<bpmn:subProcess>``, а если её подпись совпала с именем
  страницы — как ``<bpmn:callActivity>`` со ссылкой на процесс этой страницы.

Отдельно проверяется обёртка ``<object>``: фигуру с собственными свойствами
draw.io пишет именно так, разбор шёл по ``mxCell``, и такая фигура пропадала
молча вместе со всеми связями, которые к ней шли.
"""
import io
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.bpmn_exporter import generate_bpmn_xml
from app.services.drawio_parser import page_report, parse_drawio_xml
from app.services.export_validation import validate_bpmn_xml, validate_pmm_package
from app.services.pmm_exporter import generate_pmm_zip, merged_pages

BPMN = 'http://www.omg.org/spec/BPMN/20100524/MODEL'


def _lane(lane_id: str, name: str, width: int = 900) -> str:
    return (
        f'<mxCell id="{lane_id}" value="{name}" '
        'style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">'
        f'<mxGeometry x="0" y="0" width="{width}" height="200" as="geometry"/></mxCell>'
    )


def _flow(page: str, lane_id: str, sub_cell: str) -> str:
    """Страница с началом, одним шагом-подпроцессом и концом."""
    return f'''<diagram id="{page}" name="{page}">
    <mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      {_lane(lane_id, 'Bank')}
      <mxCell id="{page}_s" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="{lane_id}">
        <mxGeometry x="40" y="80" width="40" height="40" as="geometry"/></mxCell>
      {sub_cell}
      <mxCell id="{page}_e" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=end;" vertex="1" parent="{lane_id}">
        <mxGeometry x="440" y="80" width="40" height="40" as="geometry"/></mxCell>
      <mxCell id="{page}_f1" style="html=1;" edge="1" parent="{lane_id}" source="{page}_s" target="{page}_t"><mxGeometry relative="1" as="geometry"/></mxCell>
      <mxCell id="{page}_f2" style="html=1;" edge="1" parent="{lane_id}" source="{page}_t" target="{page}_e"><mxGeometry relative="1" as="geometry"/></mxCell>
    </root></mxGraphModel>
  </diagram>'''


#: Фигура подпроцесса ровно в том виде, в каком её пишет draw.io: обёртка
#: `<object>` с подписью и `isLoopSub=1` в стиле внутренней ячейки.
SUB_SHAPE = '''<object label="TO&apos;LOV" id="JAMI_t">
        <mxCell style="shape=mxgraph.bpmn.task2;html=1;rectStyle=rounded;taskMarker=abstract;isLoopSub=1;container=1;expand=0;collapsible=0;" vertex="1" parent="bank">
          <mxGeometry x="180" y="60" width="180" height="80" as="geometry"/>
        </mxCell>
      </object>'''

PLAIN_SHAPE = '''<mxCell id="TO&apos;LOV_t" value="Hisobni to&apos;lash&#10;5 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="pay">
        <mxGeometry x="180" y="60" width="180" height="80" as="geometry"/></mxCell>'''

# Страница «JAMI» стоит ВТОРОЙ: правило обязано брать её по имени, а не по
# порядку. Её шаг-подпроцесс подписан именем третьей страницы — «TO'LOV».
MULTIPAGE = f'''<mxfile host="test" pages="3">
  <diagram id="first" name="SOTIB OLISH">
    <mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      {_lane('buy', 'Bank', 700)}
      <mxCell id="buy_s" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="buy">
        <mxGeometry x="40" y="80" width="40" height="40" as="geometry"/></mxCell>
      <mxCell id="buy_t" value="Avtotransport sotib olish&#10;5 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="buy">
        <mxGeometry x="180" y="60" width="180" height="80" as="geometry"/></mxCell>
      <mxCell id="buy_e" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=end;" vertex="1" parent="buy">
        <mxGeometry x="440" y="80" width="40" height="40" as="geometry"/></mxCell>
      <mxCell id="buy_f1" style="html=1;" edge="1" parent="buy" source="buy_s" target="buy_t"><mxGeometry relative="1" as="geometry"/></mxCell>
      <mxCell id="buy_f2" style="html=1;" edge="1" parent="buy" source="buy_t" target="buy_e"><mxGeometry relative="1" as="geometry"/></mxCell>
    </root></mxGraphModel>
  </diagram>
  {_flow('JAMI', 'bank', SUB_SHAPE)}
  {_flow("TO'LOV", 'pay', PLAIN_SHAPE)}
</mxfile>'''


class MultiPageImportTest(unittest.TestCase):
    """Основная карта — сводная страница, остальные не теряются."""

    def setUp(self):
        self.process = parse_drawio_xml(MULTIPAGE, 'avto.drawio')

    def test_summary_page_becomes_the_main_map(self):
        """«JAMI» — «всего» по-узбекски: на ней процесс собран целиком."""
        used, skipped = page_report(MULTIPAGE)
        self.assertEqual(used, 'JAMI', 'основной осталась не сводная страница')
        self.assertEqual(sorted(skipped), sorted(['SOTIB OLISH', "TO'LOV"]))

    def test_other_pages_come_back_as_sub_processes(self):
        names = [page.name for page in self.process.subProcesses]
        self.assertEqual(sorted(names), sorted(['SOTIB OLISH', "TO'LOV"]),
                         'страницы файла потеряны при импорте')

    def test_a_sub_page_keeps_its_own_shapes(self):
        """Страница разбирается тем же разбором, а не заглушкой."""
        page = next(p for p in self.process.subProcesses if p.name == 'SOTIB OLISH')
        self.assertIn('Avtotransport sotib olish', [n.name for n in page.nodes])
        self.assertTrue(page.lanes, 'у страницы пропали дорожки')

    def test_pages_do_not_pile_up_on_the_main_canvas(self):
        """Содержимое страниц на основной холст не переносится."""
        names = [n.name for n in self.process.nodes]
        self.assertNotIn('Avtotransport sotib olish', names)


class CollapsedSubProcessTest(unittest.TestCase):
    """Фигура с плюсом доезжает до выгрузки подпроцессом, а не обычным шагом."""

    def setUp(self):
        self.process = parse_drawio_xml(MULTIPAGE, 'avto.drawio')
        self.xml = generate_bpmn_xml(self.process)

    def _sub_node(self):
        return next(n for n in self.process.nodes if n.type == 'subProcess')

    def test_the_object_wrapper_did_not_swallow_the_shape(self):
        """`<object>` вокруг фигуры — не повод терять её вместе со связями."""
        node = self._sub_node()
        self.assertEqual(node.id, 'JAMI_t')
        self.assertEqual(node.name, "TO'LOV")
        touching = [e for e in self.process.edges
                    if node.id in (e.sourceId, e.targetId)]
        self.assertEqual(len(touching), 2, f'связи фигуры оборвались: {touching}')

    def test_the_plus_marker_becomes_a_sub_process(self):
        """`isLoopSub=1` — это и есть маркер свёрнутого подпроцесса."""
        self.assertEqual(self._sub_node().type, 'subProcess')

    def test_a_named_page_turns_the_step_into_a_call_activity(self):
        """Подпись совпала с именем страницы — значит, это вызов её процесса."""
        node = self._sub_node()
        page = next(p for p in self.process.subProcesses if p.name == "TO'LOV")
        self.assertEqual(node.calledSubProcessId, page.id)
        calls = re.findall(r'<bpmn:callActivity[^>]*calledElement="([^"]+)"', self.xml)
        self.assertEqual(len(calls), 1, f'вызов страницы не выгрузился: {self.xml[:0]}')
        self.assertIn(f'id="{calls[0]}"', self.xml,
                      'calledElement ссылается на процесс, которого нет в файле')

    def test_a_step_without_a_matching_page_stays_a_collapsed_sub_process(self):
        """Подпись ни с чем не совпала — подпроцесс остаётся свёрнутым.

        Так нарисовано в «Xizmat avtotransportini boshqarish jarayoni»: фигуры
        с плюсом там подписаны названиями работ, а не страниц, и ссылаться им
        не на что. `<bpmn:subProcess>` без содержимого — корректный BPMN, и
        импортёр рисует его той же рамкой с плюсом.
        """
        lonely = MULTIPAGE.replace('label="TO&apos;LOV"', 'label="Boshqa ish"')
        process = parse_drawio_xml(lonely, 'avto.drawio')
        node = next(n for n in process.nodes if n.type == 'subProcess')
        self.assertIsNone(node.calledSubProcessId)
        xml = generate_bpmn_xml(process)
        self.assertIn('<bpmn:subProcess', xml)
        self.assertNotIn('<bpmn:callActivity', xml)


class MultiPageExportTest(unittest.TestCase):
    """Файл выгрузки несёт процесс и схему каждой страницы."""

    def setUp(self):
        self.process = parse_drawio_xml(MULTIPAGE, 'avto.drawio')
        self.xml = generate_bpmn_xml(self.process)
        self.root = ET.fromstring(self.xml.encode('utf-8'))

    def _ids(self):
        return re.findall(r'\sid="([^"]+)"', self.xml)

    def test_every_page_has_its_own_process(self):
        """Основную карту в файле называют по процессу, страницы — по себе."""
        names = {el.get('name') for el in self.root.iter(f'{{{BPMN}}}process')}
        for page in ('SOTIB OLISH', "TO'LOV"):
            self.assertIn(page, names, f'страница «{page}» не выгрузилась процессом')
        self.assertIn(self.process.name, names, 'основная карта не выгрузилась процессом')

    def test_every_page_has_its_own_diagram(self):
        """Без своей схемы процесс страницы открывается пустым."""
        diagrams = self.xml.count('<bpmndi:BPMNDiagram ')
        self.assertEqual(diagrams, 1 + len(self.process.subProcesses),
                         f'схем в файле {diagrams}, а страниц {1 + len(self.process.subProcesses)}')

    def test_identifiers_stay_unique_across_pages(self):
        """Шаг, скопированный между страницами, приносит СВОЙ старый id."""
        ids = self._ids()
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(duplicates, [], f'повторяются идентификаторы: {duplicates}')

    def test_the_package_stays_valid(self):
        check = validate_bpmn_xml(self.xml)
        self.assertEqual([str(e) for e in check.errors], [])

    def test_reopening_the_export_shows_only_the_main_map(self):
        """Схем в файле много, а холст один: страницы не должны наложиться."""
        reopened = parse_drawio_xml(self.xml, 'avto.bpmn')
        names = [n.name for n in reopened.nodes]
        self.assertIn("TO'LOV", names, 'основная карта прочиталась не полностью')
        self.assertNotIn('Avtotransport sotib olish', names,
                         'страница-подпроцесс легла поверх основной карты')


class PmmMultiPageTest(unittest.TestCase):
    """Нативный пакет студии несёт все страницы файла на одной карте.

    Пакет с несколькими картами PIX не открывает («Sequence contains more than
    one matching element» на 18 картах «Kredit so‘ndirish…»): карта в пакете
    одна, как в собственной выгрузке студии (``tests/fixtures/sap.pmm``), а
    страницы-подпроцессы ложатся на неё одна под другой.
    """

    def setUp(self):
        self.process = parse_drawio_xml(MULTIPAGE, 'avto.drawio')
        self.package = generate_pmm_zip(self.process)
        self.archive = zipfile.ZipFile(io.BytesIO(self.package))
        self.maps = [n for n in self.archive.namelist() if n.startswith('pm/maps/')]

    def test_every_page_lands_on_the_one_map(self):
        self.assertEqual(len(self.maps), 1, self.maps)
        labels = {n.get('label') for n in ET.fromstring(self.archive.read(self.maps[0])).iter('node')}
        for page in self.process.subProcesses:
            missing = {n.name for n in page.nodes if n.name and n.type.endswith('Task')} - labels
            self.assertEqual(missing, set(), f'со страницы «{page.name}» не доехали фигуры')

    def test_pages_do_not_overlap(self):
        merged = merged_pages(self.process)
        main_ids = {n.id for n in self.process.nodes + self.process.lanes}
        pages = [[n for n in merged.nodes + merged.lanes if n.id in main_ids]]
        for index in range(1, len(self.process.subProcesses) + 1):
            pages.append([n for n in merged.nodes + merged.lanes if n.id.startswith(f'p{index}_')])
        spans = [(min(n.geometry.y for n in p), max(n.geometry.y + n.geometry.height for n in p))
                 for p in pages if p]
        for (_top, bottom), (top, _bottom) in zip(spans, spans[1:]):
            self.assertLess(bottom, top, spans)

    def test_the_main_map_comes_first_in_the_manifest(self):
        manifest = self.archive.read('main.xml').decode('utf-8')
        parts = re.findall(r'PartName="/pm/maps/([^"]+)\.xml"', manifest)
        self.assertTrue(parts, manifest)
        self.assertEqual(f'pm/maps/{parts[0]}.xml', self.maps[0] if self.maps else '')

    def test_the_manifest_declares_every_map(self):
        manifest = self.archive.read('main.xml').decode('utf-8')
        for part in self.maps:
            self.assertIn(f'PartName="/{part}"', manifest,
                          f'часть «{part}» не объявлена в main.xml')

    def test_the_package_stays_valid(self):
        check = validate_pmm_package(self.package)
        self.assertEqual([str(e) for e in check.errors], [])

    def test_a_single_page_map_keeps_one_map(self):
        """У одностраничной карты пакет прежний: лишних частей не появляется."""
        one_page = f'<mxfile host="test">{_flow("JAMI", "bank", SUB_SHAPE)}</mxfile>'
        single = parse_drawio_xml(one_page, 'one.drawio')
        self.assertEqual(single.subProcesses, [], 'у одностраничного файла нет подпроцессов')
        maps = [n for n in zipfile.ZipFile(io.BytesIO(generate_pmm_zip(single))).namelist()
                if n.startswith('pm/maps/')]
        self.assertEqual(len(maps), 1, maps)


if __name__ == '__main__':
    unittest.main()
