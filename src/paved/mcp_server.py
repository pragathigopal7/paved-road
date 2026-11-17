"""MCP plugin: the paved road tools, exposed to AI assistants over the Model Context Protocol.

An assistant in an editor or chat can scaffold a compliant service, explain why a
manifest fails policy, size a workload, or pull up the right runbook for a firing alert
with its labels filled in.

Guardrails:
  * Every path is resolved inside a workspace root; anything outside is rejected.
  * Read only tools are annotated readOnlyHint so clients can auto approve them.
  * scaffold_service defaults to a dry run; writing requires dry_run=False and still
    refuses to overwrite a non empty directory.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from paved import policy, rightsize, runbooks, scaffold
from paved.manifests import ManifestError, load_paths

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITES = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)


class WorkspaceError(ToolError):
    """Raised as a ToolError so the client sees the reason; other exceptions are
    logged server side and reported only as a generic failure."""


def _inside(root: Path, user_path: str) -> Path:
    candidate = (root / user_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise WorkspaceError(f"path {user_path!r} is outside the workspace")
    return candidate


def build_server(workspace: Path) -> MCPServer:
    root = workspace.resolve()
    books = runbooks.load()
    server = MCPServer(
        name="paved",
        instructions=(
            "Platform tools for Kubernetes services on the paved road. Use check_manifests "
            "before suggesting manifest changes, and triage_alert when the user pastes a "
            "firing alert. Policy findings include a rule rationale; explain it, do not "
            "just suggest waiving the rule."
        ),
    )

    @server.tool(annotations=READ_ONLY)
    def check_manifests(path: str = ".", strict: bool = False) -> dict[str, Any]:
        """Check Kubernetes YAML under `path` (relative to the workspace) against the
        platform policy. Returns findings with rule IDs, file and line."""
        try:
            report = policy.check(load_paths([_inside(root, path)]))
        except ManifestError as exc:
            raise ToolError(str(exc)) from exc
        result = report.to_dict()
        result["passed"] = not report.failed(strict)
        for f in result["findings"]:
            with contextlib.suppress(ValueError):
                f["path"] = str(Path(f["path"]).resolve().relative_to(root))
        return result

    @server.tool(annotations=READ_ONLY)
    def explain_rule(rule_id: str) -> dict[str, str]:
        """Explain a policy rule (for example PR007) and why the platform enforces it."""
        r = policy.RULES.get(rule_id.upper())
        if r is None:
            raise ToolError(f"unknown rule {rule_id}; known: {', '.join(sorted(policy.RULES))}")
        return {
            "id": r.id,
            "name": r.name,
            "severity": r.severity,
            "summary": r.summary,
            "rationale": r.rationale,
            "waiver": f"annotate the workload with {policy.WAIVE_ANNOTATION} and "
            f"{policy.WAIVE_REASON_ANNOTATION}",
        }

    @server.tool(annotations=WRITES)
    def scaffold_service(
        name: str, team: str, topic: str, out_dir: str | None = None, dry_run: bool = True
    ) -> dict[str, Any]:
        """Generate a Kafka consumer service that passes policy on day one. Dry run by
        default: returns the file list. Set dry_run=false to write into the workspace."""
        try:
            files = scaffold.render(scaffold.ServiceSpec(name=name, team=team, topic=topic))
            if dry_run:
                return {"dry_run": True, "files": sorted(files)}
            target = _inside(root, out_dir or name)
            written = scaffold.write(files, target)
        except scaffold.ScaffoldError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "dry_run": False,
            "directory": str(target.relative_to(root)),
            "files": [str(p.relative_to(root)) for p in written],
        }

    @server.tool(annotations=READ_ONLY)
    def rightsize_workloads(usage: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Recommend CPU and memory requests from observed usage records, with monthly
        cost before and after. Each record: workload, container, replicas, requests,
        limits, usage{cpu_p95, cpu_p99, memory_max_bytes}."""
        try:
            return [rightsize.recommend(rightsize.Usage.from_dict(u)).to_dict() for u in usage]
        except (KeyError, ValueError) as exc:
            raise ToolError(f"invalid usage record: {exc}") from exc

    @server.tool(annotations=READ_ONLY)
    def search_runbooks(query: str, limit: int = 3) -> list[dict[str, Any]]:
        """Find failure triage playbooks by symptom, for example 'consumer lag'."""
        return [{**b.summary(), "score": s} for b, s in runbooks.search(books, query, limit)]

    @server.tool(annotations=READ_ONLY)
    def triage_alert(alert_name: str, labels: dict[str, str] | None = None) -> dict[str, Any]:
        """Return the runbook for a firing Prometheus alert with its labels filled into
        the commands, plus any placeholders still missing."""
        book = runbooks.for_alert(books, alert_name)
        if book is None:
            hits = runbooks.search(books, runbooks.split_camel(alert_name), 3)
            return {"found": False, "suggestions": [b.summary() for b, _ in hits]}
        labels = labels or {}
        return {
            "found": True,
            **book.summary(),
            "missing_labels": sorted(book.placeholders() - set(labels)),
            "runbook": book.render(labels),
        }

    return server
