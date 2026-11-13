import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .config import DEFAULT_DIMENSIONS, load_config
from .decomposition import decompose_dimensions
from .io import load_records
from .presentation import rounded
from .summary import summarize


def save_json(path, result):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.analysis-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(result, handle, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description='本地观测数据分析')
    commands = parser.add_subparsers(dest='command', required=True)
    summary = commands.add_parser('summary', help='按业务维度核对观测口径')
    summary.add_argument('--input', type=Path, default=Path('data/observations.csv'))
    summary.add_argument('--output', type=Path)

    decompose = commands.add_parser('decompose', help='两期成本变化的可加分解')
    decompose.add_argument('--input', type=Path, default=Path('data/observations.csv'))
    decompose.add_argument('--output', type=Path)
    decompose.add_argument('--first-period', type=int, required=True, help='基期 period')
    decompose.add_argument('--second-period', type=int, required=True, help='对比期 period')
    decompose.add_argument('--config', type=Path, help='JSON 配置，识别 dimensions 数组')
    decompose.add_argument('--dimensions', help='逗号分隔的维度，优先级高于配置文件')
    decompose.add_argument('--round', type=int, help='展示层保留小数位；缺省输出完整精度')
    args = parser.parse_args()
    try:
        records = load_records(args.input)
        if args.command == 'summary':
            result = summarize(records)
        else:
            if args.dimensions:
                dimensions = [name.strip() for name in args.dimensions.split(',') if name.strip()]
            elif args.config:
                dimensions = load_config(args.config)
            else:
                dimensions = list(DEFAULT_DIMENSIONS)
            result = decompose_dimensions(records, args.first_period, args.second_period, dimensions)
            if args.round is not None:
                result = rounded(result, args.round)
        if args.output:
            save_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
