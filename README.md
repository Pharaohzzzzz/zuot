# zuot

中际旭创（300308）/ 胜宏科技（300476）每日反T（先卖后买）研究与信号系统。

- 不自动下单：信号推送 + 人工在券商 App 下单，成交后用 `zuot fill` 回填。
- 只做反T；14:50 必须买回；日亏上限 2,000 元；全局同时只允许 1 笔未平T；每票每日最多 1 次往返。
- 数据：新浪/腾讯实时 Level-1，BaoStock 5 分钟历史；5 分钟 bar 收盘评估，下一 tick 执行。
- 研究优先：`zuot research` 的 Gate 不通过时，`zuot live` 拒绝启动，不会发出真钱信号。

## 快速开始

```powershell
cd zuot
uv sync --all-groups
uv run zuot doctor
uv run pytest
```

## 命令

```powershell
uv run zuot doctor                         # 环境/数据/配置检查
uv run zuot init                           # 建库并写入底仓 500 / 2000
uv run zuot status                         # 台账与风险状态
uv run zuot fetch --start 2020-01-01 --end 2026-11-10
uv run zuot fetch --realtime               # 只取一次实时快照
uv run zuot research                       # 训练/OOS 回测 + Gate + HTML/JSON 报告
uv run zuot plan                           # 今日计划
uv run zuot live --once                    # 单次轮询（Gate 通过后才允许）
uv run zuot live --poll 3 --until 15:05    # 盘中循环
uv run zuot fill --side sell --price 471.20 --symbol 300308 --qty 200
uv run zuot fill --side buy --price 466.00 --symbol 300308 --qty 200
uv run zuot eod --actual 300308:500,300476:2000
uv run zuot guard                          # 上线前/盘前风控检查
uv run zuot replay --start 2026-01-01 --end 2026-11-10 --strategy vwap_reversion
```

## 研究 Gate

`zuot research` 输出 `reports/research_<timestamp>.json/.html`。`zuot live` 只读取最新报告：
只有 `passed` 非空才启动；否则直接退出。当前四个候选策略（gap_fade、vwap_reversion、
prior_high_fade、afternoon_pulse）在 2020–2023 训练和 2024–2026 样本外都没有通过 Gate，
因此默认不允许投入真钱。

## 飞书

`configs/notify.toml` 默认 `dry_run = true`，消息只打印在本地。真钱使用前：

```powershell
$env:ZUOT_FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/..."
```

然后把 `configs/notify.toml` 的 `dry_run` 改为 `false`。webhook 只存环境变量，不进 Git。

## 边界

真实持仓、成交、webhook 密钥只存本地，不进 Git。系统不构成投资建议；Gate 未通过不得投入真钱。
