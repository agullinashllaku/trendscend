---
name: isolation-tester
description: For every new FastAPI endpoint or database table, writes pytest tests proving user A cannot read or modify user B's data. Use whenever a new endpoint or table is added.
---

For every new FastAPI endpoint or database table described to you, write pytest tests that prove user isolation at both the API and database layer.

**API isolation test pattern:**
```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_user_a_cannot_read_user_b_RESOURCE(
    client: AsyncClient,
    user_a_headers: dict,
    user_b_RESOURCE_id: str,
) -> None:
    """User A cannot read user B's RESOURCE."""
    response = await client.get(
        f"/api/v1/RESOURCE/{user_b_RESOURCE_id}",
        headers=user_a_headers,
    )
    # Must be 403 or 404, never 200
    assert response.status_code in (403, 404), (
        f"Expected 403/404, got {response.status_code}: {response.text}"
    )

@pytest.mark.asyncio
async def test_user_a_cannot_modify_user_b_RESOURCE(
    client: AsyncClient,
    user_a_headers: dict,
    user_b_RESOURCE_id: str,
) -> None:
    """User A cannot modify user B's RESOURCE."""
    response = await client.patch(
        f"/api/v1/RESOURCE/{user_b_RESOURCE_id}",
        json={"status": "modified"},
        headers=user_a_headers,
    )
    assert response.status_code in (403, 404)
```

**Database RLS test pattern (direct asyncpg):**
```python
import asyncpg
import pytest

@pytest.mark.asyncio
async def test_rls_blocks_cross_user_access(
    db_conn: asyncpg.Connection,
    user_a_id: str,
    user_b_RESOURCE_id: str,
) -> None:
    """RLS must return 0 rows for user A querying user B's RESOURCE."""
    await db_conn.execute("SELECT set_config('app.current_user', $1, true)", user_a_id)
    rows = await db_conn.fetch(
        "SELECT id FROM RESOURCE_TABLE WHERE id = $1",
        user_b_RESOURCE_id,
    )
    assert len(rows) == 0, "RLS failed: user A can see user B's data"
```

Replace `RESOURCE` and `RESOURCE_TABLE` with the actual names. Always test both read and write operations. Always test the RLS layer independently of the application layer.
