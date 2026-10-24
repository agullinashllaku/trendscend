"""Route isolation declarations.

Every HTTP/WebSocket route must declare either that it is intentionally public
(``@public_route(reason)``) or user-scoped (``@user_scoped_route(node_id)``),
naming the pytest test that proves cross-user isolation. A CI test fails on any
undeclared route. Place the decorator BELOW the router decorator so the marker
is set on the very function object FastAPI registers.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount

F = TypeVar("F", bound=Callable[..., Any])

PUBLIC_ATTR = "__trendscend_public_reason__"
USER_SCOPED_ATTR = "__trendscend_isolation_test__"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

Kind = Literal["public", "user_scoped", "undeclared"]


@dataclass(frozen=True)
class RouteDeclaration:
    path: str
    methods: frozenset[str]
    kind: Kind
    reason_or_test: str


def _split_node_id(node_id: str) -> tuple[str, str]:
    parts = node_id.split("::")
    if len(parts) != 2:
        raise ValueError(f"isolation test must be 'path/to/file.py::test_func', got {node_id!r}")
    file_part, func = parts
    if not file_part.endswith(".py") or file_part == ".py" or not _IDENT.match(func):
        raise ValueError(f"isolation test must be 'path/to/file.py::test_func', got {node_id!r}")
    return file_part, func


def public_route(reason: str) -> Callable[[F], F]:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("public_route requires a non-empty reason")

    def deco(fn: F) -> F:
        if hasattr(fn, USER_SCOPED_ATTR):
            raise ValueError(f"{fn.__name__} is already declared user_scoped")
        setattr(fn, PUBLIC_ATTR, reason.strip())
        return fn

    return deco


def user_scoped_route(isolation_test: str) -> Callable[[F], F]:
    _split_node_id(isolation_test)

    def deco(fn: F) -> F:
        if hasattr(fn, PUBLIC_ATTR):
            raise ValueError(f"{fn.__name__} is already declared public")
        setattr(fn, USER_SCOPED_ATTR, isolation_test)
        return fn

    return deco


def verify_isolation_test_exists(node_id: str, root: Path) -> None:
    """Raise ValueError unless ``node_id``'s file exists under ``root`` and defines the function."""
    file_part, func = _split_node_id(node_id)
    base = root.resolve()
    path = (base / file_part).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"isolation test path escapes root: {node_id!r}")
    if not path.is_file():
        raise ValueError(f"isolation test file not found: {file_part}")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise ValueError(f"isolation test file does not parse: {file_part}") from exc
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    if func not in names:
        raise ValueError(f"isolation test function not defined at top level: {node_id}")


def _classify(endpoint: Any) -> tuple[Kind, str]:
    public = getattr(endpoint, PUBLIC_ATTR, None)
    scoped = getattr(endpoint, USER_SCOPED_ATTR, None)
    if public is not None and scoped is not None:  # pragma: no cover - decorators forbid this
        return "undeclared", "conflicting markers"
    if public is not None:
        return "public", str(public)
    if scoped is not None:
        return "user_scoped", str(scoped)
    return "undeclared", ""


def _is_builtin_docs(app: FastAPI, route: Any) -> bool:
    """True only for the framework-generated docs/openapi routes of ``app``."""
    if isinstance(route, APIRoute | APIWebSocketRoute | Mount):
        return False
    builtin = {
        app.openapi_url,
        app.docs_url,
        app.redoc_url,
        app.swagger_ui_oauth2_redirect_url,
    }
    endpoint = getattr(route, "endpoint", None)
    return route.path in builtin and getattr(endpoint, "__module__", None) == "fastapi.applications"


def _expand(route: Any) -> list[Any]:
    """Flatten FastAPI's lazily included routers (``include_router``) into concrete routes.

    FastAPI >= 0.16x keeps included routers as ``_IncludedRouter`` objects exposing
    ``effective_candidates()``; older versions flatten eagerly. Unknown shapes are
    returned as-is and end up ``undeclared`` (fail closed).
    """
    candidates = getattr(route, "effective_candidates", None)
    if candidates is None:
        return [route]
    out: list[Any] = []
    for c in candidates():
        out.extend(_expand(c))
    return out


def _concrete(route: Any) -> Any:
    """Resolve an effective-route context to the object carrying the final path."""
    return getattr(route, "starlette_route", None) or route


def _walk(routes: list[Any], prefix: str, app: FastAPI | None, out: list[RouteDeclaration]) -> None:
    for raw in routes:
        for item in _expand(raw):
            route = _concrete(item)
            path = prefix + str(getattr(route, "path", None) or getattr(item, "path", ""))
            if isinstance(route, Mount):
                sub = getattr(route.app, "routes", None)
                if sub is None:
                    out.append(RouteDeclaration(path, frozenset(), "undeclared", ""))
                    continue
                sub_app = route.app if isinstance(route.app, FastAPI) else None
                _walk(list(sub), path, sub_app, out)
                continue
            if app is not None and _is_builtin_docs(app, route):
                continue
            endpoint = getattr(item, "endpoint", None) or getattr(route, "endpoint", None)
            methods = frozenset(getattr(route, "methods", None) or ())
            kind, detail = _classify(endpoint)
            out.append(RouteDeclaration(path, methods, kind, detail))


def collect_route_declarations(app: FastAPI) -> list[RouteDeclaration]:
    out: list[RouteDeclaration] = []
    _walk(list(app.routes), "", app, out)
    return out
