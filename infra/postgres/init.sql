-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable pgcrypto (used for key fingerprinting)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Verify extensions loaded
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
    RAISE EXCEPTION 'TimescaleDB extension failed to load';
  END IF;
END
$$;
