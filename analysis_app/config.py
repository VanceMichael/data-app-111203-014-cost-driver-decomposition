"""分解命令的配置读取。

配置为 UTF-8 JSON，当前识别键 dimensions（字符串数组，取值来自
schema.DIMENSIONS）。配置文件与代码目录无关，路径由调用方参数传入。
"""

import json

from .schema import DIMENSIONS

DEFAULT_DIMENSIONS = list(DIMENSIONS)


def load_config(path):
    with open(path, encoding='utf-8') as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError('配置文件顶层必须是对象')
    dimensions = payload.get('dimensions', DEFAULT_DIMENSIONS)
    if not isinstance(dimensions, list) or not all(isinstance(name, str) for name in dimensions):
        raise ValueError('配置 dimensions 必须是字符串数组')
    return dimensions
