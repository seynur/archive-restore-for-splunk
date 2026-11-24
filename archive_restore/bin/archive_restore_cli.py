"""Shell front end to the same list / restore / delete flows as the search commands.

Run on the instance as the Splunk OS user:

    $SPLUNK_HOME/bin/splunk cmd python3 \\
        $SPLUNK_HOME/etc/apps/archive_restore/bin/archive_restore_cli.py <command> ...

restore and delete are dry runs unless --yes is given.
Exit codes: 0 ok, 1 some buckets failed, 2 rejected (usage/config/preflight/busy), 3 dry run.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence

from archive_restore import actions, ops
from archive_restore import local_ctx as lc
from archive_restore.config import ConfigError
from archive_restore.log import kv_fields, setup_logger
from archive_restore.splunk_ctx import ContextError, parse_index_list

EXIT_OK, EXIT_FAILED, EXIT_REJECTED, EXIT_DRY_RUN = 0, 1, 2, 3
TIME_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S")
TIME_FIELDS = ("earliest", "latest")
REJECTED = (
    lc.LocalContextError,
    ContextError,
    ConfigError,
    ops.PreflightError,
    OSError,
    ValueError,
)


def parse_time(value: str) -> int:
    """Epoch seconds, or YYYY-MM-DD[THH:MM[:SS]] in the server's local time."""
    try:
        return int(float(value))
    except ValueError:
        pass
    for fmt in TIME_FORMATS:
        try:
            return int(time.mktime(datetime.strptime(value, fmt).timetuple()))
        except ValueError:
            continue
    raise argparse.ArgumentTypeError("expected epoch seconds or YYYY-MM-DD[THH:MM[:SS]]")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="archive_restore_cli.py",
        description="List, restore and delete archived Splunk buckets (Archive Restore app).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser, index_required: bool) -> None:
        sp.add_argument("--index", required=index_required, help="comma-separated index names")
        sp.add_argument("--earliest", type=parse_time, default=0, help="default: 0")
        sp.add_argument("--latest", type=parse_time, default=None, help="default: now")
        sp.add_argument("--json", action="store_true", help="one JSON object per line")

    common(sub.add_parser("list", help="summary per index, or buckets with --index"), False)
    restore = sub.add_parser("restore", help="restore buckets in the time range into thaweddb")
    common(restore, True)
    restore.add_argument("--yes", action="store_true", help="really restore (default: dry run)")
    delete = sub.add_parser("delete", help="remove thawed buckets of one index")
    common(delete, True)
    delete.add_argument("--bucket", required=True, help="bucket name, or * for all in range")
    delete.add_argument("--yes", action="store_true", help="really delete (default: dry run)")
    return p


def emit(rows: Iterable[Dict[str, object]], as_json: bool, out=None) -> List[Dict[str, object]]:
    """Print rows (streaming for JSON, as a table otherwise) and return them."""
    out = out or sys.stdout
    seen: List[Dict[str, object]] = []
    for row in rows:
        seen.append(row)
        if as_json:
            out.write(json.dumps(row) + "\n")
            out.flush()
    if not as_json and seen:
        cols = list(seen[0])
        cells = [[_cell(k, r.get(k)) for k in cols] for r in seen]
        widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
        out.write("  ".join(c.ljust(w) for c, w in zip(cols, widths)).rstrip() + "\n")
        for row in cells:
            out.write("  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip() + "\n")
    return seen


def _cell(key: str, value: object) -> str:
    if value is None:
        return ""
    if key in TIME_FIELDS and isinstance(value, int):
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    latest = args.latest if args.latest is not None else int(time.time())
    user = "cli:%s" % getpass.getuser()
    logger = setup_logger("cli") if args.command != "list" else None
    try:
        if args.command == "list":
            settings = lc.load_settings()
            if not args.index:
                emit(actions.list_summary(settings.archive_root), args.json)
            else:
                indexes = parse_index_list(args.index)
                rows = actions.list_buckets(
                    settings.archive_root, indexes, args.earliest, latest, lc.thawed_dir
                )
                emit(rows, args.json)
            return EXIT_OK
        if args.command == "restore":
            settings = lc.load_settings()
            indexes = parse_index_list(args.index)
            plans = actions.plan_restore(settings, indexes, args.earliest, latest, lc.thawed_dir)
            lc.check_owner([p.thawed_dir for p in plans])
            rows = emit(
                actions.run_restore(plans, settings, logger, user, dry_run=not args.yes), args.json
            )
        else:
            indexes = parse_index_list(args.index)
            if len(indexes) != 1:
                raise ContextError("delete takes exactly one index")
            index = indexes[0]
            thawed = lc.thawed_dir(index)
            lc.check_owner([thawed])
            targets = actions.delete_targets(thawed, args.bucket, args.earliest, latest)
            if args.yes:
                rows = emit(actions.run_delete(index, thawed, targets, logger, user), args.json)
            else:
                rows = emit(
                    (ops.result_row("delete", index, n, "would_delete") for n in targets),
                    args.json,
                )
    except REJECTED as exc:
        if logger is not None:
            logger.error(
                kv_fields(
                    user=user,
                    action=args.command,
                    index=args.index,
                    status="rejected",
                    reason=str(exc),
                )
            )
        sys.stderr.write("error: %s\n" % exc)
        return EXIT_REJECTED
    if not rows:
        sys.stderr.write("nothing matched\n")
    if any(r["status"] == "failed" for r in rows):
        return EXIT_FAILED
    return EXIT_OK if args.yes else EXIT_DRY_RUN


if __name__ == "__main__":
    sys.exit(main())
