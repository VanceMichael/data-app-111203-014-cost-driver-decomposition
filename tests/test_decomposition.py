import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from analysis_app.cli import save_json
from analysis_app.config import load_config
from analysis_app.decomposition import (
    ENTRY,
    EXIT,
    UNSUPPORTED,
    INTERACTION,
    MIX,
    PRICE,
    SHAPLEY_PRICE,
    SHAPLEY_STRUCTURE,
    STRUCTURE,
    VOLUME,
    decompose,
    decompose_dimensions,
)
from analysis_app.presentation import rounded
from analysis_app.schema import FIELDS

_FIELDS = list(FIELDS)


def make_record(obs_id, period, pool, hardware, priority, cost, work):
    return {
        'observation_id': obs_id, 'job': 'J-' + obs_id, 'pool': pool,
        'period': period, 'cost': float(cost), 'work': float(work),
        'duration': 1.0, 'hardware': hardware, 'priority': priority,
    }


def write_csv(directory, rows):
    path = Path(directory) / 'input.csv'
    import csv
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


# 手算案例（两期、两个单元 A/B，单维度 pool）：
# A: cost 100->132, work 10->12, u 10->11；B: cost 60->70, work 10->10, u 6->7
# A: 价格 (11-10)*10=10, 结构 10*(12-10)=20, 交互 1*2=2；
#    Shapley 价格 1*(10+12)/2=11, Shapley 结构 2*(10+11)/2=21
# B: 价格 (7-6)*10=10, 结构 0, 交互 0；Shapley 同值
# 汇总: 价格 20, 结构 20, 交互 2, 总差额 42；Shapley 价格 21、结构 21
# 总量效应: 平均 u0=160/20=8, 8*(22-20)=16；组合效应 20-16=4
def hand_records():
    rows = []
    seq = 0
    plan = {
        0: {'A': (100, 10), 'B': (60, 10)},
        1: {'A': (132, 12), 'B': (70, 10)},
    }
    for period, cells in plan.items():
        for pool, (cost, work) in sorted(cells.items()):
            seq += 1
            rows.append(make_record(f'obs-{seq}', period, pool, 'H0', 'L0', cost, work))
    return rows


class HandCalculationTest(unittest.TestCase):
    def setUp(self):
        self.result = decompose(hand_records(), 0, 1, ['pool'])
        self.bridge = self.result['bridge']
        self.cells = {item['cell']['pool']: item for item in self.result['cells']}

    def test_hand_computed_effects(self):
        a = self.cells['A']['effects']
        self.assertAlmostEqual(a[PRICE], 10)
        self.assertAlmostEqual(a[STRUCTURE], 20)
        self.assertAlmostEqual(a[INTERACTION], 2)
        self.assertAlmostEqual(a[SHAPLEY_PRICE], 11)
        self.assertAlmostEqual(a[SHAPLEY_STRUCTURE], 21)
        b = self.cells['B']['effects']
        self.assertAlmostEqual(b[PRICE], 10)
        self.assertAlmostEqual(b[STRUCTURE], 0)
        self.assertAlmostEqual(b[INTERACTION], 0)

    def test_bridge_totals_match_hand_calc(self):
        self.assertAlmostEqual(self.bridge[PRICE], 20)
        self.assertAlmostEqual(self.bridge[STRUCTURE], 20)
        self.assertAlmostEqual(self.bridge[INTERACTION], 2)
        self.assertAlmostEqual(self.bridge['total_change'], 42)
        self.assertAlmostEqual(self.bridge[VOLUME], 16)
        self.assertAlmostEqual(self.bridge[MIX], 4)
        self.assertAlmostEqual(self.bridge[SHAPLEY_PRICE], 21)
        self.assertAlmostEqual(self.bridge[SHAPLEY_STRUCTURE], 21)

    def test_three_lenses_are_separated(self):
        totals = self.result['totals']
        self.assertEqual(totals['cost'], {'first': 160, 'second': 202, 'change': 42})
        self.assertEqual(totals['work'], {'first': 20, 'second': 22, 'change': 2})
        self.assertAlmostEqual(totals['unit_cost']['first'], 8)
        self.assertAlmostEqual(totals['unit_cost']['second'], 202 / 22)

    def test_cell_effects_sum_to_bridge(self):
        for name in (PRICE, STRUCTURE, INTERACTION, SHAPLEY_PRICE, SHAPLEY_STRUCTURE):
            cell_sum = sum(item['effects'][name] for item in self.result['cells'])
            self.assertAlmostEqual(cell_sum, self.bridge[name])
        cell_cost_change = sum(item['cost_change'] for item in self.result['cells'])
        self.assertAlmostEqual(cell_cost_change, self.bridge['total_change'])


class AdditivityTest(unittest.TestCase):
    def test_bridge_closes_exactly_three_way(self):
        result = decompose(hand_records(), 0, 1, ['pool'])
        b = result['bridge']
        rebuilt = (b[PRICE] + b[STRUCTURE] + b[INTERACTION]
                   + b['entry_contribution'] + b['exit_contribution']
                   + b['unsupported_contribution'])
        self.assertAlmostEqual(rebuilt, b['total_change'])
        self.assertAlmostEqual(b['residual'], 0.0)

    def test_bridge_closes_under_shapley(self):
        result = decompose(hand_records(), 0, 1, ['pool'])
        b = result['bridge']
        rebuilt = (b[SHAPLEY_PRICE] + b[SHAPLEY_STRUCTURE]
                   + b['entry_contribution'] + b['exit_contribution']
                   + b['unsupported_contribution'])
        self.assertAlmostEqual(rebuilt, b['total_change'])
        self.assertAlmostEqual(b['shapley_residual'], 0.0)

    def test_shapley_equals_three_way_after_interaction_split(self):
        result = decompose(hand_records(), 0, 1, ['pool'])
        b = result['bridge']
        self.assertAlmostEqual(b[SHAPLEY_PRICE] + b[SHAPLEY_STRUCTURE],
                               b[PRICE] + b[STRUCTURE] + b[INTERACTION])
        for item in result['cells']:
            e = item['effects']
            self.assertAlmostEqual(
                e[SHAPLEY_PRICE] + e[SHAPLEY_STRUCTURE],
                e[PRICE] + e[STRUCTURE] + e[INTERACTION])


class OrderInvarianceTest(unittest.TestCase):
    def _records(self):
        rows = []
        seq = 0
        for period in (0, 1):
            for pool in ('P0', 'P1'):
                for hardware in ('H0', 'H1'):
                    for priority in ('L0', 'L1'):
                        seq += 1
                        cost = 10 + period * 3 + (1 if hardware == 'H1' else 0)
                        work = 5 + (1 if priority == 'L1' else 0) + period
                        rows.append(make_record(f'obs-{seq}', period, pool, hardware,
                                                priority, cost, work))
        return rows

    def test_dimension_permutation_invariant(self):
        records = self._records()
        order_a = decompose(records, 0, 1, ['pool', 'hardware', 'priority'])
        order_b = decompose(records, 0, 1, ['priority', 'pool', 'hardware'])
        order_c = decompose(records, 0, 1, ['hardware', 'priority', 'pool'])
        for key in ('totals', 'bridge'):
            self.assertEqual(order_a[key], order_b[key])
            self.assertEqual(order_a[key], order_c[key])
        # 单元集合与数值不随排列改变（cell 标签字典的键序差异不影响内容）
        def canonical(result):
            return sorted(json.dumps(item, sort_keys=True) for item in result['cells'])
        self.assertEqual(canonical(order_a), canonical(order_b))
        self.assertEqual(canonical(order_a), canonical(order_c))

    def test_factor_order_symmetry(self):
        # 三项分解不含任何因素先后次序（不存在序贯替代），单元恒等式
        # 价格+结构+交互 = u1*w1 - u0*w0 对因素角色完全对称。
        for item in decompose(hand_records(), 0, 1, ['pool'])['cells']:
            e = item['effects']
            identity = (item['unit_cost_second'] * item['work_second']
                        - item['unit_cost_first'] * item['work_first'])
            self.assertAlmostEqual(e[PRICE] + e[STRUCTURE] + e[INTERACTION], identity)
        # 交换基期/对比期：总差额取反；Shapley 两项各自取反（基期反转对称）
        forward = decompose(hand_records(), 0, 1, ['pool'])
        backward = decompose(hand_records(), 1, 0, ['pool'])
        self.assertAlmostEqual(forward['bridge']['total_change'],
                               -backward['bridge']['total_change'])
        for name in (SHAPLEY_PRICE, SHAPLEY_STRUCTURE):
            self.assertAlmostEqual(forward['bridge'][name],
                                   -backward['bridge'][name])
        # 三项口径下基期反转不改变总闭合，差异仅在交互项的归属：
        # -价格(反向) = 价格(正向)+交互，-结构(反向) = 结构(正向)+交互
        fb, bb = forward['bridge'], backward['bridge']
        self.assertAlmostEqual(-bb[PRICE], fb[PRICE] + fb[INTERACTION])
        self.assertAlmostEqual(-bb[STRUCTURE], fb[STRUCTURE] + fb[INTERACTION])
        # 交互项是两个差分之积，双重取反后符号不变
        self.assertAlmostEqual(bb[INTERACTION], fb[INTERACTION])

    def test_dimension_bridges_each_close_to_total(self):
        records = self._records()
        packed = decompose_dimensions(records, 0, 1,
                                      ['pool', 'hardware', 'priority'])
        total = packed['joint_bridge']['bridge']['total_change']
        for single in packed['dimension_bridges']:
            b = single['bridge']
            self.assertAlmostEqual(b['total_change'], total)
            self.assertAlmostEqual(b['residual'], 0.0)
        self.assertAlmostEqual(packed['joint_bridge']['bridge']['residual'], 0.0)

    def test_input_shuffle_does_not_change_result(self):
        import random
        records = self._records()
        baseline = decompose_dimensions(records, 0, 1,
                                        ['pool', 'hardware', 'priority'])
        shuffled = copy.deepcopy(records)
        random.Random(123).shuffle(shuffled)
        self.assertEqual(
            decompose_dimensions(shuffled, 0, 1, ['pool', 'hardware', 'priority']),
            baseline)


class CorrelatedDimensionsTest(unittest.TestCase):
    def test_perfectly_correlated_pool_hardware(self):
        rows = []
        seq = 0
        # pool 与 hardware 一一对应（完全相关），priority 提供第二个变化维度
        mapping = {'P0': 'H0', 'P1': 'H1', 'P2': 'H2'}
        for period in (0, 1):
            for pool, hardware in mapping.items():
                for priority in ('L0', 'L1'):
                    seq += 1
                    cost = 20 + period * 4 + (2 if priority == 'L1' else 0)
                    work = 10 + period
                    rows.append(make_record(f'obs-{seq}', period, pool, hardware,
                                            priority, cost, work))
        joint = decompose(rows, 0, 1, ['pool', 'hardware'])
        single_pool = decompose(rows, 0, 1, ['pool'])
        single_hw = decompose(rows, 0, 1, ['hardware'])
        # 三座桥都闭合到同一总差额
        for result in (joint, single_pool, single_hw):
            b = result['bridge']
            self.assertAlmostEqual(b['residual'], 0.0)
            self.assertAlmostEqual(
                b[PRICE] + b[STRUCTURE] + b[INTERACTION], b['total_change'])
        # 一一对应时，两个单维度桥的效应金额相同
        self.assertEqual(len(single_pool['cells']), len(single_hw['cells']))
        for name in (PRICE, STRUCTURE, INTERACTION):
            self.assertAlmostEqual(single_pool['bridge'][name],
                                   single_hw['bridge'][name])


class EntryExitTest(unittest.TestCase):
    def _records(self):
        rows = hand_records()  # A、B 两期都在
        # 新硬件 H1 仅第二期出现（进入）
        rows.append(make_record('obs-entry', 1, 'A', 'H1', 'L0', 30, 5))
        # 硬件 H9 仅第一期出现（退出）
        rows.append(make_record('obs-exit', 0, 'B', 'H9', 'L0', 14, 7))
        return rows

    def test_entry_exit_classified_and_reconciled(self):
        result = decompose(self._records(), 0, 1, ['pool', 'hardware'])
        statuses = {tuple(sorted(item['cell'].items())): item['status']
                    for item in result['cells']}
        self.assertIn(ENTRY, statuses.values())
        entry = [item for item in result['cells'] if item['status'] == ENTRY]
        exit_ = [item for item in result['cells'] if item['status'] == EXIT]
        self.assertEqual(len(entry), 1)
        self.assertEqual(len(exit_), 1)
        self.assertAlmostEqual(entry[0]['cost_change'], 30)
        self.assertAlmostEqual(exit_[0]['cost_change'], -14)
        b = result['bridge']
        self.assertAlmostEqual(b['entry_contribution'], 30)
        self.assertAlmostEqual(b['exit_contribution'], -14)
        # 进入退出单元不产生价格/结构效应
        for item in entry + exit_:
            for name in (PRICE, STRUCTURE, INTERACTION):
                self.assertIsNone(item['effects'][name])
        rebuilt = (b[PRICE] + b[STRUCTURE] + b[INTERACTION]
                   + b['entry_contribution'] + b['exit_contribution']
                   + b['unsupported_contribution'])
        self.assertAlmostEqual(rebuilt, b['total_change'])
        # 总差额 = 手算 42 + 进入 30 - 退出 14
        self.assertAlmostEqual(b['total_change'], 58)

    def test_unsupported_share_is_reported(self):
        result = decompose(self._records(), 0, 1, ['pool', 'hardware'])
        info = result['unsupported_counterfactuals']
        self.assertEqual(info[ENTRY]['cell_count'], 1)
        self.assertEqual(info[EXIT]['cell_count'], 1)
        # 进入单元身份可追溯
        self.assertEqual(info[ENTRY]['cells'][0]['hardware'], 'H1')


class ZeroWorkTest(unittest.TestCase):
    def _records(self):
        rows = hand_records()
        # 两期都存在但第一期零工作量、有费用：不得丢弃、不得补零均价
        rows.append(make_record('obs-zw0', 0, 'C', 'H0', 'L0', 5, 0))
        rows.append(make_record('obs-zw0b', 1, 'C', 'H0', 'L0', 8, 4))
        # 第二期零工作量有费用
        rows.append(make_record('obs-zw1', 0, 'D', 'H0', 'L0', 9, 3))
        rows.append(make_record('obs-zw1b', 1, 'D', 'H0', 'L0', 7, 0))
        # 仅第二期出现且零工作量有费用：进入且单位成本为空
        rows.append(make_record('obs-zw-entry', 1, 'E', 'H0', 'L0', 11, 0))
        return rows

    def test_zero_work_cells_kept_without_zero_filling(self):
        result = decompose(self._records(), 0, 1, ['pool'])
        by_pool = {item['cell']['pool']: item for item in result['cells']}
        self.assertEqual(set(by_pool), {'A', 'B', 'C', 'D', 'E'})
        c = by_pool['C']
        self.assertEqual(c['status'], UNSUPPORTED)
        self.assertIsNone(c['unit_cost_first'])  # 禁止补零
        self.assertAlmostEqual(c['unit_cost_second'], 2.0)
        self.assertAlmostEqual(c['cost_first'], 5)  # 费用保留
        for name in (PRICE, STRUCTURE, INTERACTION, SHAPLEY_PRICE, SHAPLEY_STRUCTURE):
            self.assertIsNone(c['effects'][name])
        d = by_pool['D']
        self.assertEqual(d['status'], UNSUPPORTED)
        self.assertIsNone(d['unit_cost_second'])
        e = by_pool['E']
        self.assertEqual(e['status'], ENTRY)
        self.assertIsNone(e['unit_cost_second'])
        self.assertAlmostEqual(e['cost_second'], 11)

    def test_unsupported_bucket_closes_bridge(self):
        result = decompose(self._records(), 0, 1, ['pool'])
        b = result['bridge']
        info = result['unsupported_counterfactuals']
        # C: 8-5=3, D: 7-9=-2
        self.assertAlmostEqual(b['unsupported_contribution'], 1)
        self.assertEqual(info[UNSUPPORTED]['cell_count'], 2)
        self.assertAlmostEqual(info[ENTRY]['cost_change'], 11)
        rebuilt = (b[PRICE] + b[STRUCTURE] + b[INTERACTION]
                   + b['entry_contribution'] + b['exit_contribution']
                   + b['unsupported_contribution'])
        self.assertAlmostEqual(rebuilt, b['total_change'])
        # 不支持反事实比例可复核（按单元差额绝对值）
        abs_total = sum(abs(item['cost_change']) for item in result['cells'])
        self.assertAlmostEqual(
            info[UNSUPPORTED]['share_of_abs_cell_changes'],
            info[UNSUPPORTED]['cost_change_abs'] / abs_total)
        # 可评估单元的成本覆盖率
        self.assertLess(info['evaluable_share_first_cost'], 1.0)
        self.assertLess(info['evaluable_share_second_cost'], 1.0)


class PresentationTest(unittest.TestCase):
    def test_rounding_is_display_only(self):
        result = decompose(hand_records(), 0, 1, ['pool'])
        original_price = result['bridge'][PRICE]
        display = rounded(result, 2)
        self.assertEqual(display['bridge'][PRICE], round(original_price, 2))
        # 原始结果不被修改，仍为完整精度且闭合
        self.assertEqual(result['bridge'][PRICE], original_price)
        self.assertIsNone(rounded(None, 2))
        self.assertEqual(rounded('x', 2), 'x')
        self.assertEqual(rounded(3, 2), 3)

    def test_json_serializable_with_no_nan(self):
        result = decompose_dimensions(hand_records(), 0, 1, ['pool'])
        # allow_nan=False：出现 NaN/Infinity 会抛错
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
        self.assertIn('cost-driver-decomposition', payload)


class ValidationTest(unittest.TestCase):
    def test_unknown_dimension_rejected(self):
        with self.assertRaisesRegex(ValueError, '未知分解维度'):
            decompose(hand_records(), 0, 1, ['region'])

    def test_duplicate_dimension_rejected(self):
        with self.assertRaisesRegex(ValueError, '重复'):
            decompose(hand_records(), 0, 1, ['pool', 'pool'])

    def test_same_period_rejected(self):
        with self.assertRaisesRegex(ValueError, '对比期不能相同'):
            decompose(hand_records(), 0, 0, ['pool'])

    def test_missing_period_rejected(self):
        with self.assertRaisesRegex(ValueError, '没有观测记录'):
            decompose(hand_records(), 0, 9, ['pool'])


class CliTest(unittest.TestCase):
    def test_decompose_cli_matches_library(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = write_csv(directory, hand_records())
            out_path = Path(directory) / 'result.json'
            run = subprocess.run(
                [sys.executable, '-m', 'analysis_app', 'decompose',
                 '--input', str(csv_path), '--first-period', '0',
                 '--second-period', '1', '--dimensions', 'pool',
                 '--output', str(out_path)],
                text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            expected = decompose_dimensions(hand_records(), 0, 1, ['pool'])
            self.assertEqual(json.loads(run.stdout), expected)
            self.assertEqual(json.loads(out_path.read_text()), expected)

    def test_config_file_and_round_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = write_csv(directory, hand_records())
            config_path = Path(directory) / 'config.json'
            config_path.write_text(json.dumps({'dimensions': ['pool']}), encoding='utf-8')
            run = subprocess.run(
                [sys.executable, '-m', 'analysis_app', 'decompose',
                 '--input', str(csv_path), '--first-period', '0',
                 '--second-period', '1', '--config', str(config_path),
                 '--round', '2'],
                text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            payload = json.loads(run.stdout)
            self.assertEqual(payload['joint_bridge']['bridge'][PRICE], 20)
            self.assertEqual(load_config(config_path), ['pool'])

    def test_dimensions_override_config(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = write_csv(directory, hand_records())
            config_path = Path(directory) / 'config.json'
            config_path.write_text(json.dumps({'dimensions': ['hardware']}), encoding='utf-8')
            run = subprocess.run(
                [sys.executable, '-m', 'analysis_app', 'decompose',
                 '--input', str(csv_path), '--first-period', '0',
                 '--second-period', '1', '--config', str(config_path),
                 '--dimensions', 'pool'],
                text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)['dimensions'], ['pool'])

    def test_cli_invalid_period_exit_code_2(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = write_csv(directory, hand_records())
            run = subprocess.run(
                [sys.executable, '-m', 'analysis_app', 'decompose',
                 '--input', str(csv_path), '--first-period', '0',
                 '--second-period', '9', '--dimensions', 'pool'],
                text=True, capture_output=True)
            self.assertEqual(run.returncode, 2)
            self.assertIn('没有观测记录', run.stderr)

    def test_summary_command_still_works(self):
        run = subprocess.run(
            [sys.executable, '-m', 'analysis_app', 'summary'],
            text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        payload = json.loads(run.stdout)
        self.assertEqual(payload['product'], 'cost-driver-decomposition')


class FixtureIntegrationTest(unittest.TestCase):
    def test_real_fixture_reconciles_all_dimensions(self):
        from analysis_app.io import load_records
        records = load_records('data/observations.csv')
        packed = decompose_dimensions(records, 0, 1,
                                      ['pool', 'hardware', 'priority'])
        total = packed['joint_bridge']['totals']['cost']['change']
        for bridge in packed['dimension_bridges'] + [packed['joint_bridge']]:
            b = bridge['bridge']
            self.assertAlmostEqual(b['total_change'], total)
            self.assertAlmostEqual(b['residual'], 0.0)
            self.assertAlmostEqual(b['shapley_residual'], 0.0)
            rebuilt = (b[PRICE] + b[STRUCTURE] + b[INTERACTION]
                       + b['entry_contribution'] + b['exit_contribution']
                       + b['unsupported_contribution'])
            self.assertAlmostEqual(rebuilt, total)
        # 原始分子分母在单元层可追溯
        for item in packed['joint_bridge']['cells']:
            self.assertIn('cost_first', item)
            self.assertIn('work_first', item)
            self.assertIn('cost_second', item)
            self.assertIn('work_second', item)
        # 方法说明包含 Shapley 价值函数与不补零承诺
        method = packed['method']
        self.assertIn('v(空集)', method['shapley_value_function'])
        self.assertIn('补零', method['no_zero_filling'])
        self.assertIn('因果责任', method['attribution_wording'])


if __name__ == '__main__':
    unittest.main()
