"""Связи у шлюза и события кончаются на их контуре, а не в пустом углу рамки.

На карте «1.53» в шлюз «Imzolamdimi?» входили три связи слева. Разведение
причалов сдвигало одну из них вдоль рамки на 20 px вниз, и она обрывалась в
углу вокруг ромба — стрелка не касалась шлюза. По всем картам банка так висели
721 конец из 13 642. В draw.io такие связи сходятся в вершине ромба, а любой
конец продлевается до контура (``rhombusPerimeter`` / ``ellipsePerimeter``).
"""
import json
import math
import os
import unittest

from app.models.process import CanvasPage
from app.services.bpmn_exporter import canvas_layout
from app.services.canvas_cache import as_process
from app.services.drawio_parser import parse_drawio_xml

GATEWAY_STYLE = ('points=[[0.25,0.25,0],[0.5,0,0],[0.75,0.25,0],[1,0.5,0],[0.75,0.75,0],[0.5,1,0],'
                 '[0.25,0.75,0],[0,0.5,0]];shape=mxgraph.bpmn.gateway2;perimeter=rhombusPerimeter;gwType=exclusive;')
MAP = f'''<mxfile host="test"><diagram id="d" name="Map"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
  <mxGeometry x="0" y="0" width="900" height="460" as="geometry"/></mxCell>
<mxCell id="a" value="Kelishish" style="rounded=1;" vertex="1" parent="lane"><mxGeometry x="100" y="60" width="120" height="80" as="geometry"/></mxCell>
<mxCell id="b" value="Imzolash" style="rounded=1;" vertex="1" parent="lane"><mxGeometry x="100" y="200" width="120" height="80" as="geometry"/></mxCell>
<mxCell id="c" value="Tekshirish" style="rounded=1;" vertex="1" parent="lane"><mxGeometry x="100" y="340" width="120" height="80" as="geometry"/></mxCell>
<mxCell id="g" value="Imzolamdimi?" style="{GATEWAY_STYLE}" vertex="1" parent="lane"><mxGeometry x="400" y="75" width="50" height="50" as="geometry"/></mxCell>
<mxCell id="e" value="" style="shape=mxgraph.bpmn.event;symbol=terminate2;outline=end;perimeter=ellipsePerimeter;" vertex="1" parent="lane">
  <mxGeometry x="600" y="82" width="36" height="36" as="geometry"/></mxCell>
<mxCell id="f1" style="edgeStyle=orthogonalEdgeStyle;entryX=0;entryY=0.5;" edge="1" parent="lane" source="a" target="g"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="f2" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane" source="b" target="g"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="f3" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane" source="c" target="g"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="f4" value="Ha" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="lane" source="g" target="e"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>'''


class GatewayOutlineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = parse_drawio_xml(MAP, 'outline.drawio')
        cls.routes = canvas_layout(cls.process).routes
        cls.nodes = {n.id: n for n in cls.process.nodes}

    def box(self, node_id):
        g = self.nodes[node_id].geometry
        return g.x + g.width / 2, g.y + g.height / 2, g.width / 2, g.height / 2

    def test_every_line_into_the_gateway_touches_the_diamond(self):
        cx, cy, hw, hh = self.box('g')
        ends = [tuple(self.routes[e][-1]) for e in ('f1', 'f2', 'f3')]
        for x, y in ends:
            self.assertAlmostEqual(abs(x - cx) / hw + abs(y - cy) / hh, 1.0, delta=0.05, msg=(x, y))
        # Как в draw.io: входящие слева сходятся в левой вершине, а не расходятся по рамке.
        self.assertEqual(ends[0], (cx - hw, cy))
        self.assertEqual(ends[1], (cx - hw, cy))

    def test_line_out_of_the_gateway_reaches_the_event_circle(self):
        cx, cy, hw, hh = self.box('e')
        x, y = self.routes['f4'][-1]
        self.assertAlmostEqual(math.hypot((x - cx) / hw, (y - cy) / hh), 1.0, delta=0.06)
        gx, gy, ghw, ghh = self.box('g')
        sx, sy = self.routes['f4'][0]
        self.assertAlmostEqual(abs(sx - gx) / ghw + abs(sy - gy) / ghh, 1.0, delta=0.05)


# Фрагмент карты «1.53» у шлюза «Imzolamdimi?»: правый край шага «344» на
# высоте 1601, вершина шлюза — на 1600, и в ту же вершину входят ещё три связи.
# Связь кончалась вертикальным хвостиком в пиксель, и стрелка у шлюза смотрела
# вверх (по всем картам банка — 161 связь). На простой карте трассировщик
# выпрямляет такую связь сам, поэтому фрагмент — настоящий.
FRAGMENT = os.path.join(os.path.dirname(__file__), 'fixtures', 'imzolamdimi_fragment.json')


class GatewayEntryJogTest(unittest.TestCase):
    def test_line_enters_the_vertex_horizontally(self):
        with open(FRAGMENT, encoding='utf-8') as f:
            page = CanvasPage.model_validate(json.load(f))
        route = canvas_layout(as_process(page)).routes['THhvWE7dPTGhgFkc9Dn9-361']
        (bx, by), (cx, cy) = route[-2], route[-1]
        self.assertEqual((cx, cy), (34255, 1600), route)   # левая вершина шлюза
        # Наконечник ложится по последнему отрезку: он идёт слева направо.
        self.assertEqual(by, cy, route)
        self.assertLess(bx, cx, route)


if __name__ == '__main__':
    unittest.main()
