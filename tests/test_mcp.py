import asyncio
import json
import shutil

import pytest

from conftest import ROOT

mcp = pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

from paved.mcp_server import build_server  # noqa: E402


@pytest.fixture
def workspace(tmp_path):
    shutil.copytree(ROOT / "examples" / "orders-consumer", tmp_path / "orders-consumer")
    shutil.copy(ROOT / "tests" / "fixtures" / "bad-deployment.yaml", tmp_path / "bad.yaml")
    return tmp_path


def call(server, tool, args):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(tool, args)

    return asyncio.run(go())


def payload(result):
    assert not result.is_error, result
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(result.content[0].text)


def test_tools_are_listed_with_annotations(workspace):
    async def go():
        async with Client(build_server(workspace)) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(go()).tools}
    assert set(tools) == {
        "check_manifests",
        "explain_rule",
        "scaffold_service",
        "rightsize_workloads",
        "search_runbooks",
        "triage_alert",
    }
    assert tools["check_manifests"].annotations.read_only_hint is True
    assert tools["scaffold_service"].annotations.read_only_hint is False


def test_check_manifests_reports_relative_paths(workspace):
    server = build_server(workspace)
    good = payload(call(server, "check_manifests", {"path": "orders-consumer"}))
    assert good["passed"] and good["errors"] == 0
    bad = payload(call(server, "check_manifests", {"path": "bad.yaml"}))
    assert not bad["passed"]
    assert bad["findings"][0]["path"] == "bad.yaml"


def test_paths_outside_the_workspace_are_rejected(workspace):
    result = call(
        build_server(workspace / "orders-consumer"), "check_manifests", {"path": "../bad.yaml"}
    )
    assert result.is_error
    assert "outside the workspace" in result.content[0].text


def test_scaffold_is_dry_run_by_default_and_writes_inside_workspace(workspace):
    server = build_server(workspace)
    dry = payload(
        call(
            server, "scaffold_service", {"name": "refunds", "team": "payments", "topic": "refunds"}
        )
    )
    assert dry["dry_run"] and "deploy/k8s/deployment.yaml" in dry["files"]
    assert not (workspace / "refunds").exists()
    wet = payload(
        call(
            server,
            "scaffold_service",
            {"name": "refunds", "team": "payments", "topic": "refunds", "dry_run": False},
        )
    )
    assert wet["directory"] == "refunds"
    assert (workspace / "refunds" / "deploy" / "k8s" / "deployment.yaml").exists()


def test_triage_alert_fills_labels(workspace):
    out = payload(
        call(
            build_server(workspace),
            "triage_alert",
            {
                "alert_name": "PodCrashLooping",
                "labels": {
                    "namespace": "payments",
                    "pod": "orders-consumer-7d9f-abcde",
                    "container": "app",
                },
            },
        )
    )
    assert out["found"] and out["slug"] == "pod-crashloop"
    assert "kubectl -n payments describe pod orders-consumer-7d9f-abcde" in out["runbook"]
    assert out["missing_labels"] == []


def test_unknown_alert_suggests_runbooks(workspace):
    out = payload(call(build_server(workspace), "triage_alert", {"alert_name": "ConsumerLagWeird"}))
    assert out["found"] is False and out["suggestions"]


def test_explain_rule_and_rightsize(workspace):
    server = build_server(workspace)
    rule = payload(call(server, "explain_rule", {"rule_id": "pr007"}))
    assert rule["id"] == "PR007" and "throttling" in rule["rationale"]
    usage = json.loads((ROOT / "examples" / "usage.json").read_text())["workloads"][:1]
    [rec] = payload(call(server, "rightsize_workloads", {"usage": usage}))
    assert rec["recommended"]["cpu_request"] == "230m"
