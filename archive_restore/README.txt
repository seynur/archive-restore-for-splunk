Archive Restore for Splunk
==========================

Restores frozen (archived) Splunk buckets from a locally mounted archive into an index's
thaweddb, for the indexes and dates you choose. The data becomes searchable again without a
restart. You can also remove thawed data and review every action in the History view.

Requirements
  * Splunk Enterprise on one instance (standalone or an all-in-one restore box). Splunk Cloud
    and indexer clusters are not supported.
  * The archive mounted on that instance and readable by the splunk OS user, laid out as
    <archive_root>/<index>/<bucket>/rawdata/journal.*  (coldToFrozenDir layout).
  * Each restored index must exist on the instance under the same name.

Setup
  1. Create local/archive_restore.conf:
         [settings]
         archive_root = /mnt/frozen_archive
     The other settings (caps, timeouts, space_factor, check_integrity) are described in
     README/archive_restore.conf.spec.
  2. Give the archive_restore_admin role to the restore operators. admin already has the
     archive_restore capability.

Use
  * Dashboards: Restore (preview, then confirm), Restored data (thawed buckets, delete) and
    History.
  * Search commands: | archivelist, | archiverestore index=<a,b> [dry_run=t],
    | archivedelete index=<a> bucket=<name>|*
  * Command line, run on the instance as the splunk OS user:
        $SPLUNK_HOME/bin/splunk cmd python3 \
          $SPLUNK_HOME/etc/apps/archive_restore/bin/archive_restore_cli.py --help

Safety
  * Each run is capped by bucket count and by bytes, and checks free space before it starts.
  * Only one run at a time.
  * Every bucket is copied, integrity-checked when an l2hash exists, rebuilt and then moved
    into place in a single rename. A failed bucket is cleaned up.
  * The archive itself is never modified.

Support and source: https://github.com/seynur/archive-restore-for-splunk
License: MIT (see LICENSE)
