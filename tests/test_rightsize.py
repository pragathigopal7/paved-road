import json

import pytest

from conftest import ROOT
from paved import rightsize as rs


@pytest.mark.parametrize(
    "raw,cores", [("250m", 0.25), ("2", 2.0), (1, 1.0), ("1500m", 1.5), (None, None)]
)
def test_parse_cpu(raw, cores):
    assert rs.parse_cpu(raw) == cores


@pytest.mark.parametrize(
    "raw,num",
    [("512Mi", 512 * rs.MI), ("1Gi", rs.GI), ("1G", 10**9), ("128974848", 128974848)],
)
def test_parse_memory(raw, num):
    assert rs.parse_memory(raw) == num


def test_parse_memory_rejects_garbage():
    with pytest.raises(ValueError):
        rs.parse_memory("lots")


def usage(**kw):
    base = {
        "namespace": "payments",
        "workload": "orders-consumer",
        "container": "app",
        "replicas": 4,
        "requests": {"cpu": "1", "memory": "2Gi"},
        "limits": {"memory": "2Gi"},
        "usage": {"cpu_p95": 0.18, "cpu_p99": 0.3, "memory_max_bytes": 300 * rs.MI},
    }
    base.update(kw)
    return rs.Usage.from_dict(base)


def test_over_provisioned_workload_gets_smaller_cheaper_requests():
    rec = rs.recommend(usage())
    # 0.18 * 1.25 = 0.225 -> rounded up to 230m
    assert rec.recommended["cpu_request"] == "230m"
    # 300Mi * 1.2 = 360Mi -> next 16Mi step is 368Mi
    assert rec.recommended["memory_request"] == "368Mi"
    assert rec.recommended["memory_limit"] == rec.recommended["memory_request"]
    assert rec.recommended["cpu_limit"] is None
    assert "over-provisioned" in rec.flags
    assert rec.monthly_delta < 0


def test_cost_math():
    pricing = rs.Pricing(vcpu_hour=0.04, gb_hour=0.005)
    # (1 core * 0.04 + 2 GiB * 0.005) * 730 h * 3 replicas
    assert pricing.monthly(1.0, 2 * rs.GI, 3) == pytest.approx(109.5)


def test_flags_oom_risk_and_cpu_throttling():
    rec = rs.recommend(
        usage(
            requests={"cpu": "100m", "memory": "256Mi"},
            limits={"cpu": "200m", "memory": "256Mi"},
            usage={"cpu_p95": 0.15, "cpu_p99": 0.19, "memory_max_bytes": 250 * rs.MI},
        )
    )
    joined = " ".join(rec.flags)
    assert "oom-risk" in joined
    assert "cpu-throttling" in joined
    assert "under-requested" in joined
    assert rec.monthly_delta > 0  # fixing risk costs money, and the table says so


def test_minimums_apply():
    rec = rs.recommend(usage(usage={"cpu_p95": 0.001, "memory_max_bytes": rs.MI}))
    assert rec.recommended["cpu_request"] == "25m"
    assert rec.recommended["memory_request"] == "64Mi"


def test_example_usage_file_and_table():
    recs = [rs.recommend(u) for u in rs.load_usage_file((ROOT / "examples/usage.json").read_text())]
    table = rs.format_table(recs)
    assert table.splitlines()[0].startswith("WORKLOAD")
    assert table.splitlines()[-1].startswith("TOTAL")
    assert len(recs) >= 3


def test_prometheus_source_builds_usage_from_queries():
    seen = []

    def fake_fetch(url):
        seen.append(url)
        q = url.split("query=", 1)[1]

        def vec(v):
            return {
                "status": "success",
                "data": {"result": [{"metric": {"container": "app"}, "value": [0, str(v)]}]},
            }

        if "quantile_over_time%280.95" in q:
            return vec(0.2)
        if "quantile_over_time%280.99" in q:
            return vec(0.4)
        if "working_set" in q:
            return vec(400 * rs.MI)
        if "requests" in q and "cpu" in q:
            return vec(0.5)
        if "requests" in q:
            return vec(rs.GI)
        if "limits" in q and "memory" in q:
            return vec(rs.GI)
        if "limits" in q:
            return {"status": "success", "data": {"result": []}}
        if "replicas" in q:
            return {"status": "success", "data": {"result": [{"metric": {}, "value": [0, "3"]}]}}
        raise AssertionError(q)

    src = rs.PrometheusSource("http://prom:9090/", fetch=fake_fetch)
    [u] = src.usage("payments", "orders-consumer")
    assert (u.cpu_p95, u.cpu_p99, u.replicas) == (0.2, 0.4, 3)
    assert u.memory_request == rs.GI and u.cpu_limit is None
    assert all(url.startswith("http://prom:9090/api/v1/query?") for url in seen)


def test_prometheus_errors_surface():
    src = rs.PrometheusSource("http://prom", fetch=lambda url: {"status": "error", "error": "bad"})
    with pytest.raises(RuntimeError, match="bad"):
        src.query("up")


def test_usage_json_is_valid():
    doc = json.loads((ROOT / "examples/usage.json").read_text())
    assert doc["workloads"]
