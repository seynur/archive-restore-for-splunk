"""Instance context for the CLI: settings and thawed paths from `splunk btool` (no splunkd login).

The CLI runs on the instance as the Splunk OS user; OS access to that account is the access
control. Everything here fails closed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

from . import ops
from .actions import IndexNotFound
from .buckets import is_valid_index
from .config import Settings, parse_settings

BTOOL_TIMEOUT = 120


class LocalContextError(RuntimeError):
    """The instance does not allow the operation."""


class UnknownIndex(LocalContextError, IndexNotFound):
    """The index has no stanza or thawedPath on this instance."""


def btool(conf: str, stanza: str) -> Dict[str, str]:
    """Merged ``key = value`` pairs of one stanza, as splunkd sees them. Empty if absent."""
    rc, out = ops.run([ops.splunk_bin(), "btool", conf, "list", stanza], BTOOL_TIMEOUT)
    if rc != 0:
        raise LocalContextError("splunk btool %s failed (rc=%d): %s" % (conf, rc, out[-300:]))
    values: Dict[str, str] = {}
    inside = False
    for line in out.splitlines():
        if line.startswith("["):
            inside = line.strip() == "[%s]" % stanza
            continue
        if inside and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def load_settings() -> Settings:
    raw = btool("archive_restore", "settings")
    if not raw:
        raise LocalContextError("archive_restore.conf [settings] not found (is the app installed?)")
    return parse_settings(raw)


def thawed_dir(index: str) -> Path:
    """Expanded thawedPath of an index defined on this instance."""
    if not is_valid_index(index):
        raise LocalContextError("invalid index name: %r" % index)
    raw = btool("indexes", index).get("thawedPath", "")
    if not raw:
        raise UnknownIndex("index %s does not exist on this instance" % index)
    path = os.path.expandvars(raw)
    if "$" in path or not os.path.isabs(path):
        raise LocalContextError(
            "cannot resolve thawedPath %r for index %s (run via `splunk cmd`)" % (raw, index)
        )
    return Path(path)


def check_owner(paths: List[Path]) -> None:
    """Refuse unless this process runs as the owner of every thaweddb (the Splunk user).

    Running as root would leave root-owned buckets that splunkd cannot read.
    """
    uid = os.geteuid()
    for p in paths:
        owner = p.stat().st_uid
        if owner != uid:
            raise LocalContextError(
                "run as the user that owns %s (uid %d), not uid %d" % (p, owner, uid)
            )
