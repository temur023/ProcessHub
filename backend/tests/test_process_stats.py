"""Время и деньги карты: трудоёмкость, длительность пути, себестоимость по ставкам дорожек."""
import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.models.process import Geometry, ProcessEdge, ProcessNode
from app.services.process_stats import (
    DEFAULT_HOURLY_RATE,
    apply_costs,
    compute_stats,
)


def node(nid, type_='userTask', sla=0, wait=0, lane=None, category='manual', **kw):
    return ProcessNode(
        id=nid, name=nid, type=type_, category=category, laneId=lane,
        slaMinutes=sla, waitMinutes=wait, geometry=Geometry(), **kw,
    )


def lane(lid, rate=None):
    return ProcessNode(id=lid, name=f'Дорожка {lid}', type='lane', hourlyRate=rate, geometry=Geometry())


def flow(src, tgt, kind='sequenceFlow'):
    return ProcessEdge(id=f'{src}-{tgt}', sourceId=src, targetId=tgt, kind=kind)


class TestTime(unittest.TestCase):
    def test_sums_work_and_wait_over_steps_only(self):
        nodes = [
            node('s', 'startEvent', sla=30),  # модель ставит 30 любой фигуре — не шаг
            node('a', sla=10, wait=5),
            node('b', sla=20, wait=0),
            node('e', 'endEvent', sla=30),
        ]
        stats = compute_stats(nodes, [flow('s', 'a'), flow('a', 'b'), flow('b', 'e')], [])
        self.assertEqual(stats.stepCount, 2)
        self.assertEqual(stats.workMinutes, 30)
        self.assertEqual(stats.waitMinutes, 5)
        self.assertEqual(stats.pathMinutes, 35)
        self.assertEqual(stats.pathNodeIds, ['s', 'a', 'b', 'e'])
        self.assertEqual(stats.pathStepCount, 2)

    def test_xor_takes_the_longest_branch_not_the_sum(self):
        nodes = [
            node('s', 'startEvent'), node('g', 'exclusiveGateway'),
            node('fast', sla=10), node('slow', sla=90),
            node('j', 'exclusiveGateway'), node('e', 'endEvent'),
        ]
        edges = [flow('s', 'g'), flow('g', 'fast'), flow('g', 'slow'),
                 flow('fast', 'j'), flow('slow', 'j'), flow('j', 'e')]
        stats = compute_stats(nodes, edges, [])
        self.assertEqual(stats.workMinutes, 100)
        self.assertEqual(stats.pathMinutes, 90)
        self.assertIn('slow', stats.pathNodeIds)
        self.assertNotIn('fast', stats.pathNodeIds)

    def test_parallel_takes_the_longest_simultaneous_branch(self):
        nodes = [
            node('s', 'startEvent'), node('a', sla=5), node('fork', 'parallelGateway'),
            node('p1', sla=40), node('p2', sla=15, wait=30),
            node('join', 'parallelGateway'), node('z', sla=5), node('e', 'endEvent'),
        ]
        edges = [flow('s', 'a'), flow('a', 'fork'), flow('fork', 'p1'), flow('fork', 'p2'),
                 flow('p1', 'join'), flow('p2', 'join'), flow('join', 'z'), flow('z', 'e')]
        stats = compute_stats(nodes, edges, [])
        self.assertEqual(stats.pathMinutes, 5 + 45 + 5)
        self.assertIn('p2', stats.pathNodeIds)

    def test_rework_loop_is_walked_once(self):
        nodes = [node('s', 'startEvent'), node('a', sla=10), node('check', sla=20),
                 node('g', 'exclusiveGateway'), node('e', 'endEvent')]
        edges = [flow('s', 'a'), flow('a', 'check'), flow('check', 'g'),
                 flow('g', 'a'),  # вернуть на доработку
                 flow('g', 'e')]
        stats = compute_stats(nodes, edges, [])
        self.assertEqual(stats.pathMinutes, 30)
        self.assertEqual(stats.pathNodeIds.count('a'), 1)

    def test_long_chain_does_not_hit_recursion_limit(self):
        n = 3000
        nodes = [node('s', 'startEvent')] + [node(f't{i}', sla=1) for i in range(n)]
        edges = [flow('s', 't0')] + [flow(f't{i}', f't{i + 1}') for i in range(n - 1)]
        self.assertEqual(compute_stats(nodes, edges, []).pathMinutes, n)

    def test_without_start_event_path_starts_at_shapes_without_inputs(self):
        nodes = [node('a', sla=10), node('b', sla=15)]
        self.assertEqual(compute_stats(nodes, [flow('a', 'b')], []).pathMinutes, 25)

    def test_message_flows_and_associations_are_not_the_path(self):
        nodes = [node('s', 'startEvent'), node('a', sla=10), node('x', sla=500),
                 node('db', 'dataStore')]
        edges = [flow('s', 'a'), flow('a', 'x', kind='messageFlow'), flow('a', 'db', kind='association')]
        self.assertEqual(compute_stats(nodes, edges, []).pathMinutes, 10)

    def test_timer_counts_only_when_its_time_is_written_on_the_map(self):
        nodes = [node('s', 'startEvent'),
                 node('t1', 'intermediateTimerEvent', sla=30),  # условные 30 минут модели
                 node('t2', 'intermediateTimerEvent', sla=1440, slaText='1 kun'),
                 node('e', 'endEvent')]
        edges = [flow('s', 't1'), flow('t1', 't2'), flow('t2', 'e')]
        stats = compute_stats(nodes, edges, [])
        self.assertEqual(stats.waitMinutes, 1440)
        self.assertEqual(stats.pathMinutes, 1440)

    def test_unmeasured_steps_are_counted(self):
        nodes = [node('a', sla=30), node('b', sla=30, slaMeasured=True)]
        self.assertEqual(compute_stats(nodes, [], []).unmeasuredStepCount, 1)


class TestMoney(unittest.TestCase):
    def test_default_rate_is_the_import_rate(self):
        self.assertEqual(DEFAULT_HOURLY_RATE, 1932 * 60)
        stats = compute_stats([node('a', sla=30)], [], [])
        self.assertEqual(stats.costUzs, 30 * 1932)

    def test_cost_is_time_times_lane_rate_and_robot_runs_at_its_price(self):
        lanes = [lane('L1', rate=60_000), lane('L2')]
        nodes = [
            node('a', sla=30, lane='L1'),                        # 30 мин × 60 000 / ч
            node('b', sla=60, wait=600, lane='L2'),              # ожидание денег не стоит
            node('bot', 'serviceTask', sla=5, lane='L1', category='rpa_bot', costPerExecution=800),
            node('free', sla=10),                                # вне дорожек
        ]
        stats = compute_stats(nodes, [], lanes)
        self.assertEqual(stats.costUzs, 30_000 + 60 * 1932 + 800 + 10 * 1932)
        rows = {r.laneId: r for r in stats.lanes}
        self.assertEqual(rows['L1'].costUzs, 30_800)
        self.assertEqual(rows['L1'].hourlyRate, 60_000)
        self.assertFalse(rows['L1'].rateIsDefault)
        self.assertEqual(rows['L1'].stepCount, 2)
        self.assertTrue(rows['L2'].rateIsDefault)
        self.assertEqual(rows['L2'].waitMinutes, 600)
        self.assertEqual(rows[None].laneName, 'Без дорожки')
        self.assertEqual(rows[None].costUzs, 10 * 1932)

    def test_empty_lane_is_listed_and_empty_no_lane_row_is_not(self):
        stats = compute_stats([node('a', sla=10, lane='L1')], [], [lane('L1'), lane('L2')])
        self.assertEqual([r.laneId for r in stats.lanes], ['L1', 'L2'])

    def test_apply_costs_rewrites_human_steps_and_keeps_robot_price(self):
        lanes = [lane('L1', rate=90_000)]
        nodes = [node('a', sla=20, lane='L1', costPerExecution=1),
                 node('bot', 'serviceTask', sla=5, lane='L1', category='rpa_bot', costPerExecution=800),
                 node('g', 'exclusiveGateway', costPerExecution=5000)]
        apply_costs(nodes, lanes)
        self.assertEqual(nodes[0].costPerExecution, 30_000)
        self.assertEqual(nodes[1].costPerExecution, 800)
        self.assertEqual(nodes[2].costPerExecution, 5000)


class TestHttp(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_endpoint_returns_the_service_numbers(self):
        lanes = [lane('L1', rate=60_000)]
        nodes = [node('s', 'startEvent'), node('a', sla=30, wait=10, lane='L1'), node('e', 'endEvent')]
        edges = [flow('s', 'a'), flow('a', 'e')]
        page = {
            'nodes': [n.model_dump() for n in nodes],
            'edges': [e.model_dump() for e in edges],
            'lanes': [l.model_dump() for l in lanes],
        }
        res = self.client.post('/api/v1/canvas/stats', json=page)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), compute_stats(nodes, edges, lanes).model_dump())
        self.assertEqual(res.json()['pathMinutes'], 40)
        self.assertEqual(res.json()['costUzs'], 30_000)

    def test_saving_a_map_recomputes_step_cost_from_lane_rate(self):
        from app.models.process import (BusinessProcess, PixRegistrySchema,
                                        ProcessPassport, ProcessetMiningMetrics)
        process = BusinessProcess(
            id='proc-stats-save', name='Ставки', fileName='x.drawio',
            passport=ProcessPassport(code='P', name='Ставки', version='1', status='draft', owner='o',
                                     department='d', category='c', targetSlaHours=1, description='',
                                     createdDate='2026-10-04', updatedDate='2026-10-04'),
            nodes=[node('a', sla=45, lane='L1', costPerExecution=5000)],
            edges=[], lanes=[lane('L1', rate=80_000)],
            validation=[],
            registry=PixRegistrySchema(id='r', name='r', code='r', description='', fields=[], records=[]),
            miningMetrics=ProcessetMiningMetrics(totalCases=0, conformanceRate=0, avgLeadTimeHours=0,
                                                 targetLeadTimeHours=0, slaBreachRate=0, reworkRate=0,
                                                 potentialRpaSavingsUzs=0, deviations=[]),
        )
        res = self.client.post('/api/v1/processes/', json=process.model_dump(mode='json'))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()['nodes'][0]['costPerExecution'], 60_000)
        self.client.delete('/api/v1/processes/proc-stats-save')


if __name__ == '__main__':
    unittest.main()
