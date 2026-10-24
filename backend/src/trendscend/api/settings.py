"""Environment-driven settings. Errors name variables, never their values."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

_TRUTHY = frozenset({"1", "true", "yes"})


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    # SecretStr: repr/str/model_dump never reveal DSN passwords.
    database_url: SecretStr
    redis_url: SecretStr
    trusted_proxies: frozenset[str] = frozenset()
    environment: Literal["dev", "staging", "prod"]
    rate_limit_per_minute: int = Field(default=60, gt=0)
    # Dev-only escape hatch for the startup DB-role assertion. Rejected elsewhere.
    allow_unsafe_db_role: bool = False

    @field_validator("trusted_proxies")
    @classmethod
    def _valid_proxies(cls, v: frozenset[str]) -> frozenset[str]:
        for entry in v:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError:
                # Never echo the entry.
                raise ValueError("trusted_proxies contains an invalid IP/CIDR entry") from None
        return v

    @model_validator(mode="after")
    def _unsafe_role_only_in_dev(self) -> Settings:
        if self.allow_unsafe_db_role and self.environment != "dev":
            raise ValueError("allow_unsafe_db_role is only permitted when environment is dev")
        return self


def raw_flag_set(source: Mapping[str, str]) -> bool:
    return source.get("ALLOW_UNSAFE_DB_ROLE", "").strip().lower() in _TRUTHY


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from ``env`` (default: ``os.environ``).

    Raises RuntimeError naming the offending variable(s); never echoes values.
    """
    source = os.environ if env is None else env
    for name in ("DATABASE_URL", "REDIS_URL", "ENVIRONMENT"):
        if not source.get(name):
            raise RuntimeError(f"Missing required environment variable: {name}")
    proxies = frozenset(
        p.strip() for p in source.get("TRUSTED_PROXIES", "").split(",") if p.strip()
    )
    if raw_flag_set(source) and source["ENVIRONMENT"] != "dev":
        raise RuntimeError("ALLOW_UNSAFE_DB_ROLE is only permitted when ENVIRONMENT is dev")
    raw: dict[str, object] = {
        "database_url": source["DATABASE_URL"],
        "redis_url": source["REDIS_URL"],
        "environment": source["ENVIRONMENT"],
        "trusted_proxies": proxies,
        "allow_unsafe_db_role": raw_flag_set(source),
    }
    if "RATE_LIMIT_PER_MINUTE" in source:
        raw["rate_limit_per_minute"] = source["RATE_LIMIT_PER_MINUTE"]
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        # Field names only: pydantic's message would include the offending input.
        fields = sorted({".".join(str(p) for p in e["loc"]) or "settings" for e in exc.errors()})
        raise RuntimeError(f"Invalid settings: {', '.join(fields)}") from None
