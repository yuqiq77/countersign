"""Shared helpers for workflow tests."""

import asyncio
import uuid

from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from countersign.contracts.enums import CaseState
from countersign.contracts.workflow_io import (
    CaseWorkflowInput,
    CaseWorkflowResult,
    CaseWorkflowStatus,
)
from countersign.runtime import workflow_id_for
from countersign.workflows.job_case import JobIntelligenceWorkflow

JOB_URL = "https://boards.greenhouse.io/acme/jobs/1"


async def time_skipping_env() -> WorkflowEnvironment:
    """A time-skipping environment with the production payload converter.

    The default converter deserializes in Python mode, which strict
    contracts reject; production (runtime.connect_client) uses the pydantic
    converter, so tests must too.
    """

    return await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)


async def start_case(
    env: WorkflowEnvironment, queue: str, *, url: str = JOB_URL, generation: int = 1
) -> WorkflowHandle[JobIntelligenceWorkflow, CaseWorkflowResult]:
    case_id = str(uuid.uuid4())
    return await env.client.start_workflow(
        JobIntelligenceWorkflow.run,
        CaseWorkflowInput(case_id=case_id, url=url, generation=generation),
        id=workflow_id_for(case_id),
        task_queue=queue,
    )


async def wait_for_pending_approval(
    handle: WorkflowHandle[JobIntelligenceWorkflow, CaseWorkflowResult],
    *,
    generation: int = 1,
) -> CaseWorkflowStatus:
    """Poll the status query until the given round reaches PENDING_APPROVAL.

    The query follows continue-as-new to the latest run, so after a
    REQUEST_EVIDENCE decision the round number is what distinguishes the
    next approval from the one just decided.
    """

    for _ in range(200):
        status = await handle.query(JobIntelligenceWorkflow.status)
        if status.state == CaseState.PENDING_APPROVAL and status.generation == generation:
            return status
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow never reached PENDING_APPROVAL in round {generation}")
