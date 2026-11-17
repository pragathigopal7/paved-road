import json

from conftest import FIXTURES
from paved import policy
from paved.manifests import load_paths, load_text, selector_matches


def by_rule(report, ref=None):
    out = {}
    for f in report.findings:
        if ref is None or f.object_ref == ref:
            out.setdefault(f.rule_id, []).append(f)
    return out


def test_scaffolded_service_is_fully_compliant(generated):
    report = policy.check(load_paths([generated]))
    assert report.workloads == 1
    assert report.findings == [], policy.format_text(report)
    assert not report.failed(strict=True)


def test_bad_deployment_trips_security_observability_and_reliability_rules():
    report = policy.check(load_paths([FIXTURES / "bad-deployment.yaml"]))
    rules = by_rule(report, "Deployment/payments/legacy-worker")
    expected = {
        "PR001",
        "PR002",
        "PR003",
        "PR004",
        "PR005",  # security
        "PR007",
        "PR008",
        "PR009",  # reliability
        "PR010",
        "PR011",
        "PR012",
        "PR013",  # observability
        "PR014",
        "PR015",
        "PR016",
        "PR017",  # isolation
    }
    assert expected <= set(rules)
    assert "privileged" in " ".join(f.message for f in rules["PR005"])
    assert "hostNetwork" in " ".join(f.message for f in rules["PR005"])
    assert report.failed()


def test_findings_point_at_the_document_line():
    report = policy.check(load_paths([FIXTURES / "bad-deployment.yaml"]))
    lines = {f.object_ref: f.line for f in report.findings}
    assert lines["Deployment/payments/legacy-worker"] == 2
    assert lines["CronJob/payments/nightly-report"] == 27


def test_cronjob_pod_template_is_found_and_probes_not_required():
    report = policy.check(load_paths([FIXTURES / "bad-deployment.yaml"]))
    rules = by_rule(report, "CronJob/payments/nightly-report")
    assert "PR008" not in rules  # probes only for long running workloads
    assert "PR009" not in rules  # pinned by digest
    assert "PR013" not in rules  # labels present on the nested pod template


def test_waiver_without_reason_is_ignored_and_flagged():
    report = policy.check(load_paths([FIXTURES / "bad-deployment.yaml"]))
    rules = by_rule(report, "CronJob/payments/nightly-report")
    assert "PR000" in rules
    assert all(not f.waived for f in rules["PR012"])


def test_waiver_with_reason_suppresses_but_is_reported(generated):
    dep = generated / "deploy/k8s/deployment.yaml"
    text = dep.read_text().replace(
        "  name: orders-consumer\n  labels:",
        "  name: orders-consumer\n  annotations:\n"
        "    paved.dev/waive: PR007\n"
        "    paved.dev/waive-reason: load test, sized by the benchmark\n  labels:",
        1,
    )
    text = text.replace("            limits:\n              memory: 256Mi\n", "")
    dep.write_text(text)
    report = policy.check(load_paths([generated]))
    pr007 = [f for f in report.findings if f.rule_id == "PR007"]
    assert pr007 and all(f.waived for f in pr007)
    assert pr007[0].waive_reason == "load test, sized by the benchmark"
    assert not report.failed()


def test_network_policy_must_select_the_pods(generated):
    sec = generated / "deploy/k8s/security.yaml"
    sec.write_text(
        sec.read_text().replace(
            "  podSelector:\n    matchLabels:\n      app.kubernetes.io/name: orders-consumer",
            "  podSelector:\n    matchLabels:\n      app.kubernetes.io/name: someone-else",
        )
    )
    report = policy.check(load_paths([generated]))
    assert {f.rule_id for f in report.findings} == {"PR014"}


def test_service_monitor_must_reach_the_pods_through_a_service(generated):
    svc = generated / "deploy/k8s/service.yaml"
    svc.write_text(
        svc.read_text().replace(
            "spec:\n  type: ClusterIP\n  selector:\n    app.kubernetes.io/name: orders-consumer",
            "spec:\n  type: ClusterIP\n  selector:\n    app.kubernetes.io/name: not-these-pods",
        )
    )
    report = policy.check(load_paths([generated]))
    assert {f.rule_id for f in report.findings} == {"PR010"}


def test_selector_operators():
    labels = {"app": "a", "tier": "web"}
    assert selector_matches(None, labels)
    assert selector_matches({"matchLabels": {"app": "a"}}, labels)
    assert not selector_matches({"matchLabels": {"app": "b"}}, labels)
    exprs = [
        ({"key": "tier", "operator": "In", "values": ["web", "api"]}, True),
        ({"key": "tier", "operator": "NotIn", "values": ["web"]}, False),
        ({"key": "app", "operator": "Exists"}, True),
        ({"key": "zone", "operator": "DoesNotExist"}, True),
        ({"key": "zone", "operator": "Exists"}, False),
    ]
    for expr, want in exprs:
        assert selector_matches({"matchExpressions": [expr]}, labels) is want, expr


def test_non_kubernetes_yaml_is_skipped():
    objs = load_text("foo: bar\n---\n\n---\napiVersion: v1\nkind: ConfigMap\nmetadata: {name: x}\n")
    assert [o.kind for o in objs] == ["ConfigMap"]


def test_sarif_output_is_valid_shape(generated):
    report = policy.check(load_paths([FIXTURES / "bad-deployment.yaml"]))
    sarif = json.loads(policy.format_sarif(report, base=FIXTURES.parent.parent))
    run = sarif["runs"][0]
    assert sarif["version"] == "2.1.0"
    rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert {r["ruleId"] for r in run["results"]} <= rule_ids
    loc = run["results"][0]["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "tests/fixtures/bad-deployment.yaml"
    assert loc["region"]["startLine"] >= 1


def test_github_annotations():
    report = policy.check(load_paths([FIXTURES / "bad-deployment.yaml"]))
    out = policy.format_github(report)
    assert "::error file=" in out and ",line=2,title=PR001 run-as-non-root::" in out
    assert out.splitlines()[-1].startswith("FAIL:")
