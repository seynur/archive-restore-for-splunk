[settings]
archive_root = <absolute path>
* Root of the frozen archive, laid out as <archive_root>/<index>/<bucket>/rawdata/journal.*
  (the layout written by org_frozen_app or coldToFrozenDir).
* Required. No default.

max_buckets_per_run = <positive integer>
* Maximum buckets one archiverestore run may restore. Narrow the time range when exceeded.
* Default: 200

max_bytes_per_run = <positive integer>
* Maximum archived bytes one archiverestore run may restore.
* Default: 536870912000 (500 GB)

space_factor = <number >= 1>
* Free space on the thaweddb volume must be at least (archived bytes x space_factor),
  because rebuilt buckets also hold index files (often 3-5x a zstd journal).
* Default: 5

rebuild_timeout = <positive integer>
* Seconds allowed for one "splunk rebuild" before it is killed.
* Default: 3600

integrity_timeout = <positive integer>
* Seconds allowed for one "splunk check-integrity" before it is killed.
* Default: 3600

check_integrity = <boolean>
* Run "splunk check-integrity" on buckets that carry rawdata/l2hash before rebuilding.
* Default: true
