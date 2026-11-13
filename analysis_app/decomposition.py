"""两期成本变化的可加分解。

口径区分：
- cost（支出，元）：可加，是分解桥的对象；
- work（工作量，标准化工作单位）：可加，是单位成本的分母；
- unit_cost（单位成本 = cost / work）：跨单元不可加，仅在总量与单元层列示。

在两期共同支持（两期均存在且工作量均为正）的单元上做对称的三项分解，
价格效应、结构效应、交互效应的公式对两个因素完全对称，与维度排列和
因素先后次序无关；另给出把交互项按 Shapley 分摊后的两项视图。
新出现、消失以及无法构造反事实的单元独立列示，不做任何均价补零。
"""

import math
from collections import defaultdict

from .io import fingerprint
from .schema import DIMENSIONS, PRODUCT, UNIT

EVALUABLE = 'evaluable'
ENTRY = 'entry'
EXIT = 'exit'
UNSUPPORTED = 'unsupported'

PRICE = 'price_effect'
STRUCTURE = 'structure_effect'
INTERACTION = 'interaction_effect'
SHAPLEY_PRICE = 'shapley_price_effect'
SHAPLEY_STRUCTURE = 'shapley_structure_effect'
VOLUME = 'volume_effect'
MIX = 'mix_effect'
ENTRY_TOTAL = 'entry_contribution'
EXIT_TOTAL = 'exit_contribution'
UNSUPPORTED_TOTAL = 'unsupported_contribution'

_METHOD = {
    'lenses': 'cost（支出，元，可加）、work（工作量，可加）、unit_cost（cost/work，跨单元不可加，仅列示不汇总相加）。',
    'evaluable_rule': '单元在两期均出现且两期工作量均大于零时才可构造价格与结构反事实；其余单元不估算效应，差额整额归入进入、退出或不支持反事实桶。',
    'price_effect': '价格效应 = (u1 - u0) * w0，其中 u=cost/work 为单元单位成本，下标 0/1 表示第一期/第二期。',
    'structure_effect': '结构效应 = u0 * (w1 - w0)，以第一期单位成本衡量工作量变化的支出影响。',
    'interaction_effect': '交互效应 = (u1 - u0) * (w1 - w0)，价格与工作量同时变化的联合项，独立列示不强制归并。',
    'order_invariance': '三项公式对价格与结构两个因素对称，单元按键排序聚合，结果与维度排列及因素次序无关。',
    'shapley_value_function': 'Shapley 视图的联盟价值函数 v(S)：v(空集)=u0*w0，v({价格})=u1*w0，v({结构})=u0*w1，v({价格,结构})=u1*w1；两个因素的 Shapley 值分别为 0.5*(u1-u0)*(w0+w1) 与 0.5*(w1-w0)*(u0+u1)，两者之和恒等于该单元成本差额。',
    'shapley_missing_coalition': '当单元任一期工作量为零或单元仅在单期出现时，联盟价值 v(S) 没有数据支持：不定义、不补零、不做 Shapley 分摊，该单元成本差额整额进入进入/退出/不支持桶。',
    'no_zero_filling': '零工作量单元的单位成本显式记为 null，禁止把空单元均价补零；零工作量但有费用的单元保留在明细中。',
    'entry_exit': '仅在第二期出现的单元为进入（entry），仅在第一期出现的为退出（exit），其成本差额独立列示。',
    'attribution_wording': '本结果是统计性变化分解（会计恒等式下的算术归因），不构成对任何团队或因素的因果责任判定。',
    'rounding': '全部计算使用完整浮点精度并以 math.fsum 汇总，舍入只发生在展示层；桥内残差为浮点运算残差，展示层舍入后可能出现轻微不闭合。',
}


def _fsum(values):
    return math.fsum(values)


def validate_dimensions(dimensions):
    if not dimensions:
        raise ValueError('分解维度不能为空')
    unknown = [name for name in dimensions if name not in DIMENSIONS]
    if unknown:
        raise ValueError('未知分解维度：' + ','.join(unknown))
    if len(set(dimensions)) != len(dimensions):
        raise ValueError('分解维度存在重复：' + ','.join(dimensions))
    return list(dimensions)


def aggregate_cells(records, period_first, period_second, dimensions):
    """按 (维度取值, 期别) 聚合原始分子分母，保留零工作量单元。"""
    if period_first == period_second:
        raise ValueError('两个对比期不能相同')
    cells = defaultdict(lambda: {
        period_first: {'cost': 0.0, 'work': 0.0, 'rows': 0},
        period_second: {'cost': 0.0, 'work': 0.0, 'rows': 0},
    })
    rows_first = rows_second = 0
    for row in records:
        period = row['period']
        if period == period_first:
            rows_first += 1
        elif period == period_second:
            rows_second += 1
        else:
            continue
        key = tuple(row[name] for name in dimensions)
        bucket = cells[key][period]
        bucket['cost'] += row['cost']
        bucket['work'] += row['work']
        bucket['rows'] += 1
    if rows_first == 0:
        raise ValueError(f'第一期 {period_first} 没有观测记录')
    if rows_second == 0:
        raise ValueError(f'第二期 {period_second} 没有观测记录')
    return cells, rows_first, rows_second


def _unit_cost(cost, work):
    return cost / work if work > 0 else None


def _cell_record(dimensions, key, first_bucket, second_bucket):
    c0, w0 = first_bucket['cost'], first_bucket['work']
    c1, w1 = second_bucket['cost'], second_bucket['work']
    present_first = first_bucket['rows'] > 0
    present_second = second_bucket['rows'] > 0
    u0, u1 = _unit_cost(c0, w0), _unit_cost(c1, w1)

    if present_first and present_second and w0 > 0 and w1 > 0:
        status = EVALUABLE
        du, dw = u1 - u0, w1 - w0
        effects = {
            PRICE: du * w0,
            STRUCTURE: u0 * dw,
            INTERACTION: du * dw,
            SHAPLEY_PRICE: du * (w0 + w1) / 2.0,
            SHAPLEY_STRUCTURE: dw * (u0 + u1) / 2.0,
        }
    else:
        if present_first and not present_second:
            status = EXIT
        elif present_second and not present_first:
            status = ENTRY
        else:
            status = UNSUPPORTED
        effects = {name: None for name in (PRICE, STRUCTURE, INTERACTION,
                                            SHAPLEY_PRICE, SHAPLEY_STRUCTURE)}

    return {
        'cell': {name: value for name, value in zip(dimensions, key)},
        'status': status,
        'cost_first': c0 if present_first else 0.0,
        'cost_second': c1 if present_second else 0.0,
        'cost_change': (c1 if present_second else 0.0) - (c0 if present_first else 0.0),
        'work_first': w0 if present_first else 0.0,
        'work_second': w1 if present_second else 0.0,
        'work_change': (w1 if present_second else 0.0) - (w0 if present_first else 0.0),
        'unit_cost_first': u0,
        'unit_cost_second': u1,
        'rows_first': first_bucket['rows'],
        'rows_second': second_bucket['rows'],
        'effects': effects,
    }


def decompose(records, period_first, period_second, dimensions):
    """对给定维度组合执行两期可加分解，返回自包含结果字典。"""
    dimensions = validate_dimensions(dimensions)
    filtered = sorted(
        (row for row in records if row['period'] in (period_first, period_second)),
        key=lambda row: row['observation_id'])
    cells, rows_first, rows_second = aggregate_cells(filtered, period_first, period_second, dimensions)

    cell_records = []
    for key in sorted(cells):
        cell_records.append(_cell_record(
            dimensions, key,
            cells[key][period_first], cells[key][period_second]))

    evaluable = [item for item in cell_records if item['status'] == EVALUABLE]
    entries = [item for item in cell_records if item['status'] == ENTRY]
    exits = [item for item in cell_records if item['status'] == EXIT]
    unsupported = [item for item in cell_records if item['status'] == UNSUPPORTED]

    def effect_sum(name):
        return _fsum(item['effects'][name] for item in evaluable)

    def change_sum(items):
        return _fsum(item['cost_change'] for item in items)

    price = effect_sum(PRICE)
    structure = effect_sum(STRUCTURE)
    interaction = effect_sum(INTERACTION)
    shapley_price = effect_sum(SHAPLEY_PRICE)
    shapley_structure = effect_sum(SHAPLEY_STRUCTURE)
    entry_total = change_sum(entries)
    exit_total = change_sum(exits)
    unsupported_total = change_sum(unsupported)

    # 结构效应在总量层进一步拆为总量效应与组合（mix）效应，仅使用可评估单元。
    c0_eval = _fsum(item['cost_first'] for item in evaluable)
    w0_eval = _fsum(item['work_first'] for item in evaluable)
    w1_eval = _fsum(item['work_second'] for item in evaluable)
    if w0_eval > 0:
        u0_eval_bar = c0_eval / w0_eval
        volume = u0_eval_bar * (w1_eval - w0_eval)
        mix = structure - volume
    else:
        volume = mix = None

    cost_first = _fsum(item['cost_first'] for item in cell_records)
    cost_second = _fsum(item['cost_second'] for item in cell_records)
    work_first = _fsum(item['work_first'] for item in cell_records)
    work_second = _fsum(item['work_second'] for item in cell_records)
    total_change = cost_second - cost_first

    components = [price, structure, interaction, entry_total, exit_total, unsupported_total]
    shapley_components = [shapley_price, shapley_structure, entry_total, exit_total, unsupported_total]
    residual = total_change - _fsum(components)
    shapley_residual = total_change - _fsum(shapley_components)

    abs_changes = _fsum(abs(item['cost_change']) for item in cell_records)

    def bucket_summary(items):
        abs_total = _fsum(abs(item['cost_change']) for item in items)
        return {
            'cell_count': len(items),
            'cells': [item['cell'] for item in items],
            'cost_change': change_sum(items),
            'cost_change_abs': abs_total,
            'share_of_abs_cell_changes': (abs_total / abs_changes) if abs_changes > 0 else None,
        }

    c0_evaluable = _fsum(item['cost_first'] for item in evaluable)
    c1_evaluable = _fsum(item['cost_second'] for item in evaluable)

    return {
        'product': PRODUCT,
        'schema_version': 1,
        'unit': UNIT,
        'period_first': period_first,
        'period_second': period_second,
        'dimensions': list(dimensions),
        'input_fingerprint': fingerprint(sorted(filtered, key=lambda row: row['observation_id'])),
        'rows_first': rows_first,
        'rows_second': rows_second,
        'totals': {
            'cost': {'first': cost_first, 'second': cost_second, 'change': total_change},
            'work': {'first': work_first, 'second': work_second, 'change': work_second - work_first},
            'unit_cost': {
                'first': _unit_cost(cost_first, work_first),
                'second': _unit_cost(cost_second, work_second),
                'change': (_unit_cost(cost_second, work_second) - _unit_cost(cost_first, work_first))
                          if work_first > 0 and work_second > 0 else None,
            },
        },
        'bridge': {
            PRICE: price,
            STRUCTURE: structure,
            INTERACTION: interaction,
            SHAPLEY_PRICE: shapley_price,
            SHAPLEY_STRUCTURE: shapley_structure,
            VOLUME: volume,
            MIX: mix,
            ENTRY_TOTAL: entry_total,
            EXIT_TOTAL: exit_total,
            UNSUPPORTED_TOTAL: unsupported_total,
            'total_change': total_change,
            'residual': residual,
            'shapley_residual': shapley_residual,
        },
        'unsupported_counterfactuals': {
            'evaluable_cell_count': len(evaluable),
            'evaluable_share_first_cost': (c0_evaluable / cost_first) if cost_first else None,
            'evaluable_share_second_cost': (c1_evaluable / cost_second) if cost_second else None,
            ENTRY: bucket_summary(entries),
            EXIT: bucket_summary(exits),
            UNSUPPORTED: bucket_summary(unsupported),
        },
        'cells': cell_records,
        'method': _METHOD,
    }


def decompose_dimensions(records, period_first, period_second, dimensions):
    """每个维度单独出一座桥，并给出全维度组合的联合桥，全部闭合到同一总差额。"""
    dimensions = validate_dimensions(dimensions)
    filtered = sorted(
        (row for row in records if row['period'] in (period_first, period_second)),
        key=lambda row: row['observation_id'])
    dimension_bridges = [decompose(filtered, period_first, period_second, [name])
                         for name in dimensions]
    joint_bridge = decompose(filtered, period_first, period_second, dimensions)
    return {
        'product': PRODUCT,
        'schema_version': 1,
        'unit': UNIT,
        'period_first': period_first,
        'period_second': period_second,
        'dimensions': list(dimensions),
        'input_fingerprint': fingerprint(sorted(filtered, key=lambda row: row['observation_id'])),
        'dimension_bridges': dimension_bridges,
        'joint_bridge': joint_bridge,
        'method': _METHOD,
    }
