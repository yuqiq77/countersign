"""Shared Temporal runtime constants and client construction."""

import os

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

TASK_QUEUE = "countersign-main"
WORKFLOW_NAME = "JobIntelligenceWorkflow"


def workflow_id_for(case_id: str) -> str:
    """One stable workflow id per case (v4 §20).

    Evidence-loop rounds share it through Continue-As-New; generation
    distinguishes workflow inputs and round-specific artifact identities.
    """

    return f"job-case/{case_id}"


async def connect_client() -> Client:
    """Connect to Temporal with the Pydantic payload converter."""

    address = os.environ.get("COUNTERSIGN_TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.environ.get("COUNTERSIGN_TEMPORAL_NAMESPACE", "default")
    return await Client.connect(
        address, namespace=namespace, data_converter=pydantic_data_converter
    )
