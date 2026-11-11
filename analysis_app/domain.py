import math
import statistics
from collections import Counter


def describe(records):
    cost = math.fsum(row['cost'] for row in records)
    work = math.fsum(row['work'] for row in records)
    return {'cost_total': cost, 'work_total': work, 'unit_cost': cost / work if work else None}
