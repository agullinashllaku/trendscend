# Phase 2 Regime Classifier Validation

> Generated: 2026-10-15 19:00 UTC
> Data: BTC/USDT 100-bar fixture (4h bars), fast config (adx_period=5)

## Forward Returns by Regime (1-bar horizon)

| Regime | Stats |
|--------|-------|
| `trending_up` | n=32, mean=0.35%, hit_rate=78% |
| `trending_down` | n=28, mean=-0.76%, hit_rate=18% |
| `ranging` | n=25, mean=1.07%, hit_rate=96% |
| `high_vol_chaos` | n=13, mean=-0.12%, hit_rate=46% |

## Interpretation

- **trending_up**: Mean return should be positive (momentum strategies benefit).
- **trending_down**: Mean return should be negative (trend strategies go flat/short).
- **ranging**: Returns near zero with lower variance (grid/mean-rev strategies benefit).
- **high_vol_chaos**: High variance; risk manager should reduce exposure.

## Conclusion

✅ trending_up mean return is higher than trending_down — classifier directional signal is valid.
Ranging mean |return| = 1.17% — ℹ️ above 0.5%; regime boundary may need tuning with more data.
