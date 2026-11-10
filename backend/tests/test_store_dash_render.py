"""Пунктир «цилиндр системы ↔ шаг» — повторяет ли выгрузка линию из draw.io.

Эталон здесь сам исходник: редактор ведёт такую связь короткой ПРЯМОЙ, а при
``edgeStyle=elbowEdgeStyle`` — одним коленом. Выгрузка строила её тем же
ортогональным трассировщиком, что и поток управления, и на коротком зазоре
заготовка «ус — колено — ус» вырождалась. Отсюда три дефекта, которые видно,
открыв .bpmn в bpmn.io рядом с исходной схемой:

* линия проскакивала мимо цели на длину уса и возвращалась по себе — разворот
  на 180° там, где в draw.io прямая чёрточка;
* обход препятствий, взявшись за такой маршрут, уводил его крюком за верхнюю
  грань шага и возвращал сверху;
* в ломаной оставались отрезки в два-четыре пикселя. Сплошную линию это ещё не
  портит, пунктир — рассыпает на оторванные чёрточки: штрих в такой отрезок
  просто не попадает.

Карта в фикстуре собрана из всех способов, которыми аналитик ставит цилиндр
рядом с шагом: над шагом, сбоку, вплотную гранью к грани, наискось — и пятым
случаем, когда от прежнего положения фигур в стиле осталась контрольная точка
``elbowEdgeStyle``.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.process import Geometry, ProcessEdge, ProcessNode
from app.services.drawio_parser import parse_drawio_xml
from app.services.edge_routing import build_obstacles, orthogonal_waypoints

STORE_DASHES = """<mxGraphModel>
  <root>
    <mxCell id="0" />
    <mxCell id="1" parent="0" />

    <mxCell id="lane" value="Operatsion blok" style="swimlane;html=1;startSize=30;horizontal=0;" vertex="1" parent="1">
      <mxGeometry x="0" y="0" width="1700" height="420" as="geometry" />
    </mxCell>

    <mxCell id="task_above" value="Mijozni identifikatsiya qilish&#10;2 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="120" y="200" width="180" height="80" as="geometry" />
    </mxCell>
    <mxCell id="store_above" value="IABS" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;align=center;verticalAlign=bottom;" vertex="1" parent="lane">
      <mxGeometry x="190" y="144" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="dash_above" style="rounded=0;orthogonalLoop=1;jettySize=auto;html=1;entryX=0.5;entryY=0;entryDx=0;entryDy=0;entryPerimeter=0;dashed=1;" edge="1" parent="lane" source="store_above" target="task_above">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>

    <mxCell id="task_side" value="Hujjatlarni skaner qilish&#10;3 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="460" y="200" width="180" height="80" as="geometry" />
    </mxCell>
    <mxCell id="store_side" value="EHA" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;align=center;verticalAlign=bottom;" vertex="1" parent="lane">
      <mxGeometry x="404" y="210" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="dash_side" style="rounded=0;orthogonalLoop=1;jettySize=auto;html=1;entryX=0;entryY=0.25;entryDx=0;entryDy=0;entryPerimeter=0;dashed=1;" edge="1" parent="lane" source="store_side" target="task_side">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>

    <mxCell id="task_touch" value="Shartnomaga muhr bosish&#10;1 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="800" y="200" width="180" height="80" as="geometry" />
    </mxCell>
    <mxCell id="store_touch" value="EDO" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;align=center;verticalAlign=bottom;" vertex="1" parent="lane">
      <mxGeometry x="790" y="160" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="dash_touch" style="rounded=0;orthogonalLoop=1;jettySize=auto;html=1;exitX=0.958;exitY=0.8;exitDx=0;exitDy=0;exitPerimeter=0;entryX=0.002;entryY=0.089;entryDx=0;entryDy=0;entryPerimeter=0;dashed=1;" edge="1" parent="lane" source="store_touch" target="task_touch">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>

    <mxCell id="task_skew" value="Bank komissiyasini tanlash&#10;1 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="1150" y="230" width="180" height="80" as="geometry" />
    </mxCell>
    <mxCell id="store_skew" value="IABS" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;align=center;verticalAlign=bottom;" vertex="1" parent="lane">
      <mxGeometry x="1102" y="174" width="40" height="40" as="geometry" />
    </mxCell>
    <mxCell id="dash_skew" style="rounded=0;orthogonalLoop=1;jettySize=auto;html=1;entryX=0;entryY=0.25;entryDx=0;entryDy=0;entryPerimeter=0;dashed=1;" edge="1" parent="lane" source="store_skew" target="task_skew">
      <mxGeometry relative="1" as="geometry" />
    </mxCell>

    <mxCell id="task_elbow" value="Xatni imzolash&#10;1 min" style="shape=mxgraph.bpmn.task2;html=1;" vertex="1" parent="lane">
      <mxGeometry x="1420" y="230" width="180" height="80" as="geometry" />
    </mxCell>
    <mxCell id="store_elbow" value="EHA" style="shape=datastore;html=1;labelPosition=center;verticalLabelPosition=top;align=center;verticalAlign=bottom;" vertex="1" parent="lane">
      <mxGeometry x="1376" y="180" width="30" height="20" as="geometry" />
    </mxCell>
    <mxCell id="dash_elbow" style="edgeStyle=elbowEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;exitX=0.5;exitY=1;exitDx=0;exitDy=0;entryX=0;entryY=0.25;entryDx=0;entryDy=0;entryPerimeter=0;dashed=1;" edge="1" parent="lane" source="store_elbow" target="task_elbow">
      <mxGeometry relative="1" as="geometry">
        <Array as="points">
          <mxPoint x="1391" y="222" />
          <mxPoint x="1424" y="157" />
        </Array>
      </mxGeometry>
    </mxCell>
  </root>
</mxGraphModel>"""

DASHES = ('dash_above', 'dash_side', 'dash_touch', 'dash_skew', 'dash_elbow')


class StoreDashTest(unittest.TestCase):
    #: Короче этого отрезок пунктиром уже не читается, px. Столько же считает
    #: микроизломом сама трассировка (``MIN_SEGMENT``).
    MIN_LEG = 8

    def setUp(self):
        self.process = parse_drawio_xml(STORE_DASHES, 'stores.drawio')
        self.nodes = {n.id: n for n in self.process.nodes}
        self.obstacles = build_obstacles(self.process.nodes)

    def _route(self, edge_id):
        edge = next(e for e in self.process.edges if e.id == edge_id)
        src, tgt = self.nodes[edge.sourceId], self.nodes[edge.targetId]
        return orthogonal_waypoints(edge, src, tgt, None, self.obstacles)

    def _all(self):
        return {eid: self._route(eid) for eid in DASHES}

    def test_every_dash_survived_the_import(self):
        """Без связей проверять было бы нечего."""
        kinds = {e.id: e.kind for e in self.process.edges}
        for eid in DASHES:
            self.assertEqual(kinds.get(eid), 'association', f'{eid} не стал ассоциацией')

    def test_no_dash_turns_back_on_itself(self):
        """Разворот на 180°: линия идёт вперёд и возвращается по себе же."""
        for eid, route in self._all().items():
            for a, b, c in zip(route, route[1:], route[2:]):
                first = (b[0] - a[0], b[1] - a[1])
                second = (c[0] - b[0], c[1] - b[1])
                if first == (0, 0) or second == (0, 0):
                    continue
                self.assertGreaterEqual(
                    first[0] * second[0] + first[1] * second[1], 0,
                    f'{eid}: ломаная разворачивается назад {route}')

    def test_no_dash_leaves_the_pair_of_shapes(self):
        """Линия не делает крюк за рамку, общую для цилиндра и шага."""
        for eid, route in self._all().items():
            edge = next(e for e in self.process.edges if e.id == eid)
            src = self.nodes[edge.sourceId].geometry
            tgt = self.nodes[edge.targetId].geometry
            lo_x = min(src.x, tgt.x) - 8
            hi_x = max(src.x + src.width, tgt.x + tgt.width) + 8
            lo_y = min(src.y, tgt.y) - 8
            hi_y = max(src.y + src.height, tgt.y + tgt.height) + 8
            for x, y in route:
                self.assertTrue(
                    lo_x <= x <= hi_x and lo_y <= y <= hi_y,
                    f'{eid}: точка ({x}, {y}) вне рамки фигур, ломаная {route}')

    def test_no_dash_has_a_leg_too_short_to_be_dashed(self):
        for eid, route in self._all().items():
            legs = [max(abs(b[0] - a[0]), abs(b[1] - a[1])) for a, b in zip(route, route[1:])]
            self.assertTrue(
                all(leg >= self.MIN_LEG for leg in legs),
                f'{eid}: отрезки {legs} короче {self.MIN_LEG} px — пунктир рассыплется')

    def test_a_store_over_a_step_is_linked_by_one_straight_line(self):
        """Цилиндр над шагом — короткая вертикаль вниз, как в draw.io."""
        route = self._route('dash_above')
        self.assertEqual(len(route), 2, f'вместо прямой ломаная {route}')
        self.assertEqual(route[0][0], route[1][0], f'вертикаль перекошена: {route}')

    def test_a_store_beside_a_step_is_linked_by_one_straight_line(self):
        """Цилиндр сбоку — короткая горизонталь, а не обход поверху."""
        route = self._route('dash_side')
        self.assertEqual(len(route), 2, f'вместо прямой ломаная {route}')
        self.assertEqual(route[0][1], route[1][1], f'горизонталь перекошена: {route}')

    def test_an_elbow_dash_keeps_a_single_bend(self):
        """У ``elbowEdgeStyle`` точки в файле — подсказка, а не изломы маршрута.

        mxGraph двигает такой точкой единственное колено и проходить через неё
        не обязан. Пока ломаная шла через все точки подряд, из одного колена
        получалась лесенка в четыре отрезка.
        """
        route = self._route('dash_elbow')
        self.assertLessEqual(len(route), 3, f'вместо колена лесенка {route}')


class TouchingStoreDashTest(unittest.TestCase):
    """Цилиндр стоит гранью к грани шага — линии не остаётся длины.

    Геометрия взята с карты «Asosiy hisob raqam ochish yangi»: цилиндр «EHA»
    нижней гранью касается верхней грани шага «Xizmat yozishmani yuborish».
    Правило «цилиндр над шагом — короткая вертикаль вниз» сводило оба причала
    в одну точку, и заготовка маршрута складывалась в разворот на 180°: линия
    уходила на ус вниз, возвращалась на два уса вверх и снова спускалась.

    У такой пары единственное, что ещё описывает видимую линию, — причалы,
    расставленные аналитиком: draw.io рисует по ним короткую наклонную
    чёрточку через общую грань.
    """

    def setUp(self):
        self.store = ProcessNode(
            id='store', name='EHA', type='dataStore',
            geometry=Geometry(x=1520, y=1659, width=40, height=40))
        self.task = ProcessNode(
            id='task', name='Xizmat yozishmani yuborish', type='userTask',
            geometry=Geometry(x=1550, y=1699, width=170, height=80))
        self.edge = ProcessEdge(
            id='dash', name='', kind='association', sourceId='store', targetId='task',
            points=[], exitX=0.958, exitY=0.8, entryX=0.002, entryY=0.089,
            dashed=True, style='dashed=1;')
        self.obstacles = build_obstacles([self.store, self.task])

    def _route(self):
        return orthogonal_waypoints(
            self.edge, self.store, self.task, None, self.obstacles)

    def test_the_faces_really_touch(self):
        """Без соприкосновения проверять было бы нечего."""
        self.assertEqual(
            self.store.geometry.y + self.store.geometry.height, self.task.geometry.y)

    def test_the_dash_does_not_fold_back_on_itself(self):
        route = self._route()
        for a, b, c in zip(route, route[1:], route[2:]):
            first = (b[0] - a[0], b[1] - a[1])
            second = (c[0] - b[0], c[1] - b[1])
            if first == (0, 0) or second == (0, 0):
                continue
            self.assertGreaterEqual(
                first[0] * second[0] + first[1] * second[1], 0,
                f'ломаная разворачивается назад {route}')

    def test_the_dash_stays_visible(self):
        """Линия нулевой длины на карте не видна вовсе."""
        route = self._route()
        length = sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(route, route[1:]))
        self.assertGreater(length, StoreDashTest.MIN_LEG, f'пунктир выродился в точку {route}')

    def test_the_dash_keeps_the_anchors_of_the_analyst(self):
        """Причалы аналитика — единственное, что здесь описывает линию."""
        route = self._route()
        self.assertEqual(len(route), 2, f'вместо прямой ломаная {route}')
        self.assertAlmostEqual(route[0][0], 1520 + 0.958 * 40, delta=1)
        self.assertAlmostEqual(route[-1][1], 1699 + 0.089 * 80, delta=1)


if __name__ == '__main__':
    unittest.main()
