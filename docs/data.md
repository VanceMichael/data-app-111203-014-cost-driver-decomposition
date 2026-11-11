# 数据契约

算力成本台账核算资源池支出、工作量和单位成本，保留硬件与优先级维度。

主测量 `cost` 的单位为元，主体字段是 `job`，核算维度是 `pool`。`observation_id` 是文件内唯一观测编号，主体允许重复观测。数值只接受有限值；空单元格解析为 None，不能直接当成零。额外列不进入当前核算。

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `observation_id` | str | 必填 |
| `job` | str | 必填 |
| `pool` | str | 必填 |
| `period` | int | 允许空值，空值不是零 |
| `cost` | float | 必填 |
| `work` | float | 允许空值，空值不是零 |
| `duration` | float | 允许空值，空值不是零 |
| `hardware` | str | 允许空值，空值不是零 |
| `priority` | str | 允许空值，空值不是零 |

时间字段 day、week、tick、hour、minute、cycle、start_day、reported_day、known_at、visible_at、opened、observed 均以各字段名所述的相对时间单位表示整数；不同字段并不必然代表同一种时间。缺少的登记码与浓度等信息保持为空。示例只表达产品数据结构，不能代替实际调查资料。
