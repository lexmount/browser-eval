"""Task-result evaluators behind one browser-use evaluation contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from harness.trajectory_replay.public_webarena.executor import judge_outputs
from harness.trajectory_replay.public_webarena.suite import BrowserTask


@dataclass(frozen=True)
class TaskEvidence:
    outputs: dict[str, str]
    process_passed: bool
    agent_response: dict[str, Any] | list[Any] | str | None = None
    network_har_path: Path | None = None


@dataclass(frozen=True)
class TaskEvaluation:
    status: str
    score: float
    checks: tuple[dict[str, Any], ...]
    evaluator: str
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "pass" and self.score == 1.0


class TaskEvaluator(Protocol):
    def evaluate(self, task: BrowserTask, evidence: TaskEvidence) -> TaskEvaluation: ...


class ExactOutputsEvaluator:
    def evaluate(self, task: BrowserTask, evidence: TaskEvidence) -> TaskEvaluation:
        checks = tuple(judge_outputs(evidence.outputs, task.assertions))
        passed = bool(checks) and all(check.get("passed") is True for check in checks)
        return TaskEvaluation(
            status="pass" if passed else "fail",
            score=1.0 if passed else 0.0,
            checks=checks,
            evaluator="exact_outputs",
            error="" if passed else _format_exact_failures(checks),
        )


def _format_exact_failures(checks: tuple[dict[str, Any], ...]) -> str:
    return "; ".join(
        f"{check['output']} exact mismatch: expected {check['expected']!r}, got {check['actual']!r}"
        for check in checks
        if not check.get("passed")
    )


class WebArenaVerifiedEvaluator:
    """Thin adapter around WebArena-Verified's stable public Python API."""

    def __init__(self, *, config_path: str | Path | None = None) -> None:
        try:
            from webarena_verified import WebArenaVerified
        except ImportError as exc:  # pragma: no cover - exercised in integration environments
            raise RuntimeError(
                "webarena-verified is required for WebArena result evaluation"
            ) from exc
        self._implementation = WebArenaVerified(
            config=Path(config_path) if config_path is not None else None
        )

    def evaluate(self, task: BrowserTask, evidence: TaskEvidence) -> TaskEvaluation:
        config = task.evaluator or {}
        task_id = int(config["task_id"])
        upstream_task = self._implementation.get_task(task_id)
        if int(upstream_task.revision) != int(config["revision"]):
            raise ValueError(
                f"WebArena task {task_id} revision changed: "
                f"expected {config['revision']}, got {upstream_task.revision}"
            )
        if evidence.agent_response is None:
            return TaskEvaluation(
                status="error",
                score=0.0,
                checks=(),
                evaluator="webarena_verified",
                error="agent_response evidence is missing",
            )
        if evidence.network_har_path is None or not evidence.network_har_path.is_file():
            return TaskEvaluation(
                status="error",
                score=0.0,
                checks=(),
                evaluator="webarena_verified",
                error="network.har evidence is missing",
            )
        try:
            har = json.loads(evidence.network_har_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return TaskEvaluation(
                status="error",
                score=0.0,
                checks=(),
                evaluator="webarena_verified",
                error=f"network.har evidence is invalid: {type(exc).__name__}",
            )
        if not isinstance(har, dict) or not (har.get("log") or {}).get("entries"):
            return TaskEvaluation(
                status="error",
                score=0.0,
                checks=(),
                evaluator="webarena_verified",
                error="network.har evidence contains no entries",
            )
        result = self._implementation.evaluate_task(
            task_id=task_id,
            agent_response=evidence.agent_response,
            network_trace=evidence.network_har_path,
        )
        payload = result.model_dump(mode="json")
        checks = tuple(payload.get("evaluators_results") or [])
        status = str(payload.get("status") or "error")
        return TaskEvaluation(
            status="pass" if status == "success" else "error" if status == "error" else "fail",
            score=float(payload.get("score") or 0.0),
            checks=checks,
            evaluator="webarena_verified",
            error=str(payload.get("error_msg") or ""),
        )


class EvaluatorRegistry:
    def __init__(self, *, webarena_config_path: str | Path | None = None) -> None:
        self._webarena_config_path = webarena_config_path
        self._webarena: WebArenaVerifiedEvaluator | None = None

    def evaluate(self, task: BrowserTask, evidence: TaskEvidence) -> TaskEvaluation:
        kind = str((task.evaluator or {}).get("kind") or "")
        if kind == "exact_outputs":
            return ExactOutputsEvaluator().evaluate(task, evidence)
        if kind == "webarena_verified":
            if self._webarena is None:
                self._webarena = WebArenaVerifiedEvaluator(config_path=self._webarena_config_path)
            return self._webarena.evaluate(task, evidence)
        raise ValueError(f"unsupported evaluator kind: {kind!r}")


def write_agent_response(path: str | Path, response: Any) -> None:
    Path(path).write_text(
        json.dumps(response, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
