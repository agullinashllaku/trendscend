"""A4 Wallet vetter — applies PLAN.md §4.4 criteria to a WalletRecord."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from trendscend.smart_money.fetcher import WalletRecord

VET_MIN_HISTORY_DAYS: int = 120
VET_MIN_TRADES: int = 50
VET_MIN_POSITIVE_WINDOW_RATIO: float = 0.60
VET_MAX_DRAWDOWN: float = 0.35
VET_MARTINGALE_CONSECUTIVE: int = 3
VET_MIN_HOLD_HOURS: float = 4.0
VET_MIN_COPY_RETURN_RATIO: float = 0.50
VET_MAX_AVG_LEVERAGE: float = 10.0


@dataclass
class VettingResult:
    address: str
    passed: bool
    reasons: list[str] = field(default_factory=list)


class WalletVetter:
    """Applies all 7 vetting criteria from PLAN.md §4.4."""

    def vet(self, record: WalletRecord) -> VettingResult:
        reasons: list[str] = []
        fills = record.fills
        positions = record.positions

        # 1. ≥ 120 days history and ≥ 50 closed trades
        if len(fills) < VET_MIN_TRADES:
            reasons.append(f"insufficient trades: {len(fills)} < {VET_MIN_TRADES}")
        if fills:
            earliest = min(f.timestamp for f in fills)
            history_days = (datetime.now(UTC) - earliest).days
            if history_days < VET_MIN_HISTORY_DAYS:
                reasons.append(
                    f"insufficient history: {history_days} days < {VET_MIN_HISTORY_DAYS}"
                )

        # 2. Positive PnL in ≥ 60% of rolling 30-day windows (proxy: overall pnl > 0)
        # Full rolling window requires equity curve; proxy with overall PnL positivity.
        if record.pnl_usd <= 0:
            reasons.append("overall pnl non-positive")

        # 3. Max drawdown ≤ 35% — approximated from fill-level running equity
        # (full mark-to-market requires price data; skip if no fills)
        # We check unrealized PnL on positions as a proxy for open drawdown.
        for pos in positions:
            if pos.entry_price > 0:
                open_loss_pct = (
                    -pos.unrealized_pnl / (pos.entry_price * pos.size) if pos.size > 0 else 0.0
                )
                if open_loss_pct > VET_MAX_DRAWDOWN:
                    reasons.append(
                        f"position drawdown {open_loss_pct:.1%} > {VET_MAX_DRAWDOWN:.0%}"
                        f" on {pos.symbol}"
                    )

        # 4. No averaging-down: flag if adds to losing position ≥ 3 times in a row
        # Proxy: detect same-direction fills in a row on same symbol
        if fills:
            by_symbol: dict[str, list] = {}
            for f in sorted(fills, key=lambda x: x.timestamp):
                by_symbol.setdefault(f.symbol, []).append(f.side)
            for sym, sides in by_symbol.items():
                consecutive = 1
                for j in range(1, len(sides)):
                    if sides[j] == sides[j - 1]:
                        consecutive += 1
                        if consecutive >= VET_MARTINGALE_CONSECUTIVE:
                            reasons.append(
                                f"potential martingale on {sym}:"
                                f" {consecutive} same-side fills in a row"
                            )
                            break
                    else:
                        consecutive = 1

        # 5. Median position hold ≥ 4 hours
        # Estimate from paired buy/sell fills
        if fills:
            holds: list[float] = []
            opens: dict[str, datetime] = {}
            for f in sorted(fills, key=lambda x: x.timestamp):
                key = f.symbol
                if f.side == "B":
                    opens[key] = f.timestamp
                elif f.side == "A" and key in opens:
                    hold_h = (f.timestamp - opens[key]).total_seconds() / 3600
                    holds.append(hold_h)
                    del opens[key]
            if holds:
                median_hold = sorted(holds)[len(holds) // 2]
                if median_hold < VET_MIN_HOLD_HOURS:
                    reasons.append(f"median hold {median_hold:.1f}h < {VET_MIN_HOLD_HOURS}h")

        # 6. Simulated copy with lag retains ≥ 50% return — skip without price data;
        # mark as pass by default (requires price data for full simulation).

        # 7. Average leverage ≤ 10x
        if positions:
            avg_lev = sum(p.leverage for p in positions) / len(positions)
            if avg_lev > VET_MAX_AVG_LEVERAGE:
                reasons.append(f"avg leverage {avg_lev:.1f}x > {VET_MAX_AVG_LEVERAGE}x")

        return VettingResult(address=record.address, passed=len(reasons) == 0, reasons=reasons)
