"""Чтение готовой выгрузки: пакет `.pmm` и эндпоинт просмотра.

Читателя `.pmm` на бэкенде не было вовсе: единственный жил в браузере
(`app/src/lib/pmm-import.ts`) и был последним куском предметной логики, ради
которого фронт держал собственный разбор. Логика переехала сюда, а вместе с ней
вскрылся дефект, который клиентский читатель нёс молча.

Значок длительности экспортёр пишет непрерывающим граничным событием
(`boundary_non_interrupting_event_timer`) — такого типа читатель не знал, и
значки превращались в шаги: на «overnight» карта приезжала с 78 шагами вместо
40, а время у шагов при этом пропадало.

Просмотр идёт отдельным эндпоинтом `/import/preview`, а не обычным импортом, по
двум причинам: тот нормализует геометрию (для сверки выгрузки нужен файл без
поправок) и кладёт процесс в хранилище (просмотр — разовая сверка, засорять
список процессов копиями собственных файлов незачем).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from fastapi.testclient import TestClient

from app.main import app
from app.models.process import TASK_NODE_TYPES
from app.services.drawio_parser import parse_drawio_xml
from app.services.pmm_exporter import generate_pmm_zip
from app.services.pmm_importer import PmmError, package_parts, parse_pmm_package

# Карта с дорожкой, двумя шагами и временем у каждого: значки длительности
# экспортёр допишет сам — именно на них и споткнулся прежний читатель.
SOURCE_MAP = """<mxGraphModel>
  <root>
    <mxCell id="0" /><mxCell id="1" parent="0" />
    <mxCell id="lane" value="Buxgalteriya" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="900" height="220" as="geometry" />
    </mxCell>
    <mxCell id="s" value="Boshlanish" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=standard;" vertex="1" parent="lane">
      <mxGeometry x="40" y="90" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="t1" value="Hisobni tekshirish&#10;20 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="160" y="70" width="180" height="80" as="geometry" />
    </mxCell>
    <mxCell id="t2" value="To&apos;lovni amalga oshirish&#10;2 soat" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="420" y="70" width="180" height="80" as="geometry" />
    </mxCell>
    <mxCell id="e" value="Tugadi" style="shape=mxgraph.bpmn.event;html=1;symbol=general;outline=end;" vertex="1" parent="lane">
      <mxGeometry x="680" y="90" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="store" value="IABS" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;align=center;verticalAlign=bottom;" vertex="1" parent="lane">
      <mxGeometry x="230" y="14" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="dash" style="rounded=0;orthogonalLoop=1;jettySize=auto;html=1;entryX=0.5;entryY=0;entryDx=0;entryDy=0;entryPerimeter=0;dashed=1;" edge="1" parent="lane" source="store" target="t1">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>
    <mxCell id="f1" style="html=1;" edge="1" parent="lane" source="s" target="t1"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f2" value="Ha" style="html=1;" edge="1" parent="lane" source="t1" target="t2"><mxGeometry relative="1" as="geometry" /></mxCell>
    <mxCell id="f3" style="html=1;" edge="1" parent="lane" source="t2" target="e"><mxGeometry relative="1" as="geometry" /></mxCell>
  </root>
</mxGraphModel>"""


def _steps(process):
    return [n for n in process.nodes if n.type in TASK_NODE_TYPES]


class PmmRoundTripTest(unittest.TestCase):
    """Собственный пакет платформа обязана прочитать обратно."""

    def setUp(self):
        self.source = parse_drawio_xml(SOURCE_MAP, 'source.drawio')
        self.package = generate_pmm_zip(self.source)
        self.opened, self.map_xml, self.parts = parse_pmm_package(self.package, 'source.pmm')

    def test_the_package_is_a_pix_bundle(self):
        """Без карты внутри пакета читать было бы нечего."""
        self.assertIn('main.xml', self.parts)
        self.assertTrue(any(p.startswith('pm/maps/') for p in self.parts), self.parts)
        self.assertIn('<Map', self.map_xml)

    def test_steps_do_not_multiply(self):
        """Значки длительности — не шаги.

        Прежний читатель не знал типа, которым экспортёр их пишет, и каждый
        значок приезжал обратно отдельным шагом.
        """
        self.assertEqual(len(_steps(self.opened)), len(_steps(self.source)))

    def test_step_time_comes_back(self):
        """Время шага живёт в значке: не разобрав значок, его теряешь."""
        timed = {n.name: n.slaMinutes for n in _steps(self.opened) if (n.slaMinutes or 0) > 0}
        self.assertEqual(len(timed), 2, f'время вернулось не у всех шагов: {timed}')
        self.assertEqual(timed.get('Hisobni tekshirish'), 20)
        self.assertEqual(timed.get("To'lovni amalga oshirish"), 120)

    def test_lanes_and_links_survive(self):
        self.assertEqual([l.name for l in self.opened.lanes], [l.name for l in self.source.lanes])
        kinds = {e.kind for e in self.opened.edges}
        self.assertIn('sequenceFlow', kinds)
        self.assertIn('association', kinds, 'пунктир к хранилищу потерян')

    def test_branch_label_survives(self):
        """Подпись связи в .pmm лежит в атрибуте `Text`, а не `label`."""
        named = [e.name for e in self.opened.edges if e.name]
        self.assertIn('Ha', named, f'подписи связей: {named}')

    def test_geometry_is_not_normalised(self):
        """Просмотр показывает файл как есть — иначе сверять его незачем."""
        opened_lane = self.opened.lanes[0].geometry
        source_lane = self.source.lanes[0].geometry
        self.assertEqual((opened_lane.width, opened_lane.height),
                         (source_lane.width, source_lane.height))

    def test_a_broken_package_is_named_as_such(self):
        with self.assertRaises(PmmError):
            parse_pmm_package(b'not a zip at all', 'broken.pmm')
        self.assertEqual(package_parts(b'not a zip at all'), [])


class ExportPreviewEndpointTest(unittest.TestCase):
    """`/import/preview` открывает готовый файл и не трогает хранилище."""

    def setUp(self):
        self.client = TestClient(app)
        self.source = parse_drawio_xml(SOURCE_MAP, 'source.drawio')

    def _store_size(self) -> int:
        return len(self.client.get('/api/v1/processes/').json())

    def test_pmm_package_opens(self):
        package = generate_pmm_zip(self.source)
        before = self._store_size()
        res = self.client.post(
            '/api/v1/import/preview',
            files={'file': ('map.pmm', package, 'application/zip')},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body['format'], 'pmm')
        self.assertTrue(body['parts'], 'состав пакета не показан')
        self.assertEqual(
            len([n for n in body['process']['nodes'] if n['type'] in TASK_NODE_TYPES]),
            len(_steps(self.source)),
        )
        self.assertEqual(self._store_size(), before, 'просмотр засорил список процессов')

    def test_bpmn_file_opens(self):
        from app.services.bpmn_exporter import generate_bpmn_xml
        xml = generate_bpmn_xml(self.source)
        before = self._store_size()
        res = self.client.post(
            '/api/v1/import/preview',
            files={'file': ('map.bpmn', xml.encode('utf-8'), 'application/xml')},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body['format'], 'bpmn')
        self.assertTrue(body['process']['nodes'])
        self.assertEqual(self._store_size(), before, 'просмотр засорил список процессов')

    def test_a_foreign_format_is_refused(self):
        res = self.client.post(
            '/api/v1/import/preview',
            files={'file': ('map.drawio', SOURCE_MAP.encode('utf-8'), 'application/xml')},
        )
        self.assertEqual(res.status_code, 400, res.text)

    def test_a_broken_package_is_refused_with_a_reason(self):
        res = self.client.post(
            '/api/v1/import/preview',
            files={'file': ('map.pmm', b'not a zip', 'application/zip')},
        )
        self.assertEqual(res.status_code, 422, res.text)
        self.assertIn('.pmm', res.json()['detail'])


if __name__ == '__main__':
    unittest.main()
