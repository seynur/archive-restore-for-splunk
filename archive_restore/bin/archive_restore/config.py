"""archive_restore.conf [settings] parsing and validation (fails closed)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

TRUE = {"1", "true", "t", "yes", "y", "on"}
FALSE = {"0", "false", "f", "no", "n", "off"}


class ConfigError(ValueError):
    """archive_restore.conf is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    archive_root: Path
    max_buckets_per_run: int = 200
    max_bytes_per_run: int = 500 * 1024**3
    space_factor: float = 5.0
    rebuild_timeout: int = 3600
    integrity_timeout: int = 3600
    check_integrity: bool = True


def _positive_int(raw: Mapping[str, str], key: str, default: int) -> int:
    value = str(raw.get(key) or "").strip()
    if not value:
        return default
    try:
        n = int(value)
    except ValueError:
        raise ConfigError("%s must be an integer, got %r" % (key, value)) from None
    if n <= 0:
        raise ConfigError("%s must be > 0, got %d" % (key, n))
    return n


def _bool(raw: Mapping[str, str], key: str, default: bool) -> bool:
    value = str(raw.get(key) or "").strip().lower()
    if not value:
        return default
    if value in TRUE:
        return True
    if value in FALSE:
        return False
    raise ConfigError("%s must be a boolean, got %r" % (key, value))


def parse_settings(raw: Mapping[str, str]) -> Settings:
    root = str(raw.get("archive_root") or "").strip()
    if not root:
        raise ConfigError("archive_root is not set in archive_restore.conf [settings]")
    archive_root = Path(root)
    if not archive_root.is_absolute():
        raise ConfigError("archive_root must be an absolute path, got %r" % root)
    factor_raw = str(raw.get("space_factor") or "").strip()
    try:
        factor = float(factor_raw) if factor_raw else Settings.space_factor
    except ValueError:
        raise ConfigError("space_factor must be a number, got %r" % factor_raw) from None
    if factor < 1:
        raise ConfigError("space_factor must be >= 1, got %s" % factor)
    return Settings(
        archive_root=archive_root,
        max_buckets_per_run=_positive_int(raw, "max_buckets_per_run", Settings.max_buckets_per_run),
        max_bytes_per_run=_positive_int(raw, "max_bytes_per_run", Settings.max_bytes_per_run),
        space_factor=factor,
        rebuild_timeout=_positive_int(raw, "rebuild_timeout", Settings.rebuild_timeout),
        integrity_timeout=_positive_int(raw, "integrity_timeout", Settings.integrity_timeout),
        check_integrity=_bool(raw, "check_integrity", Settings.check_integrity),
    )
