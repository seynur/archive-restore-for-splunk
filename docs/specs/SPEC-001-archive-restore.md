# SPEC-001 — Archive restore app

Status: Accepted · 2026-11-24

## Problem

Restoring frozen Splunk buckets today means running `restore_archive_for_splunk` by hand on the
indexer. That script is broken for the restore path (wrong bucket-name parsing, undefined
variable with `--check_integrity`), runs `shell=True` with user input, has no timeouts, locks or
space checks, and restarts Splunk.

## Scope

A Splunk app for **one Splunk instance** (standalone indexer + search head) that has the frozen
archive **mounted locally**. Operators choose index(es) and a time range in a form, preview the
matching archived buckets, restore them into the **original index's thaweddb**, see what is
thawed, remove thawed buckets, and review the history.

Out of scope (v1): S3/GCS sources, indexer clusters / distributed restores, background queues,
restore into a different index, Dashboard Studio.

## Archive contract

`<archive_root>/<index>/<bucket>/rawdata/journal.*` with optional `rawdata/l2hash` — what
`org_frozen_app` and `coldToFrozenDir` write. Bucket names: `(db|rb)_<latest>_<earliest>_<id>[_<guid>]`.
Copies with the same `(earliest, latest, id, guid)` are one bucket; `db_` wins over `rb_`.
Anything else under an index directory is skipped. Symlinks are never followed.

## Commands

| Command | Behaviour |
|---|---|
| `\| archivelist` | One row per archived index: `index buckets earliest latest bytes`. Ignores the time range. |
| `\| archivelist index=a,b` | One row per archived bucket overlapping the search time range: `index bucket earliest latest bytes has_l2hash state` where state ∈ `archived, thawed, index_missing`. |
| `\| archiverestore index=a,b [dry_run=t]` | For each selected bucket: `restored`, `skipped` (already thawed), `would_restore` (dry run) or `failed` + `reason`. Every row has the same fields: `action index bucket status bytes earliest latest duration_ms reason`. |
| `\| archivedelete index=a bucket=<name>\|*` | `deleted`, `not_found` or `failed` per bucket. `*` = every thawed bucket overlapping the time range, including ones thawed by hand. Only touches thaweddb. |

Time range = the search's time range. A bucket is selected when `latest >= earliest_tr` and
`earliest <= latest_tr`.

## Command line (ADR 0002)

`bin/archive_restore_cli.py list|restore|delete`, run as the Splunk OS user via `splunk cmd`,
gives the same rows and contracts as the commands above (shared `actions` module, lock and log).
`--earliest/--latest` replace the search time range. `restore` and `delete` are dry runs unless
`--yes`. Exit codes: 0 ok, 1 some failed, 2 rejected, 3 dry run. Settings and `thawedPath` come
from `splunk btool`; it refuses to run as a user other than the thaweddb owner.

## Restore contract

1. **Fail closed before any change** when any of these hold: the user lacks the `archive_restore` capability, `archive_restore.conf` is invalid, an index name is invalid or missing on the instance, the thaweddb is missing, the bucket or byte cap is exceeded, free space < size × `space_factor`, or another restore/delete holds the lock.
2. Per bucket (failures isolated): copy into `thaweddb/.archive_restore_staging/<bucket>` →
   `splunk check-integrity` when `l2hash` exists and enabled → `splunk rebuild <path> <index>` →
   atomic rename into `thaweddb/<bucket>`. Staging is `thaweddb/.archive_restore_staging/<bucket>/<bucket>`
   so `rebuild`'s `<bucket>.rbsentinel` lands in a per-bucket directory that is always removed.
3. No restart and no index refresh. On Splunk 9.4 a renamed-in thawed bucket is searchable and a
   deleted one disappears without either (e2e verified). `rebuild-metadata-and-manifests` was
   dropped because it needs `indexes_edit`, which operators should not hold.
4. Idempotent: re-running a restore skips thawed buckets.
5. Every action is logged as KV to `$SPLUNK_HOME/var/log/splunk/archive_restore.log` with `user`
   (`cli:<os user>` for the command line).

## UI safety

- Restore and delete searches carry `depends` on their confirmation token themselves (a hidden
  row does not stop its search) and clear the token on done, error, fail and cancel.
- Delete needs an explicit bucket name or `*`; an empty field never means "all".

## Security

- App, views and commands readable only by `admin` and `archive_restore_admin`.
- Write commands also require the `archive_restore` capability (checked at run time).
- Processes are spawned argv-only with timeouts and an output cap; no shell.
- Index names `^[a-z0-9_][a-z0-9_-]{0,79}$`, bucket names per the regex above, all paths must be
  direct children of their root.

## Done when

- `make check` green (ruff + pytest), `make appinspect` has no failures.
- Manual e2e checklist in the README passes on a Splunk 9.x container.
