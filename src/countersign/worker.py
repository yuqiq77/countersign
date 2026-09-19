"""Temporal worker entrypoint: one process, all task queues (v4 §7)."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.worker import Worker

from countersign.activities import ALL_ACTIVITIES
from countersign.runtime import TASK_QUEUE, connect_client
from countersign.workflows.job_case import JobIntelligenceWorkflow

_log = logging.getLogger(__name__)


async def main() -> None:
    client = await connect_client()
    _log.info("worker up: task_queue=%s", TASK_QUEUE)
    with ThreadPoolExecutor(max_workers=8) as executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[JobIntelligenceWorkflow],
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
    asyncio.run(main())


if __name__ == "__main__":
    run()
