"""Event-driven backtester package."""

from trendscend.backtester.engine import BacktestEngine
from trendscend.backtester.fees import MAKER_FEE, SLIPPAGE_PER_SIDE, TAKER_FEE, FeeModel
from trendscend.backtester.models import BacktestResult, OrderSide, TradeRecord
from trendscend.backtester.portfolio import InsufficientFundsError, Portfolio

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "FeeModel",
    "InsufficientFundsError",
    "MAKER_FEE",
    "OrderSide",
    "Portfolio",
    "SLIPPAGE_PER_SIDE",
    "TAKER_FEE",
    "TradeRecord",
]
