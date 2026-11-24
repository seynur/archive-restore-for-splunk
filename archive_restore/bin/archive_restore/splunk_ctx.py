"""The only module that talks to splunkd (via splunklib's service from the search command)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, List, Tuple

from .actions import IndexNotFound
from .buckets import is_valid_index
from .config import Settings, parse_settings

CAPABILITY = "archive_restore"
CONF = "archive_restore"


class ContextError(RuntimeError):
    """splunkd state does not allow the operation (fails closed)."""


class UnknownIndex(ContextError, IndexNotFound):
    """The index does not exist here or has no thawedPath."""


def parse_index_list(value: str) -> List[str]:
    names = [n.strip() for n in (value or "").split(",") if n.strip()]
    if not names:
        raise ContextError("index is required")
    bad = [n for n in names if not is_valid_index(n)]
    if bad:
        raise ContextError("invalid index name(s): %s" % ", ".join(bad))
    return sorted(set(names))


def time_range(searchinfo: Any) -> Tuple[int, int]:
    """Search time range as epoch ints; all-time earliest=0, missing latest=now."""
    earliest = int(float(getattr(searchinfo, "earliest_time", 0) or 0))
    latest = int(float(getattr(searchinfo, "latest_time", 0) or 0)) or int(time.time())
    return earliest, latest


def load_settings(service: Any) -> Settings:
    try:
        stanza = service.confs[CONF]["settings"]
    except KeyError:
        raise ContextError("%s.conf [settings] not found" % CONF) from None
    return parse_settings(dict(stanza.content))


def require_capability(service: Any) -> None:
    """Fail closed unless the calling user holds the archive_restore capability."""
    body = service.get("authentication/current-context", output_mode="json").body.read()
    entries = json.loads(body).get("entry") or []
    caps = entries[0]["content"].get("capabilities", []) if entries else []
    if CAPABILITY not in caps:
        raise ContextError("the '%s' capability is required" % CAPABILITY)


def thawed_dir(service: Any, index: str) -> Path:
    """Expanded thawedPath of an index that exists on this instance."""
    try:
        idx = service.indexes[index]
    except KeyError:
        raise UnknownIndex("index %s does not exist on this instance" % index) from None
    path = idx.content.get("thawedPath_expanded") or ""
    if not path:
        raise UnknownIndex("index %s has no thawedPath" % index)
    return Path(path)


def username(searchinfo: Any) -> str:
    return str(getattr(searchinfo, "username", "") or "unknown")
