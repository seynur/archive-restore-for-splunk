# ADR 0002: A command-line front end ships inside the app

Status: Accepted · 2026-11-24

## Context

Admins want to restore from a shell as well as from dashboards. That covers scripted bulk
thaws, and the case where splunkd is busy or the UI isn't reachable. Before this app, Seynur
shipped a standalone script (`seynur-tools/restore_archive_for_splunk`). That script never
worked on the restore path, and keeping it alongside the app would mean two codebases doing
the same job.

## Decision

- **The CLI lives in the app**, as `bin/archive_restore_cli.py`. You run it with
  `splunk cmd python3 …`, and it calls the same `actions` module as the search commands. The
  standalone script is retired.
- **The instance context comes from `splunk btool`, not REST.** The CLI reads
  `archive_restore.conf [settings]` and each index's `thawedPath` with `btool`, and expands
  `$SPLUNK_DB` from the environment that `splunk cmd` sets up. This needs no splunkd
  credentials and gives the same merged view of the config that splunkd uses.
- **Access control is the OS account.** The CLI refuses to run as any user other than the owner
  of the target `thaweddb`. That keeps root from creating buckets splunkd cannot read. Actions
  are logged as `user=cli:<os user>` to the same `archive_restore.log`, so the History view
  shows them.
- **Restore and delete are dry runs by default.** Without `--yes`, they print the
  `would_restore` or `would_delete` rows and exit with 3. This mirrors the dashboard's
  preview-then-confirm step.
- **The lock and safety limits are shared.** A UI run and a CLI run can never overlap, and the
  same caps, space check and timeouts apply to both.

## Consequences

- Anyone with shell access as the Splunk user can restore, without the `archive_restore`
  capability. That is no more than they could already do to index storage by hand.
- `btool` output parsing (`key = value` under `[stanza]`) is covered by the tests and checked
  end to end against Splunk 9.4. A change in its format would make the CLI fail closed.
