"""Runtime configuration for the local FLOTILLA control plane."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path


def _integer(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _number(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return value


def is_loopback(host: str) -> bool:
    """Return whether a host is an explicit loopback address/name."""

    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class ServiceConfig:
    """Validated service settings, sourced from CLI arguments and environment."""

    host: str = "127.0.0.1"
    port: int = 8765
    ledger_path: Path = Path("data/flotilla.sqlite")
    reports_dir: Path = Path("reports")
    max_body_bytes: int = 65_536
    max_budget: float = 1_000_000.0
    api_token: str | None = None
    allowed_origin: str | None = None

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        token = os.environ.get("FLOTILLA_API_TOKEN") or None
        allowed_origin = os.environ.get("FLOTILLA_ALLOWED_ORIGIN") or None
        config = cls(
            host=os.environ.get("FLOTILLA_HOST", "127.0.0.1"),
            port=_integer("FLOTILLA_PORT", 8765, minimum=1, maximum=65_535),
            ledger_path=Path(
                os.environ.get("FLOTILLA_LEDGER", "data/flotilla.sqlite")
            ),
            reports_dir=Path(
                os.environ.get("FLOTILLA_REPORTS_DIR", "reports")
            ),
            max_body_bytes=_integer(
                "FLOTILLA_MAX_BODY_BYTES",
                65_536,
                minimum=1_024,
                maximum=10_485_760,
            ),
            max_budget=_number(
                "FLOTILLA_MAX_BUDGET",
                1_000_000.0,
                minimum=1.0,
                maximum=1_000_000_000.0,
            ),
            api_token=token,
            allowed_origin=allowed_origin,
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.host:
            raise ValueError("service host cannot be empty")
        if not 0 <= self.port <= 65_535:
            raise ValueError("service port must be between 0 and 65535")
        if not 1_024 <= self.max_body_bytes <= 10_485_760:
            raise ValueError(
                "maximum request body must be between 1024 and 10485760 bytes"
            )
        if not 1 <= self.max_budget <= 1_000_000_000:
            raise ValueError("maximum budget must be between 1 and 1000000000")
        if not is_loopback(self.host) and not self.api_token:
            raise ValueError(
                "FLOTILLA_API_TOKEN is required when binding beyond loopback"
            )
        if self.api_token is not None and len(self.api_token) < 16:
            raise ValueError("FLOTILLA_API_TOKEN must contain at least 16 characters")
        if self.allowed_origin == "*":
            raise ValueError(
                "FLOTILLA_ALLOWED_ORIGIN must be an exact origin, never '*'"
            )
        if self.allowed_origin and not self.allowed_origin.startswith(
            ("http://", "https://")
        ):
            raise ValueError(
                "FLOTILLA_ALLOWED_ORIGIN must be an exact http(s) origin"
            )
