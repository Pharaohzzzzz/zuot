# zuot

中际旭创（300308）/ 胜宏科技（300476）每日反T（先卖后买）研究与信号系统。

- 不自动下单，信号推送 + 人工执行。
- 只做反T；14:50 必须买回；日亏上限 2,000 元；全局同时只允许 1 笔未平T。
- 数据：新浪/腾讯实时 Level-1，BaoStock 5 分钟历史，东财日线。
- 研究优先：先回测过门槛，再上真钱。

## 快速开始

```powershell
cd zuot
uv sync
uv run zuot doctor
uv run pytest
```

## 命令

```powershell
uv run zuot doctor          # 环境/数据/配置检查
uv run zuot status          # 当前台账与风险状态
uv run zuot fetch --start 2020-01-01
uv run zuot research        # 回测 + 研究报告
uv run zuot plan            # 今日计划
uv run zuot live --mode shadow
uv run zuot fill --side sell --price 471.20 --qty 200
uv run zuot eod
uv run zuot guard --check
```

## 边界

真实持仓、成交、webhook 密钥只存本地，不进 Git。系统不构成投资建议。
