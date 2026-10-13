from __future__ import annotations

from abc import ABC, abstractmethod
import os
import signal
import subprocess
import time
from typing import Any

from ..models import EvalCase, ProviderResult


def terminate_owned_process_tree(process):
    """Stop an owned POSIX session, including detached descendant groups.

    If the process tree cannot be observed, leave the worker alive so the
    caller can quarantine unresolved work instead of claiming it stopped.
    """
    if process is None or process.poll() is not None:
        return True
    if os.name != 'posix':
        return False
    try:
        observed = subprocess.run(
            ['ps', '-axo', 'pid=,ppid=,pgid='], capture_output=True,
            text=True, check=True, timeout=.5,
        )
        rows = [tuple(map(int, line.split())) for line in observed.stdout.splitlines()]
        if any(len(row) != 3 for row in rows):
            return False
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    parents = {pid: parent for pid, parent, _ in rows}
    groups = {pid: group for pid, _, group in rows}
    if groups.get(process.pid) != process.pid:
        return process.poll() is not None
    owned = {process.pid}
    while True:
        descendants = {pid for pid, parent in parents.items() if parent in owned}
        expanded = owned | descendants
        if expanded == owned:
            break
        owned = expanded
    owned_groups = {groups[pid] for pid in owned}
    if os.getpgrp() in owned_groups:
        return False
    # Descendants may call setsid (Playwright does this for Chromium). Stop
    # their groups first, then the original worker, preserving its diagnostics.
    for group in sorted(owned_groups, key=lambda value: value == process.pid):
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            return False
    # SIGKILL delivery is asynchronous. Confirm the owned groups are quiescent
    # before releasing the next call; zombies cannot perform physical work.
    deadline = time.monotonic() + .5
    while True:
        try:
            observed = subprocess.run(
                ['ps', '-axo', 'pgid=,stat='], capture_output=True,
                text=True, check=True, timeout=.5,
            )
            survivors = [parts for line in observed.stdout.splitlines()
                         if len(parts := line.split()) == 2
                         and int(parts[0]) in owned_groups
                         and not parts[1].startswith('Z')]
        except (OSError, ValueError, subprocess.SubprocessError):
            return False
        if not survivors:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(.01)


class Provider(ABC):
    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.config = config
        self.type = str(config.get("type", name))

    def check_native_call(self, case: EvalCase) -> None:
        """Allow only shared, answer-free capabilities on fixed native calls."""
        if not self.config.get("uniform_native_parameters"):
            return
        if case.retrieval_hints:
            from ..retrieval_hints import parse_retrieval_hints
            try:
                plan = parse_retrieval_hints(case.track, case.retrieval_hints, identifier=case.id)
            except ValueError as error:
                raise ValueError("fixed native calls prohibit per-case retrieval hints") from error
            if plan is not None:
                raise ValueError("fixed native calls prohibit per-case retrieval hints")
        supported = {
            "web": {"body", "structure"},
            "dynamic": {"body", "structure"},
            "challenge": {"body", "structure"},
            "risk_control": {"body", "structure"},
            "document": {"body", "file"},
            "image": {"body", "resource"},
            "video": {"body", "resource"},
        }
        if case.task_kind not in supported.get(case.track, set()):
            raise ValueError("fixed native calls do not support this declared task route")

    @property
    def min_interval_seconds(self) -> float:
        """Minimum cooldown from one response completion to the next request start."""
        return max(0.0, float(self.config.get("min_interval_seconds", 0)))

    @abstractmethod
    def fetch(self, case: EvalCase, timeout: float) -> ProviderResult:
        raise NotImplementedError

    def validate(self) -> list[str]:
        return []

    def cancel(self) -> None:
        """Best-effort cancellation; non-cancellable calls are quarantined."""

    @property
    def cancellation_cleanup_seconds(self) -> float:
        """Bounded post-deadline grace for a cancellable call to stop.

        This is cleanup time, not answer latency. Adapters must opt in only
        when ``cancel`` actively stops their underlying work.
        """
        return 0.0

    def begin_call(self) -> None:
        """Reset cancellation state before the timed worker can start."""


def build_provider(name: str, config: dict[str, Any]) -> Provider:
    provider_type = config.get("type")
    if provider_type in {"headless_html", "headless_markdown"}:
        from .headless import HeadlessProvider
        return HeadlessProvider(name, config)
    if provider_type == "moli_webfetch":
        from .moli import MoliWebFetchProvider

        return MoliWebFetchProvider(name, config)
    if provider_type == "http":
        from .http import HttpProvider

        return HttpProvider(name, config)
    if provider_type == "cowagent_builtin":
        from .cowagent import CowAgentBuiltinProvider

        return CowAgentBuiltinProvider(name, config)
    if provider_type in {"lexmount", "lexmount_cn"}:
        from .lexmount import LexmountProvider

        return LexmountProvider(name, config)
    if provider_type == "firecrawl":
        from .firecrawl import FirecrawlProvider

        return FirecrawlProvider(name, config)
    if provider_type == "tavily_extract":
        from .tavily import TavilyExtractProvider

        return TavilyExtractProvider(name, config)
    if provider_type == "exa_contents":
        from .exa import ExaContentsProvider

        return ExaContentsProvider(name, config)
    if provider_type == "cloudflare_markdown":
        from .cloudflare import CloudflareMarkdownProvider

        return CloudflareMarkdownProvider(name, config)
    if provider_type == "cloudflare_content":
        from .cloudflare import CloudflareContentProvider

        return CloudflareContentProvider(name, config)
    if provider_type == "subprocess_jsonl":
        from .subprocess_jsonl import SubprocessJsonlProvider

        return SubprocessJsonlProvider(name, config)
    if provider_type == "fixture":
        from .fixture import FixtureProvider

        return FixtureProvider(name, config)
    raise ValueError(f"unknown provider type: {provider_type!r}")
