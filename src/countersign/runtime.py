"""Shared Temporal runtime constants and client construction."""

import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

TASK_QUEUE = "countersign-main"
WORKFLOW_NAME = "JobIntelligenceWorkflow"


def workflow_id_for(case_id: str, generation: int = 1) -> str:
    """Stable business workflow ID (v4 §20)."""

    return f"job-case/{case_id}/{generation}"


async def connect_client() -> Client:
    """Connect to Temporal with the Pydantic payload converter."""

    address = os.environ.get("COUNTERSIGN_TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.environ.get("COUNTERSIGN_TEMPORAL_NAMESPACE", "default")
    return await Client.connect(
        address, namespace=namespace, data_converter=pydantic_data_converter
    )
