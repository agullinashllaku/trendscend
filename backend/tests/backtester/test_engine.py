"""BacktestEngine tests — written before implementation (TDD)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from trendscend.backtester.engine import BacktestEngine
from trendscend.backtester.models import BacktestResult
from trendscend.schemas.market import OHLCV
from trendscend.schemas.signals import Signal

# ── helpers ──────────────────────────────────────────────────────────────────


def make_candles(
    n: int,
    start_price: Decimal = Decimal("100"),
    step: Decimal = Decimal("1"),
    timeframe_hours: int = 24,
) -> list[OHLCV]:
    """Create n candles with rising close prices."""
    candles = []
    base_time = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(n):
        price = start_price + step * i
        candles.append(
            OHLCV(
                timestamp=base_time + timedelta(hours=timeframe_hours * i),
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=Decimal("1000"),
            )
        )
    return candles


class StrategyProtocol(Protocol):
    def generate_signal(self, symbol: str, candles: list[OHLCV]) -> Signal: ...


# ── Strategy stubs ────────────────────────────────────────────────────────────


class AlwaysLongStrategy:
    """Always returns target_exposure=1.0 (fully long)."""

    strategy_id = "always_long"

    def generate_signal(self, symbol: str, candles: list[OHLCV]) -> Signal:
        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            target_exposure=1.0,
            conviction=1.0,
            timeframe="1d",
            generated_at=candles[-1].timestamp if candles else datetime.now(UTC),
        )


class HoldStrategy:
    """Always returns target_exposure=0.0 (no position)."""

    strategy_id = "hold"

    def generate_signal(self, symbol: str, candles: list[OHLCV]) -> Signal:
        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            target_exposure=0.0,
            conviction=1.0,
            timeframe="1d",
            generated_at=candles[-1].timestamp if candles else datetime.now(UTC),
        )


class SpyStrategy:
    """Records the candle history passed at each step (for no-lookahead test)."""

    strategy_id = "spy"
    calls: list[tuple[int, list[OHLCV]]]

    def __init__(self) -> None:
        self.calls = []

    def generate_signal(self, symbol: str, candles: list[OHLCV]) -> Signal:
        self.calls.append((len(candles), list(candles)))
        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            target_exposure=0.0,
            conviction=0.0,
            timeframe="1d",
            generated_at=candles[-1].timestamp if candles else datetime.now(UTC),
        )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestEngineGolden:
    def test_always_long_rising_series_produces_profit(self) -> None:
        """Engine with constant 'always fully long' strategy on 20-bar rising series
        should produce final_equity > initial_capital and num_trades >= 1."""
        candles = make_candles(20, start_price=Decimal("100"), step=Decimal("5"))
        engine = BacktestEngine()
        result = engine.run(
            candles=candles,
            strategy=AlwaysLongStrategy(),
            initial_capital=Decimal("10000"),
            exchange_id="binance",
            timeframe="1d",
            symbol="BTC/USDT",
        )
        assert isinstance(result, BacktestResult)
        assert result.final_equity > result.initial_capital
        assert result.num_trades >= 1

    def test_hold_strategy_returns_initial_capital(self) -> None:
        """Hold strategy (never trades) should leave equity close to initial capital."""
        candles = make_candles(10)
        engine = BacktestEngine()
        result = engine.run(
            candles=candles,
            strategy=HoldStrategy(),
            initial_capital=Decimal("10000"),
            exchange_id="binance",
            timeframe="1d",
            symbol="BTC/USDT",
        )
        assert result.num_trades == 0
        assert result.final_equity == result.initial_capital


class TestEngineNoLookahead:
    def test_signal_at_bar_n_sees_only_closed_bars(self) -> None:
        """No-lookahead: signal at iteration i sees only candles[0..i-1]."""
        n = 10
        candles = make_candles(n)
        spy = SpyStrategy()
        engine = BacktestEngine()
        engine.run(
            candles=candles,
            strategy=spy,
            initial_capital=Decimal("10000"),
            exchange_id="binance",
            timeframe="1d",
            symbol="BTC/USDT",
        )
        # Loop runs for i in range(1, n), so n-1 calls
        assert len(spy.calls) == n - 1
        # First call: spy sees 1 candle (candles[:1])
        assert spy.calls[0][0] == 1
        # Second call: spy sees 2 candles
        assert spy.calls[1][0] == 2
        # Last call: sees n-1 candles
        assert spy.calls[-1][0] == n - 1
        # The last candle seen at any call is a CLOSED bar — not the current open bar
        for call_len, call_candles in spy.calls:
            assert call_len == len(call_candles)


class TestEngineFees:
    def test_fees_reduce_return_vs_price_return(self) -> None:
        """With fees, final_equity < what you'd get from pure price appreciation."""
        candles = make_candles(10, start_price=Decimal("100"), step=Decimal("10"))
        engine = BacktestEngine()
        result = engine.run(
            candles=candles,
            strategy=AlwaysLongStrategy(),
            initial_capital=Decimal("10000"),
            exchange_id="binance",
            timeframe="1d",
            symbol="BTC/USDT",
        )
        # Price return: last/first = 190/105 ≈ 1.81 — with fees actual return must be less
        price_ratio = candles[-1].close / candles[1].open  # approximate buy price
        theoretical_equity = result.initial_capital * price_ratio
        assert result.final_equity < theoretical_equity


class TestEngineResult:
    def setup_method(self) -> None:
        candles = make_candles(20, start_price=Decimal("1000"), step=Decimal("10"))
        engine = BacktestEngine()
        self.result = engine.run(
            candles=candles,
            strategy=AlwaysLongStrategy(),
            initial_capital=Decimal("10000"),
            exchange_id="binance",
            timeframe="1d",
            symbol="BTC/USDT",
        )

    def test_num_trades_non_negative(self) -> None:
        assert self.result.num_trades >= 0

    def test_max_drawdown_in_zero_one(self) -> None:
        assert 0.0 <= self.result.max_drawdown <= 1.0

    def test_win_rate_in_zero_one(self) -> None:
        if self.result.num_trades > 0:
            assert 0.0 <= self.result.win_rate <= 1.0

    def test_equity_curve_has_entries(self) -> None:
        assert len(self.result.equity_curve) > 0

    def test_initial_and_final_equity_are_decimal(self) -> None:
        assert isinstance(self.result.initial_capital, Decimal)
        assert isinstance(self.result.final_equity, Decimal)

    def test_result_fields_populated(self) -> None:
        assert self.result.strategy_id == "always_long"
        assert self.result.symbol == "BTC/USDT"
        assert self.result.exchange_id == "binance"
        assert self.result.timeframe == "1d"

    def test_final_equity_equals_last_portfolio_equity(self) -> None:
        """BacktestResult.final_equity must equal portfolio equity at last bar."""
        if self.result.equity_curve:
            last_equity = self.result.equity_curve[-1][1]
            assert self.result.final_equity == last_equity


class TestEngineMetrics:
    def setup_method(self) -> None:
        candles = make_candles(30, start_price=Decimal("1000"), step=Decimal("5"))
        engine = BacktestEngine()
        self.result = engine.run(
            candles=candles,
            strategy=AlwaysLongStrategy(),
            initial_capital=Decimal("10000"),
            exchange_id="binance",
            timeframe="1d",
            symbol="BTC/USDT",
        )

    def test_cagr_is_float(self) -> None:
        assert isinstance(self.result.cagr, float)

    def test_sharpe_is_float(self) -> None:
        assert isinstance(self.result.sharpe_ratio, float)

    def test_sortino_is_float(self) -> None:
        assert isinstance(self.result.sortino_ratio, float)

    def test_profit_factor_non_negative(self) -> None:
        # profit_factor >= 0 always (or inf if no losing trades)
        assert self.result.profit_factor >= 0.0
