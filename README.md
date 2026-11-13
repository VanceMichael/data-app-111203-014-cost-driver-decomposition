# cost-driver-decomposition

算力成本台账核算资源池支出、工作量和单位成本，并在两期之间提供可加的成本变化分解，保留硬件与优先级维度。

## 使用

运行需要 Python 3.11 或更新版本，没有第三方运行依赖。数据在本地处理，不请求外部接口。

```bash
python3 -m analysis_app summary
python3 -m analysis_app summary --input data/observations.csv --output output/summary.json
```

标准输出是完整 JSON，输出文件使用临时文件原子替换；失败退出码为 2，不覆盖既有结果。空数据集保留零计数与空分组，缺失数值不进入数值分母。数据指纹由按 observation_id 排序后的解析记录计算。

## 两期变化分解

```bash
python3 -m analysis_app decompose --first-period 0 --second-period 1
python3 -m analysis_app decompose --first-period 0 --second-period 1 \
    --config config.example.json --dimensions pool,hardware --round 4 \
    --output output/decomposition.json
```

- `--first-period` / `--second-period`：两个对比期，必填，不能相同，两期都必须有观测记录。
- `--dimensions`：逗号分隔的维度，取值仅允许 `pool`、`hardware`、`priority`；优先级高于配置文件；两者都缺省时使用全部三个维度。
- `--config`：UTF-8 JSON 配置，当前识别 `dimensions` 字符串数组（见 `config.example.json`）。
- `--round`：展示层保留小数位；缺省输出完整精度，舍入只在展示层发生。
- 输出含每个单维度的桥（`dimension_bridges`）与全维度组合的联合桥（`joint_bridge`），各桥独立闭合到同一个总差额。

### 三个口径

| 口径 | 含义 | 可加性 |
| --- | --- | --- |
| cost | 支出（元） | 可加，是分解桥的对象 |
| work | 标准化工作量 | 可加，是单位成本的分母 |
| unit_cost | cost / work | 跨单元不可加，仅在总量与单元层列示，不做单元间相加 |

### 分解方法

单元状态分为四类：

- `evaluable`：两期均出现且两期工作量均大于零，可构造反事实；
- `entry`：仅第二期出现（进入）；
- `exit`：仅第一期出现（退出）；
- `unsupported`：两期都出现但至少一期工作量为零，反事实没有数据支持。

对 evaluable 单元，记第一期/第二期单位成本 u0/u1、工作量 w0/w1，采用与因素次序无关的对称三项分解：

- 价格效应 `price_effect = (u1-u0) * w0`
- 结构效应 `structure_effect = u0 * (w1-w0)`
- 交互效应 `interaction_effect = (u1-u0) * (w1-w0)`

三项之和恒等于该单元成本差额。结构效应在总量层再拆为总量效应 `volume_effect`（按第一期可评估单元平均单位成本计的整体工作量变化）与组合效应 `mix_effect`（单元间工作量结构变化），两者之和等于结构效应。

同时提供 Shapley 两项视图：联盟价值函数为 v(空集)=u0·w0、v({价格})=u1·w0、v({结构})=u0·w1、v({价格,结构})=u1·w1，两个因素的 Shapley 值分别为 0.5·(u1-u0)·(w0+w1) 与 0.5·(w1-w0)·(u0+u1)。当任一期工作量为零或单元仅单期出现时，联盟价值没有数据支持：不定义、不补零、不做分摊，差额整额进入进入/退出/不支持桶。

进入、退出与不支持单元的成本差额独立列示并直接计入桥，零工作量但有费用的单元不会被丢弃，其单位成本显式为 `null`，禁止把空单元均价补零。

桥的恒等关系（浮点完整精度下残差为 0）：

```
total_change = price_effect + structure_effect + interaction_effect
             + entry_contribution + exit_contribution + unsupported_contribution
             = shapley_price_effect + shapley_structure_effect
             + entry_contribution + exit_contribution + unsupported_contribution
```

`unsupported_counterfactuals` 给出不支持反事实的单元清单、成本差额、按单元差额绝对值计的比例，以及可评估单元在两期成本中的覆盖率；每个单元在 `cells` 中保留可追溯的原始分子（cost）与分母（work）。

本结果是统计性变化分解（会计恒等式下的算术归因），不构成对任何团队或因素的因果责任判定。

## 验证与容器

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q analysis_app
docker build -t cost-driver-decomposition .
docker run --rm cost-driver-decomposition
```

容器内也可执行 `python -m unittest discover -s tests -v`。镜像构建阶段取得 Python 镜像，产品启动、测试和演示均无需下载。小规模样例的完整单元测试和 summary 可以连续执行，不需要等待定时任务。

## 代码与数据

- `analysis_app/io.py`：有类型的 CSV 读取、完整性校验及内容指纹。
- `analysis_app/schema.py`：字段类型、主体与聚合维度。
- `analysis_app/domain.py`：当前业务核算函数。
- `analysis_app/summary.py`：确定性的分组与数值口径。
- `analysis_app/decomposition.py`：两期可加分解、单元状态分类与 Shapley 视图。
- `analysis_app/config.py`：分解维度配置读取。
- `analysis_app/presentation.py`：展示层舍入（唯一允许舍入的位置）。
- `analysis_app/cli.py`：本地命令及原子输出。
- `docs/data.md`：数据契约。
- `docs/decomposition.md`：分解方法与手算案例。

演示数据全部为本地合成，不对应现实机构、设备或人员；不能用来推断真实业务效果。
