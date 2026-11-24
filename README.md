# Archive Restore for Splunk

A Splunk app that restores **frozen (archived) buckets** from a mounted archive into an
index's `thaweddb`, for the indexes and dates you choose. You can drive it from dashboards
and search commands, or from a command line on the instance. Both use the same code, the same
lock and the same audit log.

It reads the layout that `coldToFrozenDir` and Seynur's `org_frozen_app` write:

```text
<archive_root>/<index>/<bucket>/rawdata/journal.<ext>   (+ rawdata/l2hash when present)
```

The design is documented in [SPEC-001](docs/specs/SPEC-001-archive-restore.md),
[ADR 0001](docs/adr/0001-sync-custom-commands.md) (synchronous search commands) and
[ADR 0002](docs/adr/0002-cli-inside-the-app.md) (CLI).

## Scope

- **Splunk Enterprise, one instance.** This can be a standalone server or an all-in-one
  restore box that can read the archive path. Each index you restore must exist on that
  instance under the same name.
- **Not supported:** Splunk Cloud (the app starts processes and writes to index storage),
  indexer clusters, and S3/GCS archives.

## Install

1. Install `archive_restore-<version>.tgz` from Splunkbase or from `make package`, then restart
   Splunk.
2. Create `$SPLUNK_HOME/etc/apps/archive_restore/local/archive_restore.conf`:

   ```ini
   [settings]
   archive_root = /mnt/frozen_archive
   ```

   Every setting and its default is in `README/archive_restore.conf.spec`. That covers the
   caps, timeouts, `space_factor` and `check_integrity`.
3. Give the `archive_restore_admin` role to the restore operators. `admin` already has the
   `archive_restore` capability. The role inherits index access from `user` and adds
   `_internal` for the History view. If `user` cannot search the restored indexes, add them in
   `local/authorize.conf`.
4. Make sure the `splunk` OS user can read `archive_root` and write each index's `thaweddb`.

## Use: dashboards

| View | What it does |
|---|---|
| **Restore** | Pick indexes and a time range, then **Submit** to preview the matching buckets. The preview shows each bucket's size and whether it is already thawed. To restore, tick *Restore the buckets listed below* and **Submit** again. The box clears itself after each run. |
| **Restored data** | Shows the thawed buckets of an index (`dbinspect`). To remove data, enter a bucket name, or `*` for every thawed bucket in the time range (this includes buckets thawed by hand). Then tick *Delete* and **Submit**. |
| **History** | Lists every restore and delete, from the UI or the CLI, by user, plus a table of failures. |

The same commands work in the app's Search view:

```spl
| archivelist                                         ``` summary of each archived index ```
| archivelist index=wineventlog                       ``` buckets in the search time range ```
| archiverestore index=wineventlog dry_run=t          ``` what would be restored ```
| archiverestore index=wineventlog,firewall
| archivedelete index=wineventlog bucket=db_1700000000_1690000000_12
```

## Use: command line

Run the CLI on the instance **as the Splunk OS user**. Splunk access control does not apply
here, so whoever can use that account can restore. Without `--yes`, `restore` and `delete`
only show what they would do.

```bash
cli() { "$SPLUNK_HOME/bin/splunk" cmd python3 \
          "$SPLUNK_HOME/etc/apps/archive_restore/bin/archive_restore_cli.py" "$@"; }

cli list                                             # summary of each archived index
cli list --index wineventlog --earliest 2024-01-01 --latest 2024-02-01
cli restore --index wineventlog,firewall --earliest 2024-01-01 --latest 2024-02-01         # preview
cli restore --index wineventlog,firewall --earliest 2024-01-01 --latest 2024-02-01 --yes
cli delete --index wineventlog --bucket '*' --earliest 2024-01-01 --latest 2024-02-01 --yes
```

**Times** are epoch seconds or `YYYY-MM-DD[THH:MM[:SS]]` in the server's local time. If you
leave `--earliest` out it is 0, and if you leave `--latest` out it is now. A bucket is selected
when its time span overlaps the range.

`--json` prints one JSON object per line.

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | Some buckets failed |
| 2 | Rejected: usage, config, preflight, or another run in progress |
| 3 | Dry run only |

The CLI reads settings and index paths with `splunk btool`. It refuses to run as any user
other than the owner of the index's `thaweddb`, so it cannot leave root-owned buckets behind.

## How a restore works

A restore works through each bucket in turn:

1. Copy the bucket to `thaweddb/.archive_restore_staging/`.
2. Run `splunk check-integrity` if the bucket has an `l2hash`.
3. Run `splunk rebuild`.
4. Atomically rename the bucket into `thaweddb/<bucket>`.

A bucket that fails is cleaned up and does not affect the others. Restoring again skips buckets
that are already thawed.

Splunk picks up a thawed bucket once it is renamed into place, and drops it once it is deleted.
**No restart is needed.** This was verified on Splunk 9.4. If a restored bucket does not show up
on some other version, restart Splunk yourself.

**Safety limits:**
- A run stops before it starts if it exceeds the bucket cap or the byte cap.
- The free space required is size × `space_factor` (default 5).
- Only one restore or delete runs at a time. The UI and the CLI share the same file lock.
- `splunk` runs as argv only, with timeouts. Index names, bucket names and paths are
  validated, and symlinks are never followed.

Every action is written to `$SPLUNK_HOME/var/log/splunk/archive_restore.log` and lands in
`index=_internal`.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
make check        # ruff check + ruff format --check + pytest
make package      # dist/archive_restore-<version>.tgz (the archive_restore/ folder)
make appinspect   # package + splunk-appinspect --mode precert
make vendor       # re-vendor splunklib (pinned splunk-sdk)
```

The repo has two parts. Only the `archive_restore/` folder ships, so the tests and docs stay
out of the package.

`archive_restore/bin/archive_restore/` holds the logic:

| Module | Role |
|---|---|
| `buckets`, `config`, `ops` | Pure logic, tested with temporary archive trees and a stub `splunk` binary. |
| `actions` | The list, restore and delete flows. Both front ends call these. |
| `splunk_ctx` | Talks to splunkd over REST, for the search commands. |
| `local_ctx` | Reads settings and index paths with `splunk btool`, for the CLI. |

The `bin/archive*.py` commands and `bin/archive_restore_cli.py` are thin wrappers.

## Manual end-to-end checklist (Splunk 9.x container)

1. Create the index `restore_test` and ingest events with a known `_time`. Set
   `coldToFrozenDir = <archive_root>/restore_test`, then roll and freeze its buckets.
2. Log in as a user without the role. The app and its commands are not visible.
3. Log in as `archive_restore_admin` and **Submit** a preview only. `archive_restore.log` must
   show no restore. Then confirm and restore. `index=restore_test` should return the events in
   that range.
4. Restore the same range again. Every row should be `skipped`.
5. Start two restores at the same time, one from the UI and one from the CLI. The second should
   be rejected with "in progress".
6. Corrupt a journal. That bucket should be `failed`, the others restored, and no staging
   directory left behind.
7. Delete with `bucket=*`. The data should disappear from search. History should show each
   action with its user (`cli:<os user>` for the CLI).

## License

MIT. See [LICENSE](LICENSE).
