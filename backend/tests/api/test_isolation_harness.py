from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, WebSocket
from httpx import ASGITransport
from starlette.staticfiles import StaticFiles

from tests.api.fakes import FakeRedis
from tests.api.test_app import FakePool, make_settings
from trendscend.api.app import create_app
from trendscend.api.isolation import (
    collect_route_declarations,
    public_route,
    user_scoped_route,
    verify_isolation_test_exists,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]


def real_app() -> FastAPI:
    return create_app(make_settings(), pool=FakePool(), redis=FakeRedis(), trust_injected_pool=True)


def test_no_undeclared_routes_in_real_app() -> None:
    decls = collect_route_declarations(real_app())
    assert decls, "expected at least the health routes"
    undeclared = [(d.path, sorted(d.methods)) for d in decls if d.kind == "undeclared"]
    assert undeclared == []
    by_path = {d.path: d for d in decls}
    assert by_path["/healthz"].kind == "public"
    assert by_path["/healthz"].reason_or_test == "liveness probe"
    assert by_path["/readyz"].reason_or_test == "readiness probe, no user data"


def test_builtin_docs_routes_excluded_only_when_framework_generated() -> None:
    app = FastAPI()
    assert collect_route_declarations(app) == []

    @app.get("/docs/secret")
    async def secret() -> None: ...

    @app.get("/openapi.json/extra")
    async def extra() -> None: ...

    paths = {d.path for d in collect_route_declarations(app) if d.kind == "undeclared"}
    assert paths == {"/docs/secret", "/openapi.json/extra"}


def test_user_defined_route_on_docs_path_is_not_exempt() -> None:
    app = FastAPI(docs_url=None)

    @app.get("/docs")
    async def my_docs() -> None: ...

    kinds = {d.path: d.kind for d in collect_route_declarations(app)}
    assert kinds["/docs"] == "undeclared"


def test_undeclared_route_detected() -> None:
    app = FastAPI()

    @app.get("/bare")
    async def bare() -> None: ...

    @app.post("/ok")
    @public_route("test")
    async def ok() -> None: ...

    decls = {d.path: d for d in collect_route_declarations(app)}
    assert decls["/bare"].kind == "undeclared"
    assert decls["/bare"].methods == frozenset({"GET"})
    assert decls["/ok"].kind == "public"


def test_user_scoped_kind_reported() -> None:
    app = FastAPI()

    @app.get("/mine")
    @user_scoped_route("tests/api/test_x.py::test_iso")
    async def mine() -> None: ...

    (d,) = collect_route_declarations(app)
    assert d.kind == "user_scoped"
    assert d.reason_or_test == "tests/api/test_x.py::test_iso"


def test_mounted_subapp_routes_are_collected() -> None:
    sub = FastAPI()

    @sub.get("/inner")
    async def inner() -> None: ...

    @sub.get("/pub")
    @public_route("x")
    async def pub() -> None: ...

    app = FastAPI()
    app.mount("/sub", sub)
    decls = {d.path: d for d in collect_route_declarations(app)}
    assert decls["/sub/inner"].kind == "undeclared"
    assert decls["/sub/pub"].kind == "public"
    assert not any(
        d.path.startswith("/sub/docs") and d.kind == "undeclared" for d in decls.values()
    )


def test_included_router_routes_are_collected() -> None:
    r = APIRouter()

    @r.get("/x")
    async def x() -> None: ...

    app = FastAPI()
    app.include_router(r, prefix="/v1")
    (d,) = collect_route_declarations(app)
    assert d.path == "/v1/x" and d.kind == "undeclared"


def test_non_router_mount_is_undeclared(tmp_path: Path) -> None:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=tmp_path))
    (d,) = collect_route_declarations(app)
    assert d.path == "/static" and d.kind == "undeclared"


def test_websocket_routes_are_collected_or_rejected() -> None:
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        await sock.close()

    (d,) = collect_route_declarations(app)
    assert d.path == "/ws" and d.kind == "undeclared"


def test_websocket_route_can_be_declared() -> None:
    app = FastAPI()

    @app.websocket("/ws")
    @public_route("demo")
    async def ws(sock: WebSocket) -> None:
        await sock.close()

    (d,) = collect_route_declarations(app)
    assert d.kind == "public"


@pytest.mark.parametrize("reason", ["", "   ", "\t\n"])
def test_public_route_requires_reason(reason: str) -> None:
    with pytest.raises(ValueError):
        public_route(reason)


@pytest.mark.parametrize(
    "node",
    [
        "",
        "test_x.py",
        "test_x.py::",
        "::test_x",
        "test_x.txt::test_a",
        "test_x.py::test_a::extra",
        "test_x.py::not valid",
        "test_x.py::1abc",
        "test_x.py::Class::test_a",
        ".py::test_a",
    ],
)
def test_user_scoped_requires_valid_node_id(node: str) -> None:
    with pytest.raises(ValueError):
        user_scoped_route(node)


def test_both_markers_rejected() -> None:
    async def h() -> None: ...

    pub = public_route("r")(h)
    with pytest.raises(ValueError):
        user_scoped_route("t.py::test_a")(pub)

    async def g() -> None: ...

    us = user_scoped_route("t.py::test_a")(g)
    with pytest.raises(ValueError):
        public_route("r")(us)


async def test_decorated_async_handler_with_depends_still_works() -> None:
    async def dep() -> str:
        return "dep-value"

    app = FastAPI()

    @app.get("/d")
    @public_route("test")
    async def handler(v: str = Depends(dep)) -> dict[str, str]:
        return {"v": v}

    assert handler.__name__ == "handler"
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/d")
    assert r.json() == {"v": "dep-value"}


def test_user_scoped_declarations_point_to_existing_tests() -> None:
    for d in collect_route_declarations(real_app()):
        if d.kind == "user_scoped":
            verify_isolation_test_exists(d.reason_or_test, BACKEND_DIR)


def test_verify_isolation_test_exists_detects_problems(tmp_path: Path) -> None:
    (tmp_path / "test_ok.py").write_text("def test_present():\n    pass\n")
    (tmp_path / "test_async.py").write_text("async def test_a():\n    pass\n")
    (tmp_path / "test_nested.py").write_text("class T:\n    def test_inner(self):\n        pass\n")
    (tmp_path / "test_bad.py").write_text("def broken(:\n")
    verify_isolation_test_exists("test_ok.py::test_present", tmp_path)
    verify_isolation_test_exists("test_async.py::test_a", tmp_path)
    with pytest.raises(ValueError, match="not found"):
        verify_isolation_test_exists("test_missing.py::test_present", tmp_path)
    with pytest.raises(ValueError, match="not defined"):
        verify_isolation_test_exists("test_ok.py::test_absent", tmp_path)
    with pytest.raises(ValueError, match="not defined"):
        verify_isolation_test_exists("test_nested.py::test_inner", tmp_path)
    with pytest.raises(ValueError):
        verify_isolation_test_exists("test_bad.py::test_x", tmp_path)
    with pytest.raises(ValueError):
        verify_isolation_test_exists("../escape.py::test_x", tmp_path)
    with pytest.raises(ValueError):
        verify_isolation_test_exists("nonsense", tmp_path)


def test_nested_included_router_and_websocket_paths() -> None:
    inner, outer = APIRouter(), APIRouter()

    @inner.get("/z")
    @public_route("t")
    async def z() -> None: ...

    @outer.websocket("/w")
    async def w(sock: WebSocket) -> None:
        await sock.close()

    outer.include_router(inner, prefix="/in")
    app = FastAPI()
    app.include_router(outer, prefix="/v1")
    decls = {d.path: d.kind for d in collect_route_declarations(app)}
    assert decls == {"/v1/in/z": "public", "/v1/w": "undeclared"}


def test_host_and_unknown_route_objects_are_undeclared_not_crash() -> None:
    from starlette.routing import Host

    app = FastAPI()
    app.router.routes.append(Host("example.com", app=FastAPI()))
    app.router.routes.append(object())  # type: ignore[arg-type]
    decls = collect_route_declarations(app)
    assert len(decls) == 2
    assert all(d.kind == "undeclared" for d in decls)
    assert {d.path for d in decls} == {"<Host>", "<object>"}


def test_endpoint_name_recorded() -> None:
    by_path = {d.path: d for d in collect_route_declarations(real_app())}
    assert by_path["/healthz"].endpoint_name == "trendscend.api.routes.health.healthz"


def test_wraps_copied_marker_is_undeclared() -> None:
    import functools

    @public_route("original")
    async def declared() -> None: ...

    @functools.wraps(declared)
    async def wrapper() -> None:
        await declared()

    app = FastAPI()
    app.add_api_route("/w", wrapper)
    (d,) = collect_route_declarations(app)
    assert d.kind == "undeclared"
    assert d.reason_or_test == "marker inherited via functools.wraps; re-declare on the wrapper"


def test_wrapper_with_own_different_declaration_is_accepted() -> None:
    import functools

    @public_route("original")
    async def declared() -> None: ...

    @public_route("wrapper reason")
    @functools.wraps(declared)
    async def wrapper() -> None:
        await declared()

    app = FastAPI()
    app.add_api_route("/w", wrapper)
    (d,) = collect_route_declarations(app)
    assert d.kind == "public" and d.reason_or_test == "wrapper reason"


def test_trailing_newline_and_bare_extension_rejected() -> None:
    for bad in ("a.py::test_x\n", "a.py\n::test_x", "dir/.py::test_x"):
        with pytest.raises(ValueError):
            user_scoped_route(bad)


def test_path_escape_rejected_even_when_target_exists(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "test_o.py"
    target.write_text("def test_o():\n    pass\n")
    with pytest.raises(ValueError, match="escapes root"):
        verify_isolation_test_exists("../outside/test_o.py::test_o", root)
    with pytest.raises(ValueError, match="escapes root"):
        verify_isolation_test_exists(f"{target}::test_o", root)
