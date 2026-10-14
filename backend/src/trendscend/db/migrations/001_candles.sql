-- Candle (OHLCV) hypertable for TimescaleDB
CREATE TABLE IF NOT EXISTS candles (
    time        TIMESTAMPTZ    NOT NULL,
    exchange    TEXT           NOT NULL,
    symbol      TEXT           NOT NULL,
    timeframe   TEXT           NOT NULL,
    open        NUMERIC(24, 8) NOT NULL,
    high        NUMERIC(24, 8) NOT NULL,
    low         NUMERIC(24, 8) NOT NULL,
    close       NUMERIC(24, 8) NOT NULL,
    volume      NUMERIC(24, 8) NOT NULL
);

SELECT create_hypertable('candles', 'time', if_not_exists => TRUE);

CREATE UNIQUE INDEX IF NOT EXISTS candles_ux
    ON candles (exchange, symbol, timeframe, time DESC);
