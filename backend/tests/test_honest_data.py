"""ИТ-системы и паспорт без выдумки: догадка помечается, заглушек нет, написания по справочнику."""
import unittest
from collections import Counter

from fastapi.testclient import TestClient

from app.main import app
from app.services.drawio_exporter import generate_drawio_xml
from app.services.drawio_parser import detect_system, parse_drawio_xml
from app.services.it_systems import canonical
from app.services.process_stats import compute_stats
from scripts.honest_data import FALLBACK_SYSTEM, OWNER_PLACEHOLDER, fix

#: Шаг без системы, шаг с системой в названии (догадка), шаг со связанным хранилищем «IABS».
MAP = '''<mxfile host="test"><diagram id="d" name="Map"><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="lane" value="Bank" style="swimlane;horizontal=0;startSize=40;" vertex="1" parent="1">
  <mxGeometry x="0" y="0" width="900" height="300" as="geometry"/></mxCell>
<mxCell id="t0" value="Arizani qabul qilish" style="rounded=1;" vertex="1" parent="lane">
  <mxGeometry x="100" y="60" width="120" height="80" as="geometry"/></mxCell>
<mxCell id="t1" value="SWIFT xabarini yuborish" style="rounded=1;" vertex="1" parent="lane">
  <mxGeometry x="300" y="60" width="120" height="80" as="geometry"/></mxCell>
<mxCell id="t2" value="Hisobni tekshirish" style="rounded=1;" vertex="1" parent="lane">
  <mxGeometry x="500" y="60" width="120" height="80" as="geometry"/></mxCell>
<mxCell id="db" value="IABS" style="shape=datastore;html=1;" vertex="1" parent="lane">
  <mxGeometry x="520" y="200" width="60" height="60" as="geometry"/></mxCell>
<mxCell id="e0" edge="1" parent="lane" source="t0" target="t1"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="e1" edge="1" parent="lane" source="t1" target="t2"><mxGeometry relative="1" as="geometry"/></mxCell>
<mxCell id="a0" style="dashed=1;endArrow=none;" edge="1" parent="lane" source="db" target="t2"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel></diagram></mxfile>'''


def steps(process):
    return {n.id: n for n in process.nodes if n.id in ('t0', 't1', 't2')}


class HonestSystemsTest(unittest.TestCase):
    def test_no_fallback_guess_is_marked_map_system_is_not(self):
        self.assertEqual(detect_system('Arizani qabul qilish', 'Bank'), '')
        got = steps(parse_drawio_xml(MAP, 'honest.drawio'))
        self.assertEqual((got['t0'].system, got['t0'].systemGuessed), (None, False))
        self.assertEqual((got['t1'].system, got['t1'].systemGuessed), ('SWIFT', True))
        # «IABS» с карты — в написании справочника и не догадка.
        self.assertEqual((got['t2'].system, got['t2'].systemGuessed), ('iABS', False))

    def test_passport_has_no_placeholders(self):
        passport = parse_drawio_xml(MAP, 'honest.drawio').passport
        self.assertEqual((passport.owner, passport.department, passport.description), ('', '', ''))

    def test_canonical_spellings(self):
        self.assertEqual(canonical('IABS, iABS (ЦФТ-Банк), Crobs'), 'iABS, CROBS')
        self.assertEqual(canonical(' ЭҲА '), 'EHA')
        self.assertEqual(canonical('Myorg.uz / ihamkor'), 'Myorg.uz / ihamkor')
        self.assertIsNone(canonical('  '))

    def test_system_set_on_the_platform_survives_drawio_and_stops_being_a_guess(self):
        process = parse_drawio_xml(MAP, 'honest.drawio')
        node = steps(process)['t1']
        node.system, node.systemGuessed = 'SWIFT, iABS', False
        back = steps(parse_drawio_xml(generate_drawio_xml(process), 'honest.drawio'))
        self.assertEqual((back['t1'].system, back['t1'].systemGuessed), ('SWIFT, iABS', False))
        # Догадку, которую никто не трогал, выгрузка не превращает в факт.
        self.assertEqual(back['t0'].system, None)

    def test_stats_count_missing_and_guessed_systems(self):
        process = parse_drawio_xml(MAP, 'honest.drawio')
        stats = compute_stats(process.nodes, process.edges, process.lanes)
        self.assertEqual((stats.noSystemStepCount, stats.guessedSystemStepCount), (1, 1))

    def test_honest_data_script_fixes_once(self):
        process = parse_drawio_xml(MAP, 'honest.drawio')
        got = steps(process)
        got['t0'].system = FALLBACK_SYSTEM
        got['t1'].system, got['t1'].systemGuessed = 'SWIFT Alliance', False
        got['t2'].system = 'IABS, Crobs'
        process.passport.owner = OWNER_PLACEHOLDER
        process.passport.department = process.lanes[0].name
        process.passport.description = 'Импортирован из файла draw.io: honest.drawio. Сформирован регламент.'
        tally = Counter()
        self.assertTrue(fix(process, tally))
        self.assertEqual((got['t0'].system, got['t0'].systemGuessed), (None, False))
        self.assertEqual((got['t1'].system, got['t1'].systemGuessed), ('SWIFT', True))
        self.assertEqual(got['t2'].system, 'iABS, CROBS')
        self.assertEqual((process.passport.owner, process.passport.department, process.passport.description),
                         ('', '', ''))
        self.assertFalse(fix(process, Counter()))

    def test_systems_directory(self):
        client = TestClient(app)
        res = client.post('/api/v1/import/file', params={'asNew': 'true'},
                          files={'file': ('honest-systems.drawio', MAP.encode(), 'application/xml')})
        self.assertEqual(res.status_code, 200, res.text)
        systems = {s['name']: s['steps'] for s in client.get('/api/v1/systems/').json()}
        self.assertGreaterEqual(systems.get('iABS', 0), 1)
        self.assertGreaterEqual(systems.get('SWIFT', 0), 1)
        self.assertNotIn('IABS', systems)
        self.assertNotIn(FALLBACK_SYSTEM, systems)


if __name__ == '__main__':
    unittest.main()
