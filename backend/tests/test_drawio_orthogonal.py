"""Перенос ``OrthConnector``/``SegmentConnector`` против ломаных самого draw.io.

Эталон — ``fixtures/drawio_orthogonal_cases.json``: 400 связей
``orthogonalEdgeStyle`` с 10 карт банка (150 с изломами аналитика и 250 без),
для которых ломаную посчитал сам draw.io (viewer-static v24.7.17). В файле
только рамки фигур, стиль связи и точки — без подписей.
"""
import json
import os
import sys
import unittest
import zipfile
import io
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.drawio_orthogonal import orthogonal_route, perimeter_kind
from app.services.drawio_parser import parse_drawio_xml
from app.services.pmm_exporter import generate_pmm_zip

CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'drawio_orthogonal_cases.json')


class DrawioOrthogonalTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(CASES, encoding='utf-8') as handle:
            cls.cases = json.load(handle)

    def _route(self, case):
        return orthogonal_route(case['style'], tuple(case['source']), tuple(case['target']),
                                case['sourceKind'], case['targetKind'], [tuple(h) for h in case['hints']])

    def _same(self, ours, theirs):
        return len(ours) == len(theirs) and all(
            abs(a[0] - b[0]) <= 0.6 and abs(a[1] - b[1]) <= 0.6 for a, b in zip(ours, theirs))

    def test_routes_without_bends_match_drawio(self):
        cases = [c for c in self.cases if not c['hints']]
        self.assertGreater(len(cases), 200)
        bad = [c for c in cases if not self._same(self._route(c), c['route'])]
        self.assertEqual(len(bad), 0, bad[:1])

    def test_routes_through_the_analysts_bends_match_drawio(self):
        cases = [c for c in self.cases if c['hints']]
        self.assertGreater(len(cases), 100)
        bad = [c for c in cases if not self._same(self._route(c), c['route'])]
        self.assertEqual(len(bad), 0, bad[:1])

    def test_perimeter_kind_follows_the_style(self):
        self.assertEqual(perimeter_kind('shape=mxgraph.bpmn.event;perimeter=ellipsePerimeter;'), 'ellipse')
        self.assertEqual(perimeter_kind('shape=mxgraph.bpmn.gateway2;perimeter=rhombusPerimeter;'), 'rhombus')
        self.assertEqual(perimeter_kind('rhombus;whiteSpace=wrap;'), 'rhombus')
        self.assertEqual(perimeter_kind('rounded=1;'), 'rectangle')


EDGE_LABEL = """<mxfile host="test"><diagram id="d" name="Map"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="900" height="300" as="geometry"/></mxCell>
  <mxCell id="a" value="A" style="rounded=1;" vertex="1" parent="lane">
    <mxGeometry x="100" y="100" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="b" value="B" style="rounded=1;" vertex="1" parent="lane">
    <mxGeometry x="600" y="100" width="120" height="80" as="geometry"/></mxCell>
  <mxCell id="e" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="a" target="b">
    <mxGeometry relative="1" as="geometry"/></mxCell>
  <mxCell id="l" value="Ichki imkoniyatdan&lt;div&gt;sport maydonini&lt;/div&gt;&lt;div&gt;ijaraga olish&lt;/div&gt;"
    style="edgeLabel;fontSize=9;html=1;" vertex="1" connectable="0" parent="e">
    <mxGeometry relative="1" x="-0.5" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>"""


class EdgeLabelLinesTest(unittest.TestCase):
    """Подпись связи едет в PIX строками и кеглем draw.io."""

    def setUp(self):
        self.process = parse_drawio_xml(EDGE_LABEL, 'label.drawio')
        package = zipfile.ZipFile(io.BytesIO(generate_pmm_zip(self.process)))
        part = next(n for n in package.namelist() if n.startswith('pm/maps/'))
        self.text = package.read(part).decode('utf-8')

    def test_model_keeps_the_lines_and_the_size(self):
        edge = next(e for e in self.process.edges if e.id == 'e')
        self.assertEqual(edge.name, 'Ichki imkoniyatdan sport maydonini ijaraga olish')
        self.assertEqual(edge.labelLines, 'Ichki imkoniyatdan\nsport maydonini\nijaraga olish')
        self.assertEqual(edge.labelFontSize, 9)

    def test_pmm_writes_line_breaks_and_font_size(self):
        self.assertIn('Text="Ichki imkoniyatdan&#10;sport maydonini&#10;ijaraga olish"', self.text)
        connector = ET.fromstring(self.text.encode('utf-8')).find('connector')
        self.assertEqual(connector.get('Text'), 'Ichki imkoniyatdan\nsport maydonini\nijaraga olish')
        self.assertEqual(connector.findtext('fontSize'), '9')


if __name__ == '__main__':
    unittest.main()
