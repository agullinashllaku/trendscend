"""Portfolio tests — written before implementation (TDD).

Includes both unit tests and Hypothesis property-based tests.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trendscend.backtester.portfolio import InsufficientFundsError, Portfolio

# ── helpers ──────────────────────────────────────────────────────────────────


NOW = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)


def make_portfolio(cash: Decimal = Decimal("10000")) -> Portfolio:
    return Portfolio(initial_cash=cash)


# ── Unit tests ────────────────────────────────────────────────────────────────


class TestPortfolioBuy:
    def test_buy_decreases_cash_by_fill_price_times_size_plus_fee(self) -> None:
        port = make_portfolio(Decimal("10000"))
        price = Decimal("1000")
        size = Decimal("5")
        fee = Decimal("5")  # arbitrary
        port.apply_fill("BTC/USDT", "buy", size, price, fee, NOW)
        expected_cash = Decimal("10000") - price * size - fee
        assert port.cash == expected_cash

    def test_buy_increases_position_by_size(self) -> None:
        port = make_portfolio(Decimal("10000"))
        port.apply_fill("BTC/USDT", "buy", Decimal("2"), Decimal("1000"), Decimal("2"), NOW)
        assert port.positions["BTC/USDT"] == Decimal("2")

    def test_second_buy_accumulates_position(self) -> None:
        port = make_portfolio(Decimal("20000"))
        port.apply_fill("BTC/USDT", "buy", Decimal("2"), Decimal("1000"), Decimal("2"), NOW)
        port.apply_fill("BTC/USDT", "buy", Decimal("3"), Decimal("1000"), Decimal("3"), NOW)
        assert port.positions["BTC/USDT"] == Decimal("5")

    def test_buy_insufficient_funds_raises(self) -> None:
        port = make_portfolio(Decimal("100"))
        with pytest.raises(InsufficientFundsError):
            # Try to buy 1 BTC at 50000 — way more than 100 cash
            port.apply_fill("BTC/USDT", "buy", Decimal("1"), Decimal("50000"), Decimal("50"), NOW)

    def test_buy_exactly_at_limit_succeeds(self) -> None:
        # cash = 1000, cost = 1 * 900 + fee 90 = 990 <= 1000
        port = make_portfolio(Decimal("1000"))
        port.apply_fill("BTC/USDT", "buy", Decimal("1"), Decimal("900"), Decimal("90"), NOW)
        assert port.cash == Decimal("10")

    def test_buy_that_makes_cash_exactly_zero_succeeds(self) -> None:
        port = make_portfolio(Decimal("1000"))
        # cost + fee = 1000 exactly
        port.apply_fill("BTC/USDT", "buy", Decimal("1"), Decimal("990"), Decimal("10"), NOW)
        assert port.cash == Decimal("0")


class TestPortfolioSell:
    def setup_method(self) -> None:
        self.port = make_portfolio(Decimal("10000"))
        # First buy some BTC
        self.port.apply_fill("BTC/USDT", "buy", Decimal("2"), Decimal("1000"), Decimal("2"), NOW)

    def test_sell_increases_cash_by_fill_price_times_size_minus_fee(self) -> None:
        cash_before = self.port.cash
        price = Decimal("1200")
        size = Decimal("1")
        fee = Decimal("1.2")
        self.port.apply_fill("BTC/USDT", "sell", size, price, fee, NOW)
        expected_cash = cash_before + price * size - fee
        assert self.port.cash == expected_cash

    def test_sell_decreases_position_by_size(self) -> None:
        self.port.apply_fill("BTC/USDT", "sell", Decimal("1"), Decimal("1200"), Decimal("1"), NOW)
        assert self.port.positions["BTC/USDT"] == Decimal("1")

    def test_full_sell_leaves_zero_position(self) -> None:
        self.port.apply_fill("BTC/USDT", "sell", Decimal("2"), Decimal("1200"), Decimal("2"), NOW)
        assert self.port.positions.get("BTC/USDT", Decimal("0")) == Decimal("0")


class TestPortfolioEquity:
    def test_equity_equals_cash_plus_position_value(self) -> None:
        port = make_portfolio(Decimal("10000"))
        port.apply_fill("BTC/USDT", "buy", Decimal("2"), Decimal("1000"), Decimal("2"), NOW)
        mark_price = Decimal("1500")
        equity = port.equity({"BTC/USDT": mark_price})
        expected = port.cash + Decimal("2") * mark_price
        assert equity == expected

    def test_equity_is_decimal(self) -> None:
        port = make_portfolio(Decimal("10000"))
        eq = port.equity({"BTC/USDT": Decimal("50000")})
        assert isinstance(eq, Decimal)

    def test_equity_with_no_positions_equals_cash(self) -> None:
        port = make_portfolio(Decimal("5000"))
        assert port.equity({}) == Decimal("5000")

    def test_equity_with_multiple_positions(self) -> None:
        port = make_portfolio(Decimal("10000"))
        port.apply_fill("BTC/USDT", "buy", Decimal("1"), Decimal("2000"), Decimal("2"), NOW)
        port.apply_fill(
            "ETH/USDT",
            "buy",
            Decimal("5"),
            Decimal("200"),
            Decimal("1"),
            NOW,
        )
        prices = {"BTC/USDT": Decimal("2500"), "ETH/USDT": Decimal("250")}
        equity = port.equity(prices)
        expected = port.cash + Decimal("1") * Decimal("2500") + Decimal("5") * Decimal("250")
        assert equity == expected


class TestPortfolioDrawdown:
    def test_max_drawdown_starts_at_zero(self) -> None:
        port = make_portfolio(Decimal("10000"))
        assert port.max_drawdown == 0.0

    def test_max_drawdown_non_negative(self) -> None:
        port = make_portfolio(Decimal("10000"))
        port.mark_to_market({"BTC/USDT": Decimal("50000")}, NOW)
        port.apply_fill("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"), Decimal("5"), NOW)
        port.mark_to_market({"BTC/USDT": Decimal("40000")}, NOW)
        assert port.max_drawdown >= 0.0

    def test_max_drawdown_not_exceeds_one(self) -> None:
        port = make_portfolio(Decimal("10000"))
        port.mark_to_market({}, NOW)
        # Simulate a large drawdown (buy and price drops significantly)
        port.apply_fill("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"), Decimal("50"), NOW)
        port.mark_to_market({"BTC/USDT": Decimal("1")}, NOW)
        assert port.max_drawdown <= 1.0

    def test_max_drawdown_tracks_worst_case(self) -> None:
        port = make_portfolio(Decimal("10000"))
        port.mark_to_market({}, NOW)
        # Buy some BTC
        port.apply_fill("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"), Decimal("5"), NOW)
        # Price goes up
        port.mark_to_market({"BTC/USDT": Decimal("60000")}, NOW)
        dd_after_rise = port.max_drawdown
        # Price drops below initial
        port.mark_to_market({"BTC/USDT": Decimal("40000")}, NOW)
        dd_after_drop = port.max_drawdown
        assert dd_after_drop >= dd_after_rise


class TestMarkToMarket:
    def test_mark_to_market_records_equity_history(self) -> None:
        port = make_portfolio(Decimal("10000"))
        port.mark_to_market({}, NOW)
        assert len(port.equity_history) == 1

    def test_mark_to_market_updates_peak(self) -> None:
        port = make_portfolio(Decimal("10000"))
        port.mark_to_market({}, NOW)
        initial_peak = port.peak_equity
        # Buy BTC and price rises — peak should update
        port.apply_fill("BTC/USDT", "buy", Decimal("0.1"), Decimal("50000"), Decimal("5"), NOW)
        port.mark_to_market({"BTC/USDT": Decimal("100000")}, NOW)
        assert port.peak_equity > initial_peak


# ── Hypothesis property-based tests ──────────────────────────────────────────


# Strategy: generate a sequence of buys (size > 0, each affordable)
# then sells (size <= position), using a fixed mark price.
valid_size = st.decimals(
    min_value=Decimal("0.001"),
    max_value=Decimal("1"),
    allow_nan=False,
    allow_infinity=False,
).map(lambda d: d.quantize(Decimal("0.001")))
valid_price = st.decimals(
    min_value=Decimal("1"),
    max_value=Decimal("100000"),
    allow_nan=False,
    allow_infinity=False,
).map(lambda d: d.quantize(Decimal("0.01")))


@settings(max_examples=50)
@given(
    fills=st.lists(
        st.tuples(valid_size, valid_price),
        min_size=1,
        max_size=10,
    ),
    mark_price=valid_price,
)
def test_hypothesis_balance_identity(
    fills: list[tuple[Decimal, Decimal]], mark_price: Decimal
) -> None:
    """Equity always equals cash + position_size * mark_price (balance identity)."""
    symbol = "BTC/USDT"
    initial_cash = Decimal("1000000")  # large enough for all buys
    port = Portfolio(initial_cash=initial_cash)
    ts = datetime(2024, 1, 1, tzinfo=UTC)

    for size, price in fills:
        fee = price * size * Decimal("0.001")
        cost = price * size + fee
        if cost <= port.cash:
            port.apply_fill(symbol, "buy", size, price, fee, ts)

    # Balance identity: equity = cash + sum(pos * mark_price)
    eq = port.equity({symbol: mark_price})
    position = port.positions.get(symbol, Decimal("0"))
    expected = port.cash + position * mark_price
    assert eq == expected


@settings(max_examples=50)
@given(
    fills=st.lists(
        st.tuples(valid_size, valid_price),
        min_size=0,
        max_size=15,
    )
)
def test_hypothesis_cash_never_negative(fills: list[tuple[Decimal, Decimal]]) -> None:
    """Cash must never go negative after any sequence of fills."""
    symbol = "BTC/USDT"
    port = Portfolio(initial_cash=Decimal("1000000"))
    ts = datetime(2024, 1, 1, tzinfo=UTC)

    for size, price in fills:
        fee = price * size * Decimal("0.001")
        cost = price * size + fee
        if cost <= port.cash:
            port.apply_fill(symbol, "buy", size, price, fee, ts)

    assert port.cash >= Decimal("0")


@settings(max_examples=50)
@given(
    fills=st.lists(
        st.tuples(valid_size, valid_price),
        min_size=1,
        max_size=10,
    ),
    mark_prices=st.lists(valid_price, min_size=1, max_size=5),
)
def test_hypothesis_max_drawdown_in_range(
    fills: list[tuple[Decimal, Decimal]], mark_prices: list[Decimal]
) -> None:
    """max_drawdown is always >= 0 and <= 1.0."""
    symbol = "BTC/USDT"
    port = Portfolio(initial_cash=Decimal("1000000"))
    ts = datetime(2024, 1, 1, tzinfo=UTC)

    port.mark_to_market({}, ts)
    for size, price in fills:
        fee = price * size * Decimal("0.001")
        cost = price * size + fee
        if cost <= port.cash:
            port.apply_fill(symbol, "buy", size, price, fee, ts)
        for mp in mark_prices:
            port.mark_to_market({symbol: mp}, ts)

    assert 0.0 <= port.max_drawdown <= 1.0
