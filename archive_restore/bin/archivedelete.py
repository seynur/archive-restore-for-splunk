"""| archivedelete index=<a> bucket=<name>|* — remove thawed buckets from thaweddb."""

import sys

import _bootstrap  # noqa: F401  (puts vendored splunklib on sys.path; must stay first)

# isort: split

from splunklib.binding import HTTPError
from splunklib.searchcommands import Configuration, GeneratingCommand, Option, dispatch

from archive_restore import actions, ops
from archive_restore import splunk_ctx as ctx
from archive_restore.log import kv_fields, setup_logger


@Configuration(type="reporting", distributed=False)
class ArchiveDeleteCommand(GeneratingCommand):
    index = Option(require=True, doc="One index name.")
    bucket = Option(
        require=True, doc="One bucket name, or * for every thawed bucket in the time range."
    )

    def generate(self):
        logger = setup_logger("delete")
        user = ctx.username(self.metadata.searchinfo)
        try:
            ctx.require_capability(self.service)
            names = ctx.parse_index_list(self.index)
            if len(names) != 1:
                raise ctx.ContextError("archivedelete takes exactly one index")
            index = names[0]
            thawed = ctx.thawed_dir(self.service, index)
            earliest, latest = ctx.time_range(self.metadata.searchinfo)
            targets = actions.delete_targets(thawed, self.bucket, earliest, latest)
            yield from actions.run_delete(index, thawed, targets, logger, user)
        except (ctx.ContextError, ops.PreflightError, OSError, ValueError, HTTPError) as exc:
            logger.error(
                kv_fields(
                    user=user, action="delete", index=self.index, status="rejected", reason=str(exc)
                )
            )
            self.error_exit(exc, str(exc))


dispatch(ArchiveDeleteCommand, sys.argv, sys.stdin, sys.stdout, __name__)
