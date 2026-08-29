"""Temporal worker entrypoint: one process, all task queues (v4 §7)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from temporalio.worker import Worker

from countersign.activities import ALL_ACTIVITIES
from countersign.runtime import TASK_QUEUE, connect_client
from countersign.workflows.job_case import JobIntelligenceWorkflow


async def main() -> None:
    client = await connect_client()
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
    asyncio.run(main())


if __name__ == "__main__":
    run()
