"""| archivelist [index=<a,b>] — list archived indexes, or buckets in the search time range."""

import sys

import _bootstrap  # noqa: F401  (puts vendored splunklib on sys.path; must stay first)

# isort: split

from splunklib.binding import HTTPError
from splunklib.searchcommands import Configuration, GeneratingCommand, Option, dispatch

from archive_restore import actions
from archive_restore import splunk_ctx as ctx
from archive_restore.config import ConfigError


@Configuration(type="reporting", distributed=False)
class ArchiveListCommand(GeneratingCommand):
    index = Option(require=False, doc="Comma-separated index names. Omit for a summary.")

    def generate(self):
        try:
            settings = ctx.load_settings(self.service)
            if not self.index:
                yield from actions.list_summary(settings.archive_root)
                return
            earliest, latest = ctx.time_range(self.metadata.searchinfo)
            yield from actions.list_buckets(
                settings.archive_root,
                ctx.parse_index_list(self.index),
                earliest,
                latest,
                lambda name: ctx.thawed_dir(self.service, name),
            )
        except (ctx.ContextError, ConfigError, OSError, ValueError, HTTPError) as exc:
            self.error_exit(exc, str(exc))


dispatch(ArchiveListCommand, sys.argv, sys.stdin, sys.stdout, __name__)
