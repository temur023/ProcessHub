"""Большая карта уходит в PIX деревом карт: главная и дочерние (``pmm_tree``)."""
import io
import unittest
import zipfile
import xml.etree.ElementTree as ET

from app.services.drawio_parser import parse_drawio_xml
from app.services.pix_spec_checker import validate_pmm_for_pix
from app.services.pmm_exporter import generate_pmm_zip
from app.services.pmm_tree import (
    CATCH_LINK,
    THROW_LINK,
    generate_pmm_package,
    generate_pmm_tree_zip,
)


def _maps(raw):
    package = zipfile.ZipFile(io.BytesIO(raw))
    return {n: ET.fromstring(package.read(n)) for n in package.namelist() if n.startswith('pm/maps/')}


def _wide_map(steps=40, key=''):
    """Одна дорожка, шаги в ряд, каждый связан со следующим; ``key`` — приставка id ячеек."""
    cells = [
        '<mxCell id="0"/><mxCell id="1" parent="0"/>',
        f'<mxCell id="{key}lane" value="Bank" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">'
        f'<mxGeometry x="0" y="0" width="{200 * steps + 200}" height="200" as="geometry"/></mxCell>',
    ]
    for i in range(steps):
        cells.append(
            f'<mxCell id="{key}t{i}" value="{i + 1}. Qadam {i + 1}" style="rounded=1;" vertex="1" '
            f'parent="{key}lane"><mxGeometry x="{100 + 200 * i}" y="60" width="120" height="80" as="geometry"/></mxCell>')
    for i in range(steps - 1):
        cells.append(
            f'<mxCell id="{key}e{i}" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="{key}lane" '
            f'source="{key}t{i}" target="{key}t{i + 1}"><mxGeometry relative="1" as="geometry"/></mxCell>')
    return ('<mxfile host="test"><diagram id="d" name="Map"><mxGraphModel><root>'
            + ''.join(cells) + '</root></mxGraphModel></diagram></mxfile>')


def _page(diagram_id, name, step):
    return (f'<diagram id="{diagram_id}" name="{name}"><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="600" height="200" as="geometry"/></mxCell>'
            f'<mxCell id="s" value="{step}" style="rounded=1;" vertex="1" parent="lane">'
            '<mxGeometry x="100" y="60" width="120" height="80" as="geometry"/></mxCell>'
            '</root></mxGraphModel></diagram>')


def _linked_pages(link_target='p2'):
    """Главная страница с фигурой-плюсом, ссылающейся на страницу «2»; страница «1» — без ссылки."""
    main = ('<diagram id="main" name="ASOSIY JARAYON"><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="900" height="200" as="geometry"/></mxCell>'
            f'<UserObject label="2. Mijozni identifikatsiyalash" link="data:page/id,{link_target}" id="sub">'
            '<mxCell style="shape=mxgraph.bpmn.task;taskMarker=abstract;isLoopSub=1;" vertex="1" parent="lane">'
            '<mxGeometry x="100" y="60" width="120" height="80" as="geometry"/></mxCell></UserObject>'
            '</root></mxGraphModel></diagram>')
    return ('<mxfile host="test">' + main + _page('p1', '1', 'Qadam A') + _page('p2', '2', 'Qadam B')
            + '</mxfile>')


class CutTest(unittest.TestCase):
    """Карта больше бюджета режется поперёк хода процесса на дочерние карты."""

    def setUp(self):
        self.maps = _maps(generate_pmm_tree_zip(parse_drawio_xml(_wide_map(), 'wide.drawio'), budget=40))
        self.roots = [m for m in self.maps.values() if not m.get('parentProcessId')]
        self.children = [m for m in self.maps.values() if m.get('parentProcessId')]

    def test_every_step_lands_on_exactly_one_child_map_within_the_budget(self):
        self.assertEqual(len(self.roots), 1)
        self.assertGreater(len(self.children), 1)
        for root in self.maps.values():
            self.assertLessEqual(sum(1 for _ in root.iter('node')), 40, root.get('name'))
        labels = [n.get('label') for m in self.children for n in m.iter('node') if n.get('type') == 'task']
        self.assertEqual(sorted(labels), sorted(f'{i + 1}. Qadam {i + 1}' for i in range(40)))

    def test_child_maps_open_from_collapsed_sub_processes_of_the_root(self):
        openers = {n.get('id') for n in self.roots[0].iter('node') if n.get('type') == 'sub_process_collapsed'}
        self.assertEqual({m.get('parentProcessId') for m in self.children}, openers)
        self.assertEqual(len(openers), len(self.children))

    def test_line_across_a_cut_ends_in_a_throw_and_starts_from_a_catch(self):
        # Шаги связаны цепочкой: каждый разрез пересекает ровно одна линия.
        throws = catches = 0
        for root in self.children:
            kinds = {n.get('id'): n.get('type') for n in root.iter('node')}
            for c in root.findall('connector'):
                if kinds.get(c.get('targetNodeId')) == THROW_LINK:
                    throws += 1
                    self.assertEqual(kinds.get(c.get('sourceNodeId')), 'task')
                if kinds.get(c.get('sourceNodeId')) == CATCH_LINK:
                    catches += 1
                    self.assertEqual(kinds.get(c.get('targetNodeId')), 'task')
        self.assertEqual(throws, len(self.children) - 1)
        self.assertEqual(catches, len(self.children) - 1)

    def test_the_package_passes_the_pix_rules(self):
        check = validate_pmm_for_pix(generate_pmm_tree_zip(
            parse_drawio_xml(_wide_map(), 'wide.drawio'), budget=40))
        self.assertTrue(check.ok, [p.message for p in check.problems])


class PageLinkTest(unittest.TestCase):
    """Страница, на которую ссылается фигура с плюсом, раскрывается этой фигурой."""

    def _root_and_children(self, drawio):
        maps = _maps(generate_pmm_tree_zip(parse_drawio_xml(drawio, 'pages.drawio')))
        root = next(m for m in maps.values() if not m.get('parentProcessId'))
        by_parent = {m.get('parentProcessId'): m for m in maps.values() if m.get('parentProcessId')}
        opened = {}
        for node in root.iter('node'):
            if node.get('type') == 'sub_process_collapsed':
                child = by_parent[node.get('id')]
                opened[node.get('label')] = [n.get('label') for n in child.iter('node') if n.get('type') == 'task']
        return opened

    def test_step_linking_a_page_opens_that_page(self):
        # Главная страница — сама главная карта; страница без ссылки — строкой ниже.
        opened = self._root_and_children(_linked_pages())
        self.assertEqual(opened['2. Mijozni identifikatsiyalash'], ['Qadam B'])
        self.assertEqual(opened['1'], ['Qadam A'])

    def test_broken_link_falls_back_to_the_step_number(self):
        # Ссылка на удалённую страницу: «2. …» раскрывает страницу «2».
        opened = self._root_and_children(_linked_pages('deleted-page'))
        self.assertEqual(opened['2. Mijozni identifikatsiyalash'], ['Qadam B'])


class LayoutTest(unittest.TestCase):
    def test_small_one_page_map_stays_one_map_under_auto(self):
        process = parse_drawio_xml(_wide_map(5), 'small.drawio')
        auto = zipfile.ZipFile(io.BytesIO(generate_pmm_package(process, 'auto')))
        single = zipfile.ZipFile(io.BytesIO(generate_pmm_zip(process)))
        self.assertEqual({n: auto.read(n) for n in auto.namelist()},
                         {n: single.read(n) for n in single.namelist()})

    def test_several_pages_go_as_a_tree_under_auto(self):
        maps = _maps(generate_pmm_package(parse_drawio_xml(_linked_pages(), 'pages.drawio'), 'auto'))
        self.assertEqual(len(maps), 3)


class TreeRulesTest(unittest.TestCase):
    """Проверка правилами PIX ловит дерево, которое студия не примет."""

    def _package(self, maps):
        """Пакет из карт ``{имя: parentProcessId}``, каждая — своя маленькая карта."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as out:
            overrides = ''.join(f'<Override PartName="/pm/maps/{name}.xml" ContentType="application/xml" />'
                                for name in maps)
            out.writestr('main.xml', f'<Types>{overrides}</Types>')
            for index, (name, parent) in enumerate(maps.items()):
                single = zipfile.ZipFile(io.BytesIO(generate_pmm_zip(
                    parse_drawio_xml(_wide_map(3, key=name), f'{name}.drawio'))))
                if index == 0:
                    out.writestr('pm/configuration.xml', single.read('pm/configuration.xml'))
                xml = single.read(next(n for n in single.namelist() if n.startswith('pm/maps/'))).decode('utf-8')
                attr = f' parentProcessId="{parent}"' if parent else ''
                xml = xml.replace('<Map ', f'<Map{attr} ', 1)
                xml = xml.replace(f'name="{ET.fromstring(xml).get("name")}"', f'name="{name}"', 1)
                out.writestr(f'pm/maps/{name}.xml', xml)
        return buf.getvalue()

    def test_two_root_maps_are_an_error(self):
        check = validate_pmm_for_pix(self._package({'a': None, 'b': None}))
        self.assertIn('pix_pmm_roots', [p.code for p in check.problems])

    def test_child_without_its_sub_process_is_an_error(self):
        check = validate_pmm_for_pix(self._package({'a': None, 'b': '00000000-0000-0000-0000-000000000000'}))
        self.assertIn('pix_pmm_parent_missing', [p.code for p in check.problems])


if __name__ == '__main__':
    unittest.main()
