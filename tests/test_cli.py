import json

from click.testing import CliRunner

from conftest import FIXTURES, ROOT
from paved.cli import main


def run(*args):
    return CliRunner().invoke(main, list(args))


def test_check_passes_on_example_and_fails_on_bad_fixture():
    ok = run("check", str(ROOT / "examples" / "orders-consumer" / "deploy"))
    assert ok.exit_code == 0, ok.output
    assert ok.output.strip().endswith("0 waived")
    bad = run("check", str(FIXTURES / "bad-deployment.yaml"))
    assert bad.exit_code == 1
    assert "PR005 no-privileged-or-host-namespaces" in bad.output


def test_check_json_and_rule_filter():
    res = run("check", str(FIXTURES / "bad-deployment.yaml"), "--format", "json", "--rule", "PR009")
    doc = json.loads(res.output)
    assert {f["rule_id"] for f in doc["findings"]} <= {"PR009", "PR000"}


def test_check_sarif_to_file(tmp_path):
    out = tmp_path / "paved.sarif"
    res = run("check", str(FIXTURES / "bad-deployment.yaml"), "--format", "sarif", "-o", str(out))
    assert res.exit_code == 1
    assert json.loads(out.read_text())["version"] == "2.1.0"


def test_new_dry_run_lists_files():
    res = run("new", "refunds", "--team", "payments", "--topic", "refunds", "--dry-run")
    assert res.exit_code == 0
    assert ".github/workflows/ci.yml" in res.output.splitlines()


def test_new_rejects_bad_name():
    res = run("new", "Refunds_Svc", "--team", "payments", "--topic", "refunds", "--dry-run")
    assert res.exit_code != 0 and "DNS label" in res.output


def test_runbook_triage_and_search():
    res = run("runbook", "triage", "NodeNotReady", "--label", "node=ip-10-0-1-23.ec2.internal")
    assert res.exit_code == 0
    assert "kubectl describe node ip-10-0-1-23.ec2.internal" in res.output
    assert "kafka" in run("runbook", "search", "consumer", "lag").output
    assert run("runbook", "triage", "NoSuchAlert").exit_code != 0


def test_rightsize_table():
    res = run("rightsize", "--usage", str(ROOT / "examples" / "usage.json"))
    assert res.exit_code == 0, res.output
    assert "payments/orders-consumer:app" in res.output
    assert run("rightsize").exit_code != 0


def test_policy_list():
    res = run("policy", "list")
    assert "PR001" in res.output and "PR017" in res.output
