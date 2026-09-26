"""Replay a stored observation through the page reader and show the model's step.

    python -m countersign.diagnostics.replay_reading <snapshot_id | case_id> [--navigate]

The stored body is read from the database, never refetched, so the model
sees exactly the dossier a live run saw. Every step's action and stated
reason, the verified reading, rejected claims and the domain classification
are printed as JSON. Without `--navigate`, FOLLOW and RENDER are reported
unavailable, so a replay makes no network request; with it, the real fetch
and render capabilities run under their usual policy. One replay is one or
more paid provider calls under the configured provider settings.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any

from countersign.agents.page_reader import ReaderConfig, read_page
from countersign.capabilities.http_fetch import FetchPolicyError, FetchResult, fetch_url
from countersign.capabilities.normalize import classify_authority
from countersign.capabilities.render import render_url
from countersign.domain.verification import classify_reading
from countersign.gateway.evaluator import resolve_provider
from countersign.gateway.provider import StructuredProvider
from countersign.persistence import repo
from countersign.persistence.blobstore import DatabaseBlobStore
from countersign.persistence.db import session_scope


def _no_fetch(url: str, timeout: float) -> FetchResult:
    raise FetchPolicyError("replay: navigation disabled (pass --navigate)")


def _no_render(url: str, body: bytes, timeout: float) -> FetchResult | None:
    return None


def replay(
    reference: str,
    *,
    provider: StructuredProvider | None = None,
    navigate: bool = False,
    config: ReaderConfig | None = None,
) -> dict[str, Any]:
    """Run the reader over a stored snapshot (by id) or a case's posting body."""

    with session_scope() as session:
        snapshot = repo.get_snapshot(session, reference) or repo.body_snapshot(session, reference)
        if snapshot is None:
            raise SystemExit(f"no snapshot or case {reference!r}")
        body = DatabaseBlobStore(session).get(snapshot.raw_object_key)
        if body is None:
            raise SystemExit(f"blob missing for snapshot {snapshot.snapshot_id}")
    first = FetchResult(
        requested_url=snapshot.canonical_url,
        final_url=snapshot.final_url,
        http_status=snapshot.http_status,
        content_type=snapshot.content_type,
        body=body,
        redirect_chain=list(snapshot.redirect_chain),
    )
    result = read_page(
        snapshot.canonical_url,
        first=first,
        authority_of=classify_authority,
        fetch=(lambda url, t: fetch_url(url, timeout_seconds=t)) if navigate else _no_fetch,
        render=render_url if navigate else _no_render,
        provider=provider or resolve_provider(),
        config=config,
    )
    chosen, status, reason = classify_reading(
        [o.dossier() for o in result.observations],
        result.apply_entry,
        result.evidence,
        now=datetime.now(UTC),
    )
    return {
        "snapshot_id": snapshot.snapshot_id,
        "url": snapshot.canonical_url,
        "observations": [
            {
                "index": o.index,
                "how": o.how.value,
                "source_url": o.source_url,
                "http_status": o.http_status,
                "authority": o.authority.value,
                "text_source": o.text_source,
                "text_chars": len(o.text),
                "controls": len(o.controls),
                "controls_truncated": o.controls_truncated,
                "jsonld": o.jsonld.model_dump(mode="json") if o.jsonld else None,
            }
            for o in result.observations
        ],
        "steps": [s.model_dump(mode="json") for s in result.steps],
        "apply_entry": result.apply_entry.model_dump(mode="json") if result.apply_entry else None,
        "evidence": [e.model_dump(mode="json") for e in result.evidence],
        "rejected_claims": [r.model_dump(mode="json") for r in result.rejected_claims],
        "notes": result.notes,
        "stop_reason": result.stop_reason.value,
        "prompt_version": result.prompt_version,
        "usage": result.usage.model_dump(mode="json") if result.usage else None,
        "classification": {"observation": chosen, "status": status.value, "reason": reason},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay a stored observation through the page reader and show the step."
    )
    parser.add_argument("reference", help="snapshot id, or case id for its posting body")
    parser.add_argument("--navigate", action="store_true", help="allow real FOLLOW/RENDER")
    args = parser.parse_args(argv)
    print(json.dumps(replay(args.reference, navigate=args.navigate), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
