import ast
import importlib.util
import subprocess
import sys

import pytest

from conftest import ROOT
from paved import scaffold


def spec(**kw):
    base = {"name": "orders-consumer", "team": "payments", "topic": "orders"}
    return scaffold.ServiceSpec(**{**base, **kw})


def test_render_resolves_every_placeholder():
    files = scaffold.render(spec())
    assert ".github/workflows/ci.yml" in files
    assert "deploy/k8s/deployment.yaml" in files
    for path, content in files.items():
        assert "@@" not in content, path


def test_python_files_are_valid_syntax():
    for path, content in scaffold.render(spec()).items():
        if path.endswith(".py"):
            ast.parse(content, filename=path)


def test_values_flow_into_manifests():
    files = scaffold.render(spec(namespace="pay-prod", environment="prod", max_replicas=12))
    assert "namespace: pay-prod" in files["deploy/k8s/kustomization.yaml"]
    assert "deployment.environment=prod" in files["deploy/k8s/deployment.yaml"]
    assert "maxReplicaCount: 12" in files["deploy/k8s/autoscaling.yaml"]
    assert "value: orders.dlq" in files["deploy/k8s/deployment.yaml"]
    assert "service-ci.yml@main" in files[".github/workflows/ci.yml"]


@pytest.mark.parametrize(
    "kw",
    [
        {"name": "Orders"},
        {"name": "orders_consumer"},
        {"name": "x" * 48},
        {"team": "Payments Team"},
        {"topic": "bad topic"},
        {"max_replicas": 0},
    ],
)
def test_invalid_specs_are_rejected(kw):
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.render(spec(**kw))


def test_write_refuses_to_clobber(tmp_path):
    files = scaffold.render(spec())
    scaffold.write(files, tmp_path / "svc")
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.write(files, tmp_path / "svc")
    scaffold.write(files, tmp_path / "svc", force=True)


def test_write_refuses_path_traversal(tmp_path):
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.write({"../escape.txt": "x"}, tmp_path / "svc")


def test_committed_example_matches_the_template():
    """examples/orders-consumer is generated output; regenerate it if this fails."""
    example = ROOT / "examples" / "orders-consumer"
    files = scaffold.render(spec())
    caches = {"__pycache__", ".pytest_cache", ".ruff_cache"}
    on_disk = {
        p.relative_to(example).as_posix(): p.read_text()
        for p in example.rglob("*")
        if p.is_file() and not caches.intersection(p.parts)
    }
    assert on_disk == files


needs_runtime = pytest.mark.skipif(
    any(
        importlib.util.find_spec(m) is None
        for m in ("confluent_kafka", "prometheus_client", "opentelemetry.sdk")
    ),
    reason="generated service dependencies not installed",
)


@needs_runtime
def test_generated_service_test_suite_passes(generated):
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(generated / "tests")],
        cwd=generated,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@needs_runtime
def test_generated_service_serves_health_and_metrics(generated, monkeypatch):
    import json
    import logging
    import urllib.error
    import urllib.request

    monkeypatch.syspath_prepend(str(generated))
    for mod in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        monkeypatch.delitem(sys.modules, mod)
    obs = importlib.import_module("app.observability")

    metrics, health = obs.Metrics(), obs.Health(stall_after_seconds=0.5)
    server = obs.serve_http(0, metrics, health)
    port = server.server_address[1]

    def get(path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, ""

    try:
        assert get("/healthz")[0] == 200
        assert get("/readyz")[0] == 503  # not ready until the group assigns partitions
        health.ready = True
        assert get("/readyz")[0] == 200
        status, body = get("/metrics")
        assert status == 200 and "kafka_consumer_lag" in body
        health._last_beat -= 10  # simulate a stalled poll loop
        assert get("/healthz")[0] == 503
    finally:
        server.shutdown()

    record = logging.LogRecord("svc", logging.INFO, "f", 1, "hello %s", ("world",), None)
    record.offset = 42
    doc = json.loads(obs.JsonFormatter("orders-consumer").format(record))
    assert doc["msg"] == "hello world" and doc["offset"] == 42
    assert doc["service"] == "orders-consumer" and doc["level"] == "info"
