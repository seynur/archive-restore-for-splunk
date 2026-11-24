# ADR 0001 — Synchronous custom search commands behind Simple XML forms

Status: Accepted · 2026-11-24

## Context

The app must let Splunk users restore archived buckets on one instance. Options considered for
running the restore: (a) custom search commands run synchronously from a dashboard search,
(b) a custom REST endpoint plus custom JS, (c) a request queue (KV store) drained by a modular
input in the background.

## Decision

- **(a) Synchronous generating commands** (`archivelist`, `archiverestore`, `archivedelete`),
  bounded by `max_buckets_per_run`, `max_bytes_per_run` and per-process timeouts, serialized by
  a file lock. Results are the command's output rows; history comes from `_internal`.
- **Classic Simple XML** forms with no custom JS. Restores need an explicit confirmation
  checkbox that unsets itself after each run; restores are idempotent as a second guard.
- **No restart and no index refresh.** Splunk's docs list a restart (or the
  `rebuild-metadata-and-manifests` call) after a thaw. On Splunk 9.4 e2e, thawed buckets were
  searchable after `rebuild` + rename, and deleted ones disappeared, without either. The REST call
  needs `indexes_edit`, which would make the operator role near-admin, so the app does neither.
- **Config in `default/`**, site values in `local/archive_restore.conf`. This intentionally
  differs from `org_frozen_app` (conf in `local/`) because this app is AppInspect-checked and
  `archive_root` is per site.
- Shared logic lives in `bin/archive_restore/` (pure `buckets`, `config`, `ops`; Splunk access
  only in `splunk_ctx`). The KV logger pattern is copied from `org_frozen_app` — Splunk apps
  cannot import each other's code.

## Consequences

- A large restore occupies a search slot until it ends; caps keep it bounded. If restores of
  hundreds of buckets become routine, revisit (c).
- Search-job cancellation can kill the command mid-bucket; the staging directory is left
  behind and cleaned on the next run of that bucket (it never appears under a real bucket name).
- Single instance only. Clusters need per-peer thawing and are out of scope.
