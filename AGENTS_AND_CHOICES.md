# Agent Map and User-Facing Choices

Companion to PLAN.md. Lists every agent, its I/O type, and every user-configurable option with its allowed values and defaults.

## Agent Map

### Shared agents (run once, public data)

| ID | Agent | Type | Input | Output |
|----|-------|------|-------|--------|
| A1 | Market Data | Code | exchange + symbol + timeframe | `MarketSnapshot` |
| A2 | Regime Classifier | Code | `MarketSnapshot` | `RegimeLabel` |
| A3 | News & Sentiment | LLM (Haiku) + Code | raw news items | `NewsEvent[]`, `NewsState` |
| A4 | Smart Money | Code + LLM summary | Hyperliquid leaderboard | `SmartMoneyState` |

### Per-user agents (inside each user's bot worker)

| ID | Agent | Type | Input | Output |
|----|-------|------|-------|--------|
| U1 | Strategy agents (one per enabled strategy) | Code | `MarketSnapshot` + `RegimeLabel` | `Signal` |
| U2 | Coordinator (Portfolio Manager) | Code + LLM (Sonnet) | `Signal[]` + `NewsState` + `SmartMoneyState` | `TradeProposal` |
| U3 | Risk Manager | Code only | `TradeProposal` + user limits | `RiskDecision` |
| U4 | Execution | Code | `RiskDecision` + exchange connection | order IDs |
| U5 | Reflection / Learning | LLM (Sonnet), offline | `DecisionRecord[]` + outcomes | journal entries + param proposals |
| U6 | Reporter | Code + LLM | `DecisionRecord[]` + positions | alerts + summaries |

---

## User-Facing Choices

### Dimension 1: Goal (investment horizon)

| Value | ID | Holding period | Timeframe | Strategies available | Instruments |
|-------|----|----------------|-----------|---------------------|-------------|
| Long-term investing | `long_term` | months–years | 1d / 1w | `rebalance_core`, `dca_trend`, `tsmom` (slow) | Spot only |
| Swing trading | `swing` | days–weeks | 4h / 1d | `tsmom`, `breakout`, `grid_range`, `dca_trend` | Spot only (MVP) |
| Active trading | `active` | hours–days | 1h / 4h | `breakout`, `mean_rev`, `grid_range`, `tsmom` (fast) | Spot only (MVP) |

Active trading shows a warning: research shows intraday rules underperform daily ones and fees compound quickly.

### Dimension 2: Risk style

| Value | ID | Max alloc/asset | Max positions | Risk/trade | Daily loss limit | Kill switch DD |
|-------|----|-----------------|---------------|------------|-----------------|----------------|
| Conservative | `conservative` | 40% | 3 | 0.5% | 2% | 10% |
| Balanced | `balanced` | 50% | 5 | 1% | 4% | 20% |
| Aggressive | `aggressive` | 60% | 8 | 2% | 7% | 30% |

Users can tighten these limits but never loosen them past the preset values.

### Dimension 3: Schedule

| Value | ID | Description |
|-------|----|-------------|
| 24/7 autopilot | `always_on` | Default. Bot acts whenever a signal fires. |
| Custom windows | `windowed` | User sets days + hours for opening new positions. Stops always active. |
| Contribution schedule | `contribution` | Long-term only. E.g. invest 50 EUR every Monday. |

### Strategy catalogue

| ID | Name | Best regime | Allowed goals | Allowed styles |
|----|------|-------------|---------------|----------------|
| `rebalance_core` | Target-weight rebalancing | any | long_term | all |
| `dca_trend` | DCA with trend filter | any | all | all |
| `tsmom` | EMA time-series momentum, vol-scaled | trending | all | balanced, aggressive |
| `breakout` | Donchian breakout + ATR stop | trending | swing, active | balanced, aggressive |
| `grid_range` | Spot grid (ranging markets only) | ranging | swing, active | balanced, aggressive |
| `mean_rev` | RSI/Bollinger mean reversion | ranging | active | aggressive |
| `hold_baseline` | Buy and hold (benchmark) | — | — | — |

### Other per-bot settings

| Setting | ID | Options | Default |
|---------|----|---------|---------|
| Strategy selection mode | `strategy_mode` | `auto`, `recommended`, `manual` | `auto` |
| Approval mode | `approval_mode` | `autonomous`, `copilot` | `copilot` (first 14 days) |
| Base currency | `base_currency` | `USDT`, `USDC`, `EUR` | `USDT` |
| News protection | `news_protection` | `on`, `off` | `on` |
| Smart-money signal | `smart_money` | `on`, `off` | `off` (until Phase 3 gate) |
| Notifications | `notifications` | `every_trade`, `daily_summary`, `alerts_only` | `daily_summary` |

### Supported exchanges (MVP)

| Exchange | ID | Testnet available | Kosovo access confirmed |
|----------|----|-------------------|------------------------|
| Binance | `binance` | Yes (testnet.binance.vision) | TBC — verify before Phase 6 |
| Bybit | `bybit` | Yes (testnet.bybit.com) | TBC — verify before Phase 6 |

### Supported assets (MVP curated list — liquid majors only)

`BTC/USDT`, `ETH/USDT`, `SOL/USDT`, `BNB/USDT`, `XRP/USDT`, `ADA/USDT`, `AVAX/USDT`, `DOT/USDT`

No microcaps or memecoins in MVP.
