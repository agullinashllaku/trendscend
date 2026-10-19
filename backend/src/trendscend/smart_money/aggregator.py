"""A4 Smart-money aggregator: builds SmartMoneyState from vetted WalletRecords."""

from __future__ import annotations

from datetime import UTC, datetime

from trendscend.schemas.smart_money import SmartMoneyAssetState, SmartMoneyState
from trendscend.smart_money.fetcher import WalletRecord


class SmartMoneyAggregator:
    """Aggregates positioning from vetted wallets into SmartMoneyState."""

    def aggregate(
        self,
        records: list[WalletRecord],
        assets: list[str],
    ) -> SmartMoneyState:
        """Build SmartMoneyState from a list of vetted WalletRecords.

        Args:
            records: Vetted wallet records (already filtered by WalletVetter).
            assets:  Asset symbols to include (e.g. ['BTC/USDT']).

        Returns:
            SmartMoneyState with per-asset net positioning.
        """
        per_asset: dict[str, SmartMoneyAssetState] = {}

        for asset in assets:
            base = asset.split("/")[0]
            long_size = 0.0
            short_size = 0.0

            for rec in records:
                for pos in rec.positions:
                    if pos.symbol == base or pos.symbol == asset:
                        if pos.side == "long":
                            long_size += pos.size
                        else:
                            short_size += pos.size

            total = long_size + short_size
            if total > 0:
                net_long = long_size / total
                net_short = short_size / total
            else:
                net_long = 0.0
                net_short = 0.0

            # Concentration: fraction of total size held by single largest wallet
            wallet_sizes = []
            for rec in records:
                ws = sum(p.size for p in rec.positions if p.symbol == base or p.symbol == asset)
                if ws > 0:
                    wallet_sizes.append(ws)
            concentration = max(wallet_sizes) / total if wallet_sizes and total > 0 else 0.0

            per_asset[asset] = SmartMoneyAssetState(
                symbol=asset,
                net_long_pct=round(net_long, 4),
                net_short_pct=round(net_short, 4),
                change_24h=0.0,
                concentration=round(min(concentration, 1.0), 4),
            )

        return SmartMoneyState(
            per_asset=per_asset,
            vetted_wallet_count=len(records),
            computed_at=datetime.now(UTC),
        )
