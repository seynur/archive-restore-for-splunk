"""| archiverestore index=<a,b> [dry_run=t] — restore archived buckets in the search time range."""

import sys

import _bootstrap  # noqa: F401  (puts vendored splunklib on sys.path; must stay first)

# isort: split

from splunklib.binding import HTTPError
from splunklib.searchcommands import Boolean, Configuration, GeneratingCommand, Option, dispatch

from archive_restore import actions, ops
from archive_restore import splunk_ctx as ctx
from archive_restore.config import ConfigError
from archive_restore.log import kv_fields, setup_logger


@Configuration(type="reporting", distributed=False)
class ArchiveRestoreCommand(GeneratingCommand):
    index = Option(require=True, doc="Comma-separated index names to restore.")
    dry_run = Option(require=False, validate=Boolean(), default=False)

    def generate(self):
        logger = setup_logger("restore")
        user = ctx.username(self.metadata.searchinfo)
        try:
            ctx.require_capability(self.service)
            settings = ctx.load_settings(self.service)
            earliest, latest = ctx.time_range(self.metadata.searchinfo)
            plans = actions.plan_restore(
                settings,
                ctx.parse_index_list(self.index),
                earliest,
                latest,
                lambda name: ctx.thawed_dir(self.service, name),
            )
            yield from actions.run_restore(plans, settings, logger, user, self.dry_run)
        except (
            ctx.ContextError,
            ConfigError,
            ops.PreflightError,
            OSError,
            ValueError,
            HTTPError,
        ) as exc:
            logger.error(
                kv_fields(
                    user=user,
                    action="restore",
                    index=self.index,
                    status="rejected",
                    reason=str(exc),
                )
            )
            self.error_exit(exc, str(exc))


dispatch(ArchiveRestoreCommand, sys.argv, sys.stdin, sys.stdout, __name__)
