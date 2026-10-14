# Task 2 - Candle Repository Review Fixes

## Fix Applied (post-review)

- **candles migration**: Added comment documenting shared/public status per PLAN.md §7
  - File: `backend/src/trendscend/db/migrations/001_candles.sql`
  - Comment clarifies that candles is a shared reference table with no RLS requirement

- **test: fixed unhelpful assertion**: Replaced `r.timestamp is not None` with meaningful range check
  - File: `backend/tests/db/test_candle_repo.py`
  - Test: `test_fetch_candles_filters_by_exchange_symbol_timeframe`
  - Changed to: `assert all(r.timestamp >= BASE_TIME for r in results)`

- **test: added unit test for empty insert**: New test exercises early-return guard in `insert_candles([])`
  - File: `backend/tests/db/test_candle_repo.py`
  - New test: `TestInsertCandlesEmptyList::test_insert_empty_returns_zero`
  - No DB dependency; validates that empty list returns 0 immediately

## Test Results

- **Total**: 36 passed, 8 skipped
- **Coverage**: 92.63% (exceeds 80% requirement)
- **Candle repo tests**: 8 passed, 8 skipped (integration tests require TEST_DATABASE_URL)
