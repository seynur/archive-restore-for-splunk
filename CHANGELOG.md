# Changelog

## 0.1.0 (unreleased)

First release.

- Dashboards: **Restore** (preview, then confirm), **Restored data** (thawed buckets and
  delete) and **History**.
- Search commands: `archivelist`, `archiverestore` (with `dry_run`) and `archivedelete`.
- Command line: `bin/archive_restore_cli.py` with `list`, `restore` and `delete`. Restore and
  delete are dry runs unless you pass `--yes`.
- Each bucket goes through copy → integrity check (when `l2hash` exists) → `splunk rebuild` →
  atomic rename into `thaweddb`.
- Safety:
  - caps on bucket count and bytes per run
  - free-space check
  - a single run lock shared by the UI and the CLI
  - timeouts that kill the whole process group
- Path and name checks reject unsafe input, and symlinks are never followed.
- Capability `archive_restore` and role `archive_restore_admin`.
- Audit log `archive_restore.log`, indexed into `_internal`.
- Replaces the standalone `restore_archive_for_splunk` script that used to live in `seynur-tools`.
