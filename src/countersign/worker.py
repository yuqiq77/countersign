"""Temporal worker entrypoint: one process, all task queues (v4 §7)."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.worker import Worker

from countersign.activities import ALL_ACTIVITIES
from countersign.capabilities.render import CHROMIUM_PATH_VAR, chromium_binary
from countersign.gateway.evaluator import profile_fingerprint
from countersign.policy.defaults import EXAMPLE_BANDS, salary_bands
from countersign.policy.official_hosts import OFFICIAL_HOSTS_PATH_VAR, load_official_hosts
from countersign.policy.operator_profile import PROFILE_PATH_VAR, load_operator_profile
from countersign.policy.watchlist import WATCHLIST_PATH_VAR, WatchPolicy, load_watchlist
from countersign.runtime import TASK_QUEUE, connect_client, sync_watch_schedule
from countersign.workflows.job_case import JobIntelligenceWorkflow
from countersign.workflows.watch import WatchWorkflow

_log = logging.getLogger(__name__)


async def main(watch: WatchPolicy | None) -> None:
    client = await connect_client()
    await sync_watch_schedule(client, watch)
    _log.info("worker up: task_queue=%s", TASK_QUEUE)
    with ThreadPoolExecutor(max_workers=8) as executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[JobIntelligenceWorkflow, WatchWorkflow],
            activities=list(ALL_ACTIVITIES),
            activity_executor=executor,
        )
        await worker.run()


def run() -> None:
    # Activity log lines carry Temporal's context (workflow id, activity
    # type, attempt) so a crash-recovery run is legible from `docker logs`.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Policy configuration is checked once here: a malformed set of salary
    # bands must stop the worker, not quarantine cases one evaluation at a time.
    try:
        bands = salary_bands()
        profile = load_operator_profile()
        official_hosts = load_official_hosts()
        watch = load_watchlist()
    except ValueError as exc:
        _log.error("refusing to start: %s", exc)
        raise SystemExit(2) from exc
    browser = chromium_binary()
    if browser is None:
        _log.info("page reader: rendering unavailable (%s unset or not a file)", CHROMIUM_PATH_VAR)
    else:
        _log.info("page reader: rendering with %s", browser)
    if profile is None:
        _log.warning(
            "operator profile: not provided (%s unset); candidate-relative judgments "
            "will be conditional",
            PROFILE_PATH_VAR,
        )
    else:
        _log.info(
            "operator profile: loaded, %d chars, fingerprint=%s",
            len(profile),
            profile_fingerprint(profile),
        )
    if official_hosts is None:
        _log.info(
            "official hosts: not configured (%s unset); only known ATS hosts are official",
            OFFICIAL_HOSTS_PATH_VAR,
        )
    else:
        # Count and identity only: the hostnames are operator-private.
        _log.info(
            "official hosts: %d entries, version=%s, fingerprint=%s",
            len(official_hosts.hosts),
            official_hosts.version,
            official_hosts.fingerprint,
        )
    if watch is None:
        _log.info("watch: not configured (%s unset); the Schedule is paused", WATCHLIST_PATH_VAR)
    else:
        # Identity and counts only: the boards are operator-private.
        _log.info(
            "watch: %d boards, version=%s, fingerprint=%s",
            len(watch.watchlist.boards),
            watch.watchlist.version,
            watch.fingerprint,
        )
    if bands is EXAMPLE_BANDS:
        _log.warning(
            "salary bands: operator bands not configured; using the generic %s example, "
            "so postings in other currencies get compensation_signal=UNKNOWN",
            bands.currency,
        )
    else:
        _log.info("salary bands: operator-configured, currency=%s", bands.currency)
    asyncio.run(main(watch))


if __name__ == "__main__":
    run()
