"""展示层工具：唯一允许发生数值舍入的地方。"""


def rounded(value, digits):
    """递归舍入；None、字符串、整数原样保留。"""
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {key: rounded(item, digits) for key, item in value.items()}
    if isinstance(value, list):
        return [rounded(item, digits) for item in value]
    return value
