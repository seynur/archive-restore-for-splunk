# archive-restore-for-splunk

Splunk app `archive_restore`: restores frozen buckets from a mounted archive into an index's
thaweddb. It has dashboards, custom search commands and a CLI, all on one shared library.
It is published on Splunkbase as "Archive Restore for Splunk".

## Layout

- `archive_restore/` is the app, and exactly what ships. Its logic is in
  `bin/archive_restore/`:
  - `buckets`, `config`, `ops` are pure
  - `actions` holds the flows
  - `splunk_ctx` talks to splunkd over REST
  - `local_ctx` reads config with btool
- The rest of the repo is dev-only and never ships: `tests/`, `docs/specs/`, `docs/adr/`,
  `Makefile`, `ruff.toml`.

## Hard rules

- Processes are spawned only through `ops.run`: argv only, never a shell, with a timeout and
  an output cap.
- Fail closed. Validate index names, bucket names and paths (`buckets.safe_child`), and never
  follow symlinks.
- Never modify the archive. Write only inside an index's thaweddb.
- No Splunk restart and no index-refresh REST calls (ADR 0001).
- The UI and CLI share `actions`, the run lock and `archive_restore.log`. New behaviour goes
  into `actions` or `ops`, not into a front end.
- Python must stay compatible with Splunk's bundled Python 3 (ruff target py37). splunklib is
  vendored and pinned (`make vendor`).
- Every behaviour lands with tests. Security-relevant paths assert fail-closed.
- Agents don't commit, push or open PRs. Leave the working tree for review.

## Commands

```bash
make check       # ruff + pytest
make appinspect  # package + splunk-appinspect --mode precert (0 failures required)
```

## Docs

- SPECs are in `docs/specs/` and ADRs in `docs/adr/`. Working notes go in `notes/`, which is
  gitignored.
- Workflow: seynur-skills `standards/ai-assisted-workflows.md`. Use the `splunk-review` and
  `splunk-security` skills for changes to this repo.
