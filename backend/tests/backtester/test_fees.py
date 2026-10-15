"""Fee and slippage model tests — written before implementation (TDD)."""

from decimal import Decimal

import pytest

from trendscend.backtester.fees import (
    MAKER_FEE,
    SLIPPAGE_PER_SIDE,
    TAKER_FEE,
    FeeModel,
)


class TestFeeConstants:
    def test_taker_fee_binance(self) -> None:
        assert TAKER_FEE["binance"] == Decimal("0.001")

    def test_taker_fee_bybit(self) -> None:
        assert TAKER_FEE["bybit"] == Decimal("0.001")

    def test_maker_fee_binance(self) -> None:
        assert MAKER_FEE["binance"] == Decimal("0.0008")

    def test_maker_fee_bybit(self) -> None:
        assert MAKER_FEE["bybit"] == Decimal("0.0002")

    def test_slippage_per_side(self) -> None:
        assert SLIPPAGE_PER_SIDE == Decimal("0.0005")

    def test_constants_are_decimal(self) -> None:
        assert isinstance(TAKER_FEE["binance"], Decimal)
        assert isinstance(TAKER_FEE["bybit"], Decimal)
        assert isinstance(MAKER_FEE["binance"], Decimal)
        assert isinstance(MAKER_FEE["bybit"], Decimal)
        assert isinstance(SLIPPAGE_PER_SIDE, Decimal)


class TestFeeModelValidation:
    def test_unknown_exchange_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown exchange"):
            FeeModel(exchange_id="kraken")

    def test_unknown_exchange_error_message_lists_supported(self) -> None:
        with pytest.raises(ValueError, match="binance"):
            FeeModel(exchange_id="kraken")


class TestFeeModelBinance:
    def setup_method(self) -> None:
        self.model = FeeModel(exchange_id="binance")

    def test_taker_fee_returns_size_times_price_times_rate(self) -> None:
        size = Decimal("2")
        price = Decimal("50000")
        fee = self.model.compute_fee(size, price, "taker")
        expected = size * price * Decimal("0.001")
        assert fee == expected

    def test_maker_fee_returns_size_times_price_times_rate(self) -> None:
        size = Decimal("2")
        price = Decimal("50000")
        fee = self.model.compute_fee(size, price, "maker")
        expected = size * price * Decimal("0.0008")
        assert fee == expected

    def test_fee_is_decimal(self) -> None:
        fee = self.model.compute_fee(Decimal("1"), Decimal("100"), "taker")
        assert isinstance(fee, Decimal)

    def test_taker_fee_correct_value(self) -> None:
        # 1 BTC at 50000: fee = 50000 * 0.001 = 50
        fee = self.model.compute_fee(Decimal("1"), Decimal("50000"), "taker")
        assert fee == Decimal("50")

    def test_maker_fee_correct_value(self) -> None:
        # 1 BTC at 50000: fee = 50000 * 0.0008 = 40
        fee = self.model.compute_fee(Decimal("1"), Decimal("50000"), "maker")
        assert fee == Decimal("40")

    def test_unknown_side_raises(self) -> None:
        with pytest.raises((ValueError, KeyError)):
            self.model.compute_fee(Decimal("1"), Decimal("100"), "unknown")  # type: ignore[arg-type]


class TestFeeModelBybit:
    def setup_method(self) -> None:
        self.model = FeeModel(exchange_id="bybit")

    def test_taker_fee_returns_size_times_price_times_rate(self) -> None:
        size = Decimal("3")
        price = Decimal("20000")
        fee = self.model.compute_fee(size, price, "taker")
        expected = size * price * Decimal("0.001")
        assert fee == expected

    def test_maker_fee_bybit_is_cheaper(self) -> None:
        # Bybit maker fee (0.0002) < Binance maker fee (0.0008)
        binance = FeeModel(exchange_id="binance")
        bybit = FeeModel(exchange_id="bybit")
        notional = Decimal("1") * Decimal("50000")
        assert bybit.compute_fee(Decimal("1"), Decimal("50000"), "maker") < binance.compute_fee(
            Decimal("1"), Decimal("50000"), "maker"
        )
        assert bybit.compute_fee(Decimal("1"), Decimal("50000"), "maker") == notional * Decimal(
            "0.0002"
        )


class TestSlippage:
    def setup_method(self) -> None:
        self.model = FeeModel(exchange_id="binance")

    def test_buy_slippage_increases_price(self) -> None:
        price = Decimal("50000")
        slipped = self.model.apply_slippage(price, "buy")
        assert slipped == price * (Decimal("1") + SLIPPAGE_PER_SIDE)
        assert slipped > price

    def test_sell_slippage_decreases_price(self) -> None:
        price = Decimal("50000")
        slipped = self.model.apply_slippage(price, "sell")
        assert slipped == price * (Decimal("1") - SLIPPAGE_PER_SIDE)
        assert slipped < price

    def test_slippage_result_is_decimal(self) -> None:
        result = self.model.apply_slippage(Decimal("100"), "buy")
        assert isinstance(result, Decimal)

    def test_buy_slippage_exact_value(self) -> None:
        # 50000 * 1.0005 = 50025
        price = Decimal("50000")
        slipped = self.model.apply_slippage(price, "buy")
        assert slipped == Decimal("50025")

    def test_sell_slippage_exact_value(self) -> None:
        # 50000 * 0.9995 = 49975
        price = Decimal("50000")
        slipped = self.model.apply_slippage(price, "sell")
        assert slipped == Decimal("49975")

    def test_round_trip_cost(self) -> None:
        """Round-trip (buy + sell at same base price) costs 0.003 * initial_price.

        Both sides use taker fee (0.001 each) plus slippage (0.0005 each).
        Total: 2 * (0.001 + 0.0005) = 0.003 of the initial price per unit.
        """
        initial_price = Decimal("50000")
        size = Decimal("1")
        buy_price = self.model.apply_slippage(initial_price, "buy")
        sell_price = self.model.apply_slippage(initial_price, "sell")
        buy_fee = self.model.compute_fee(size, buy_price, "taker")
        sell_fee = self.model.compute_fee(size, sell_price, "taker")

        # Cost = buy_price - sell_price + buy_fee + sell_fee
        # Approximately 0.003 * initial_price
        # buy_price - sell_price = 50000 * 0.001 = 50 (slippage cost, both sides)
        # buy_fee ≈ 50025 * 0.001 = 50.025
        # sell_fee ≈ 49975 * 0.001 = 49.975
        # Total ≈ 50 + 50.025 + 49.975 = 150 = 0.003 * 50000
        total_cost = (buy_price - sell_price) + buy_fee + sell_fee
        expected_approx = initial_price * Decimal("0.003")
        # Allow 1% tolerance for small Decimal arithmetic differences
        assert abs(total_cost - expected_approx) < expected_approx * Decimal("0.01")
