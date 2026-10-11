"""Shared Temporal runtime constants, client construction and the watch Schedule."""

import os
from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
)
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError, RPCStatusCode

from countersign.domain.ids import case_workflow_id
from countersign.policy.watchlist import WATCHLIST_PATH_VAR, WatchPolicy

TASK_QUEUE = "countersign-main"
WORKFLOW_NAME = "JobIntelligenceWorkflow"
WATCH_WORKFLOW_NAME = "WatchWorkflow"
WATCH_SCHEDULE_ID = "countersign-watch"


def workflow_id_for(case_id: str) -> str:
    """One stable workflow id per case (v4 §20).

    Evidence-loop rounds share it through Continue-As-New; generation
    distinguishes workflow inputs and round-specific artifact identities.
    """

    return case_workflow_id(case_id)


async def connect_client() -> Client:
    """Connect to Temporal with the Pydantic payload converter."""

    address = os.environ.get("COUNTERSIGN_TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.environ.get("COUNTERSIGN_TEMPORAL_NAMESPACE", "default")
    return await Client.connect(
        address, namespace=namespace, data_converter=pydantic_data_converter
    )


def watch_schedule(policy: WatchPolicy) -> Schedule:
    """The Schedule a watchlist describes (ticket 004).

    Cron expressions in the file's time zone; a run still going when the
    next is due makes that one skip; a run missed while the host was down
    fires once within a day of its time.
    """

    watchlist = policy.watchlist
    return Schedule(
        action=ScheduleActionStartWorkflow(
            WATCH_WORKFLOW_NAME, id=WATCH_SCHEDULE_ID, task_queue=TASK_QUEUE
        ),
        spec=ScheduleSpec(
            cron_expressions=list(watchlist.schedule.cron),
            time_zone_name=watchlist.schedule.time_zone,
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(days=1)),
        state=ScheduleState(note=f"watchlist {watchlist.version} ({policy.fingerprint})"),
    )


async def sync_watch_schedule(client: Client, policy: WatchPolicy | None) -> None:
    """Create or update the watch Schedule from the watchlist; pause it when unset."""

    handle = client.get_schedule_handle(WATCH_SCHEDULE_ID)
    if policy is None:
        try:
            await handle.pause(note=f"{WATCHLIST_PATH_VAR} unset")
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
        return
    schedule = watch_schedule(policy)
    try:
        await client.create_schedule(WATCH_SCHEDULE_ID, schedule)
    except ScheduleAlreadyRunningError:
        # The update replaces spec, policy and state, so a paused Schedule resumes.
        await handle.update(lambda _: ScheduleUpdate(schedule=schedule))
