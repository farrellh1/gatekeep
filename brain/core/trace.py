from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from core.schemas import BrainState, NormalizedEvent

logger = logging.getLogger("gatekeep")


def _dump(obj):
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def _salient(node: str, s: BrainState) -> dict:
    """The slice of state a node produced -- the auditable record for that step."""
    if node == "intake":
        return {"intake": _dump(s.intake)}
    if node == "investigator":
        return {"findings": [_dump(f) for f in s.findings]}
    if node == "judge":
        return {"verdict": _dump(s.verdict)}
    if node == "responder":
        return {"actions": [_dump(a) for a in s.actions], "gate": _dump(s.gate)}
    return {}


def _summary(node: str, s: BrainState) -> str:
    """One-line human-readable reasoning, for logs and quick scanning."""
    if node == "intake" and s.intake:
        return f"route={s.intake.route} relevant={s.intake.relevant} :: {s.intake.reason}"
    if node == "investigator":
        fails = [f"{f.check}({f.confidence}/{f.engine})" for f in s.findings if f.result == "fail"]
        return f"{len(s.findings)} findings, fails={fails or 'none'}"
    if node == "judge" and s.verdict:
        return (
            f"label={s.verdict.label} conf={s.verdict.confidence:.2f} "
            f":: {'; '.join(s.verdict.reasons)}"
        )
    if node == "responder":
        gated = s.gate.gated if s.gate else []
        return f"actions={[a.action for a in s.actions]} gated={gated}"
    return ""


class Tracer:
    """Records each agent handoff: node, timing, reasoning, and produced state.

    The pipeline is a state machine, so a run is an ordered sequence of steps;
    capturing it gives an auditable log to debug against.
    """

    def __init__(self, event: NormalizedEvent):
        self.delivery_id = event.delivery_id
        self.kind = event.kind
        self.number = event.number
        self.steps: list[dict] = []

    def record(self, node: str, state: BrainState, elapsed_ms: float) -> None:
        summary = _summary(node, state)
        self.steps.append(
            {
                "node": node,
                "elapsed_ms": round(elapsed_ms, 1),
                "summary": summary,
                "output": _salient(node, state),
            }
        )
        logger.info("[%s] %s (%.0fms): %s", self.delivery_id, node, elapsed_ms, summary)

    def record_error(self, node: str, exc: Exception, elapsed_ms: float) -> None:
        """A node raised: record which one and why, so a failed run is still
        readable in the trace."""
        detail = f"{type(exc).__name__}: {exc}"
        self.steps.append(
            {
                "node": node,
                "elapsed_ms": round(elapsed_ms, 1),
                "summary": f"ERROR: {detail}",
                "error": detail,
                "output": {},
            }
        )
        logger.error("[%s] %s (%.0fms) FAILED: %s", self.delivery_id, node, elapsed_ms, detail)

    def to_dict(self) -> dict:
        return {
            "delivery_id": self.delivery_id,
            "kind": self.kind,
            "number": self.number,
            "steps": self.steps,
        }

    def write(self, directory: str) -> Path:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{self.delivery_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path


def configure_logging(level: str | None = None) -> None:
    """Opt-in, entrypoint-level. Libraries should not configure handlers themselves."""
    logging.basicConfig(
        level=level or os.environ.get("GATEKEEP_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
