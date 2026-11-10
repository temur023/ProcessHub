"""Склейка частей большой карты из PIX обратно в одну карту (``pmm_glue``)."""
import unittest
import xml.etree.ElementTree as ET

from app.services import pmm_glue
from app.services.drawio_parser import parse_drawio_xml
from app.services.pmm_tree import CATCH_LINK, THROW_LINK, generate_pmm_tree_zip
from tests.test_pmm_tree import _wide_map


def _lane(label, x, w, y=0, h=200):
    el = ET.Element('node', {'type': 'horizontalRoad', 'label': label})
    return pmm_glue.Shape(el, f'{label}@{x}', 'horizontalRoad', label, x, y, w, h)


class GlueTest(unittest.TestCase):
    def test_cut_map_glues_back_into_one(self):
        package = generate_pmm_tree_zip(parse_drawio_xml(_wide_map(), 'wide.drawio'), budget=40)
        maps, reports = pmm_glue.glue_package(package)
        self.assertEqual(len(maps), 1)
        self.assertGreater(reports[0].parts, 1)
        self.assertEqual(reports[0].problems, [])
        glued = ET.fromstring(maps[0][1].encode('utf-8'))
        kinds = {n.get('id'): n.get('type') for n in glued.iter('node')}
        labels = {n.get('id'): n.get('label') for n in glued.iter('node')}
        self.assertEqual(sorted(t for t in kinds.values() if t != 'horizontalRoad'), ['task'] * 40)
        self.assertNotIn(THROW_LINK, kinds.values())
        self.assertNotIn(CATCH_LINK, kinds.values())
        # Цепочка шагов снова цела: 1 → 2 → … → 40, одна дорожка на всех.
        steps = sorted((labels[c.get('sourceNodeId')], labels[c.get('targetNodeId')])
                       for c in glued.findall('connector'))
        self.assertEqual(steps, sorted((f'{i}. Qadam {i}', f'{i + 1}. Qadam {i + 1}') for i in range(1, 40)))
        self.assertEqual(list(kinds.values()).count('horizontalRoad'), 1)

    def test_lane_copy_at_the_edge_returns_to_its_place(self):
        # Края частей заходят друг за друга; «Mijoz» нарисован в части 1, в части 2 — его копия у края.
        first = pmm_glue.Part('1', 'p', 1, 2, [_lane('Bank', 0, 1000), _lane('Mijoz', 400, 300, 200)], [], [])
        second = pmm_glue.Part('2', 'p', 2, 2, [_lane('Bank', 950, 1050), _lane('Mijoz (часть 1)', 950, 300, 200)],
                               [], [])
        lanes, owner, problems = pmm_glue._merge_lanes([first, second])
        self.assertEqual(problems, [])
        self.assertEqual([(l.label, l.x, l.w) for l in lanes], [('Bank', 0, 2000), ('Mijoz', 400, 300)])
        self.assertEqual(owner['Mijoz (часть 1)@950'], owner['Mijoz@400'])


if __name__ == '__main__':
    unittest.main()
