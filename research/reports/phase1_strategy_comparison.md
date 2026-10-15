# Phase 1 Strategy Comparison — Walk-Forward OOS Report

**Generated:** 2026-10-14T12:19:17.674956+00:00
**Period:** 2021-01-01 → 2025-01-01  |  Train 12m / Test 3m  |  12 OOS windows
**Assets (8):** BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT, ADA/USDT, AVAX/USDT, DOT/USDT
**Fee model:** Round-trip fee model: Binance taker 0.1% + 0.05% slippage per side = 0.15% per side

## Aggregated OOS Metrics (mean across assets and windows)

| Strategy | OOS Sharpe | OOS Max DD | OOS CAGR | vs Hold (Sharpe) | vs Hold (DD) |
|----------|-----------|-----------|---------|-----------------|-------------|
| hold_baseline | 0.131 | 35.3% | 1093.2% | — | — |
| rebalance_core | 0.131 | 35.3% | 1093.2% | +0.000 | +0.0pp |
| dca_trend | -0.089 | 14.4% | 370.4% | -0.219 | +20.9pp |
| tsmom | 0.003 | 9.4% | 17.8% | -0.127 | +25.9pp |
| breakout | 0.083 | 23.7% | 504.8% | -0.047 | +11.6pp |
| grid_range | -0.430 | 13.5% | -12.9% | -0.561 | +21.8pp |

## Per-Asset Detail

### BTC/USDT

| Strategy | OOS Sharpe | OOS Max DD | OOS CAGR | Splits |
|----------|-----------|-----------|---------|--------|
| hold_baseline | 0.290 | 24.2% | 178.2% | 12 |
| rebalance_core | 0.290 | 24.2% | 178.2% | 12 |
| dca_trend | 0.150 | 10.4% | 56.1% | 12 |
| tsmom | 0.090 | 9.4% | 28.0% | 12 |
| breakout | 0.284 | 15.0% | 134.3% | 12 |
| grid_range | -0.282 | 8.5% | -5.4% | 12 |

### ETH/USDT

| Strategy | OOS Sharpe | OOS Max DD | OOS CAGR | Splits |
|----------|-----------|-----------|---------|--------|
| hold_baseline | 0.158 | 30.9% | 105.0% | 12 |
| rebalance_core | 0.158 | 30.9% | 105.0% | 12 |
| dca_trend | 0.054 | 13.4% | 38.6% | 12 |
| tsmom | -0.150 | 11.0% | 4.5% | 12 |
| breakout | 0.072 | 20.1% | 81.7% | 12 |
| grid_range | -0.675 | 12.7% | -24.3% | 12 |

### SOL/USDT

| Strategy | OOS Sharpe | OOS Max DD | OOS CAGR | Splits |
|----------|-----------|-----------|---------|--------|
| hold_baseline | 0.229 | 44.5% | 4411.9% | 12 |
| rebalance_core | 0.229 | 44.5% | 4411.9% | 12 |
| dca_trend | 0.022 | 15.4% | 400.0% | 12 |
| tsmom | 0.129 | 9.3% | 32.6% | 12 |
| breakout | 0.257 | 29.4% | 1156.9% | 12 |
| grid_range | -0.017 | 14.0% | 19.0% | 12 |

### BNB/USDT

| Strategy | OOS Sharpe | OOS Max DD | OOS CAGR | Splits |
|----------|-----------|-----------|---------|--------|
| hold_baseline | 0.176 | 28.1% | 155.2% | 12 |
| rebalance_core | 0.176 | 28.1% | 155.2% | 12 |
| dca_trend | -0.345 | 14.3% | 91.5% | 12 |
| tsmom | -0.153 | 10.6% | 4.3% | 12 |
| breakout | 0.069 | 18.6% | 37.4% | 12 |
| grid_range | -0.608 | 12.7% | -24.0% | 12 |

### XRP/USDT

| Strategy | OOS Sharpe | OOS Max DD | OOS CAGR | Splits |
|----------|-----------|-----------|---------|--------|
| hold_baseline | 0.216 | 30.9% | 1054.2% | 12 |
| rebalance_core | 0.216 | 30.9% | 1054.2% | 12 |
| dca_trend | -0.370 | 17.9% | 904.3% | 12 |
| tsmom | -0.008 | 10.1% | 19.1% | 12 |
| breakout | 0.003 | 25.7% | 1688.8% | 12 |
| grid_range | -0.316 | 13.5% | -14.6% | 12 |

### ADA/USDT

| Strategy | OOS Sharpe | OOS Max DD | OOS CAGR | Splits |
|----------|-----------|-----------|---------|--------|
| hold_baseline | 0.055 | 38.2% | 427.9% | 12 |
| rebalance_core | 0.055 | 38.2% | 427.9% | 12 |
| dca_trend | -0.168 | 13.2% | 457.3% | 12 |
| tsmom | 0.091 | 8.2% | 21.4% | 12 |
| breakout | 0.024 | 26.5% | 199.6% | 12 |
| grid_range | -0.723 | 17.5% | -35.0% | 12 |

### AVAX/USDT

| Strategy | OOS Sharpe | OOS Max DD | OOS CAGR | Splits |
|----------|-----------|-----------|---------|--------|
| hold_baseline | -0.001 | 46.0% | 2271.2% | 12 |
| rebalance_core | -0.001 | 46.0% | 2271.2% | 12 |
| dca_trend | -0.065 | 16.3% | 881.6% | 12 |
| tsmom | 0.130 | 8.0% | 24.6% | 12 |
| breakout | 0.118 | 28.3% | 616.5% | 12 |
| grid_range | -0.298 | 15.0% | 2.3% | 12 |

### DOT/USDT

| Strategy | OOS Sharpe | OOS Max DD | OOS CAGR | Splits |
|----------|-----------|-----------|---------|--------|
| hold_baseline | -0.078 | 39.6% | 142.1% | 12 |
| rebalance_core | -0.078 | 39.6% | 142.1% | 12 |
| dca_trend | 0.012 | 14.7% | 133.9% | 12 |
| tsmom | -0.106 | 8.7% | 8.0% | 12 |
| breakout | -0.160 | 26.1% | 123.2% | 12 |
| grid_range | -0.523 | 14.3% | -21.1% | 12 |

## Gate Assessment

**Gate condition:** at least one non-baseline strategy must have higher mean OOS Sharpe
OR ≥5pp lower max drawdown vs `hold_baseline`, across ≥50% of assets.

**Gate: PASSED** — `dca_trend` ≥5pp lower max drawdown on 8/8 assets

| Strategy | Sharpe beats | DD beats | Valid assets | Passes |
|----------|-------------|---------|-------------|--------|
| rebalance_core | 0/8 | 0/8 | 8 | ✗ |
| dca_trend | 1/8 | 8/8 | 8 | ✓ |
| tsmom | 2/8 | 8/8 | 8 | ✓ |
| breakout | 2/8 | 8/8 | 8 | ✓ |
| grid_range | 0/8 | 8/8 | 8 | ✓ |

---

*Round-trip fee model: Binance taker 0.1% + 0.05% slippage per side = 0.15% per side*
