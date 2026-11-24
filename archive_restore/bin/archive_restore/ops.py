"""Restore / delete operations on a thaweddb. No Splunk SDK imports.

Processes run only through run(): argv list, timeout, output cap, never a shell.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from .buckets import Bucket, has_symlink, parse_bucket_name, safe_child
from .config import Settings
from .log import kv_fields

STAGING_DIR = ".archive_restore_staging"
MAX_OUTPUT = 64 * 1024
TIMEOUT_RC = 124
_INTEGRITY_RE = re.compile(r"succeeded=(\d+),\s*failed=(\d+)")


class PreflightError(RuntimeError):
    """A run-level check failed; nothing was changed."""


class RestoreBusy(PreflightError):
    """Another restore or delete holds the lock."""


class StepError(RuntimeError):
    """One bucket step failed; other buckets continue."""


@dataclass
class IndexPlan:
    index: str
    thawed_dir: Path
    buckets: List[Bucket]


def run(argv: List[str], timeout: int, max_output: int = MAX_OUTPUT) -> Tuple[int, str]:
    """Run argv (no shell). Return (rc, combined output capped to max_output chars).

    The child runs in its own process group. A timeout, or SIGTERM to this process (search
    cancelled), kills the whole group so no `splunk` grandchild outlives the lock.
    A timeout returns rc 124.
    """
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True
    )
    main = threading.current_thread() is threading.main_thread()
    if main:

        def _on_term(signum: int, _frame: object) -> None:
            _kill_group(proc)
            sys.exit(128 + signum)

        previous = signal.signal(signal.SIGTERM, _on_term)
    try:
        out, _ = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, _ = proc.communicate()
        rc = TIMEOUT_RC
    finally:
        if main:
            signal.signal(signal.SIGTERM, previous)
    text = (out or b"").decode("utf-8", "replace")
    if len(text) > max_output:
        text = text[:max_output] + "...[truncated]"
    return rc, text.strip()


def _kill_group(proc: "subprocess.Popen[bytes]") -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def splunk_bin() -> str:
    return os.path.join(os.environ.get("SPLUNK_HOME", "/opt/splunk"), "bin", "splunk")


def lock_path() -> Path:
    return Path(
        os.environ.get("SPLUNK_HOME", "/opt/splunk"), "var", "run", "splunk", "archive_restore.lock"
    )


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Non-blocking exclusive lock; raises RestoreBusy when held elsewhere."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RestoreBusy("another archive restore or delete is in progress") from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def preflight(plans: Iterable[IndexPlan], settings: Settings) -> None:
    """Fail closed on caps, missing thaweddb, or insufficient free space."""
    plans = list(plans)
    pending = [b for p in plans for b in p.buckets if not (p.thawed_dir / b.name).exists()]
    count = len(pending)
    size = sum(b.size for b in pending)
    if count > settings.max_buckets_per_run:
        raise PreflightError(
            "%d buckets to restore, max_buckets_per_run is %d; narrow the time range"
            % (count, settings.max_buckets_per_run)
        )
    if size > settings.max_bytes_per_run:
        raise PreflightError(
            "%d bytes to restore, max_bytes_per_run is %d; narrow the time range"
            % (size, settings.max_bytes_per_run)
        )
    needed: Dict[int, int] = {}
    paths: Dict[int, Path] = {}
    for p in plans:
        if not p.thawed_dir.is_dir():
            raise PreflightError(
                "thaweddb for index %s does not exist: %s" % (p.index, p.thawed_dir)
            )
        dev = os.stat(str(p.thawed_dir)).st_dev
        pending = [b for b in p.buckets if not (p.thawed_dir / b.name).exists()]
        needed[dev] = needed.get(dev, 0) + int(sum(b.size for b in pending) * settings.space_factor)
        paths.setdefault(dev, p.thawed_dir)
    for dev, need in needed.items():
        free = shutil.disk_usage(str(paths[dev])).free
        if need > free:
            raise PreflightError(
                "need ~%d bytes free under %s (size x space_factor), only %d available"
                % (need, paths[dev], free)
            )


def check_integrity(bucket_path: Path, timeout: int) -> None:
    rc, out = run([splunk_bin(), "check-integrity", "-bucketPath", str(bucket_path)], timeout)
    m = _INTEGRITY_RE.search(out)
    if rc != 0 or not m or int(m.group(2)) != 0 or int(m.group(1)) == 0:
        raise StepError("integrity check failed (rc=%d): %s" % (rc, out[-500:]))


def rebuild(bucket_path: Path, index: str, timeout: int) -> None:
    rc, out = run([splunk_bin(), "rebuild", str(bucket_path), index], timeout)
    if rc != 0:
        raise StepError("rebuild failed (rc=%d): %s" % (rc, out[-500:]))


# splunklib fixes the output field list from the first row, so every row has every field.
ROW_FIELDS = (
    "action",
    "index",
    "bucket",
    "status",
    "bytes",
    "earliest",
    "latest",
    "duration_ms",
    "reason",
)


def result_row(
    action: str, index: str, name: str, status: str, **extra: object
) -> Dict[str, object]:
    unknown = set(extra) - set(ROW_FIELDS)
    if unknown:
        raise KeyError("unknown result fields: %s" % ", ".join(sorted(unknown)))
    row: Dict[str, object] = dict.fromkeys(ROW_FIELDS)
    row.update(action=action, index=index, bucket=name, status=status, **extra)
    return row


def restore_buckets(
    plan: IndexPlan,
    settings: Settings,
    logger: logging.Logger,
    user: str,
    dry_run: bool = False,
) -> Iterator[Dict[str, object]]:
    """Copy -> (integrity) -> rebuild -> rename each bucket into thaweddb. Yields a row each."""
    staging_root = plan.thawed_dir / STAGING_DIR
    try:
        for b in plan.buckets:
            start = time.time()
            row = _restore_one(b, plan, staging_root, settings, dry_run)
            row["duration_ms"] = int((time.time() - start) * 1000)
            level = logging.ERROR if row["status"] == "failed" else logging.INFO
            logger.log(level, kv_fields(user=user, **row))
            yield row
    finally:
        if staging_root.is_dir() and not any(staging_root.iterdir()):
            staging_root.rmdir()


def _restore_one(
    b: Bucket, plan: IndexPlan, staging_root: Path, settings: Settings, dry_run: bool
) -> Dict[str, object]:
    common = {"bytes": b.size, "earliest": b.bucket.earliest, "latest": b.bucket.latest}
    # Per-bucket parent: `splunk rebuild` leaves <name>.rbsentinel next to the bucket, and a
    # leftover from a killed run is cleared here. The bucket keeps its own name inside it.
    parent: Optional[Path] = None
    try:
        final = safe_child(plan.thawed_dir, b.name)
        if final.exists():
            return result_row(
                "restore", plan.index, b.name, "skipped", reason="already thawed", **common
            )
        if dry_run:
            return result_row("restore", plan.index, b.name, "would_restore", **common)
        if staging_root.is_symlink():
            raise StepError("staging directory is a symlink: %s" % staging_root)
        staging_root.mkdir(exist_ok=True)
        parent = safe_child(staging_root, b.name)
        if parent.exists():
            shutil.rmtree(str(parent))
        parent.mkdir()
        staging = parent / b.name
        # Copy links as links (never follow them), then refuse if the copy holds any.
        shutil.copytree(str(b.path), str(staging), symlinks=True)
        if has_symlink(staging):
            raise StepError("archive bucket contains a symlink")
        if settings.check_integrity and b.has_l2hash:
            check_integrity(staging, settings.integrity_timeout)
        rebuild(staging, plan.index, settings.rebuild_timeout)
        os.rename(str(staging), str(final))
        return result_row("restore", plan.index, b.name, "restored", **common)
    except (OSError, StepError, ValueError) as exc:
        return result_row("restore", plan.index, b.name, "failed", reason=str(exc), **common)
    finally:
        if parent is not None and parent.exists():
            shutil.rmtree(str(parent), ignore_errors=True)


def delete_thawed(
    index: str,
    thawed_dir: Path,
    names: Iterable[str],
    logger: logging.Logger,
    user: str,
) -> Iterator[Dict[str, object]]:
    """Remove restored buckets from thaweddb only. Yields a row per name."""
    for name in names:
        if parse_bucket_name(name) is None:
            row = result_row("delete", index, name, "failed", reason="invalid bucket name")
        else:
            try:
                target = safe_child(thawed_dir, name)
                if not target.is_dir():
                    row = result_row("delete", index, name, "not_found")
                else:
                    shutil.rmtree(str(target))
                    row = result_row("delete", index, name, "deleted")
            except (OSError, ValueError) as exc:
                row = result_row("delete", index, name, "failed", reason=str(exc))
        level = logging.ERROR if row["status"] == "failed" else logging.INFO
        logger.log(level, kv_fields(user=user, **row))
        yield row
