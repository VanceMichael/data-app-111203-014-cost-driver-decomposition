FIELDS = {'observation_id': 'str', 'job': 'str', 'pool': 'str', 'period': 'int', 'cost': 'float', 'work': 'float', 'duration': 'float', 'hardware': 'str', 'priority': 'str'}
REQUIRED = ['observation_id', 'job', 'pool', 'period', 'cost', 'work', 'duration', 'hardware', 'priority']
ENTITY = 'job'
GROUP = 'pool'
MEASURE = 'cost'
PRODUCT = 'cost-driver-decomposition'
UNIT = '元'
DIMENSIONS = ('pool', 'hardware', 'priority')
