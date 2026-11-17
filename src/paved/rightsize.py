"""Right size container requests from observed usage and show the cost impact.

Policy:
  * CPU request   = p95 CPU usage plus headroom, rounded up to 10m.
  * Memory request = peak working set plus headroom, rounded up to 16Mi.
  * Memory limit  = memory request, so the pod gets predictable OOM behavior.
  * No CPU limit, to avoid CFS throttling; flag existing limits that throttle.

Usage comes from a JSON file or straight from Prometheus (cAdvisor and
kube-state-metrics series). Unit prices default to AWS Fargate list prices per
vCPU hour and GB hour as a neutral proxy for node cost; pass your own blended rate.
"""

from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

HOURS_PER_MONTH = 730
MI = 1024**2
GI = 1024**3

DEFAULT_VCPU_HOUR = 0.04048
DEFAULT_GB_HOUR = 0.004445

_MEM_UNITS = {
    "": 1,
    "k": 10**3,
    "M": 10**6,
    "G": 10**9,
    "T": 10**12,
    "Ki": 1024,
    "Mi": MI,
    "Gi": GI,
    "Ti": 1024**4,
}


def parse_cpu(value: str | float | int | None) -> float | None:
    """'250m' -> 0.25 cores, '2' -> 2.0."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    v = str(value).strip()
    if v.endswith("m"):
        return float(v[:-1]) / 1000
    return float(v)


def parse_memory(value: str | float | int | None) -> int | None:
    """'512Mi' -> bytes. Accepts plain numbers as bytes."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return int(value)
    m = re.fullmatch(r"\s*([0-9.]+)\s*([KMGT]i?|k)?\s*", str(value))
    if not m:
        raise ValueError(f"cannot parse memory quantity: {value!r}")
    return int(float(m.group(1)) * _MEM_UNITS[m.group(2) or ""])


def format_cpu(cores: float) -> str:
    return f"{round(cores * 1000)}m"


def format_memory(num_bytes: int) -> str:
    if num_bytes % GI == 0:
        return f"{num_bytes // GI}Gi"
    return f"{math.ceil(num_bytes / MI)}Mi"


@dataclass
class Usage:
    namespace: str
    workload: str
    container: str
    replicas: int
    cpu_p95: float
    cpu_p99: float
    memory_max_bytes: int
    cpu_request: float | None = None
    cpu_limit: float | None = None
    memory_request: int | None = None
    memory_limit: int | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Usage:
        requests = d.get("requests") or {}
        limits = d.get("limits") or {}
        usage = d["usage"]
        return cls(
            namespace=d.get("namespace", "default"),
            workload=d["workload"],
            container=d.get("container", "app"),
            replicas=int(d.get("replicas", 1)),
            cpu_p95=float(usage["cpu_p95"]),
            cpu_p99=float(usage.get("cpu_p99", usage["cpu_p95"])),
            memory_max_bytes=int(usage["memory_max_bytes"]),
            cpu_request=parse_cpu(requests.get("cpu")),
            cpu_limit=parse_cpu(limits.get("cpu")),
            memory_request=parse_memory(requests.get("memory")),
            memory_limit=parse_memory(limits.get("memory")),
        )


@dataclass
class Pricing:
    vcpu_hour: float = DEFAULT_VCPU_HOUR
    gb_hour: float = DEFAULT_GB_HOUR

    def monthly(self, cpu: float, memory_bytes: int, replicas: int) -> float:
        hourly = cpu * self.vcpu_hour + (memory_bytes / GI) * self.gb_hour
        return round(hourly * HOURS_PER_MONTH * replicas, 2)


@dataclass
class Recommendation:
    namespace: str
    workload: str
    container: str
    replicas: int
    current: dict[str, str | None]
    recommended: dict[str, str | None]
    monthly_cost_current: float
    monthly_cost_recommended: float
    flags: list[str] = field(default_factory=list)

    @property
    def monthly_delta(self) -> float:
        return round(self.monthly_cost_recommended - self.monthly_cost_current, 2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["monthly_delta"] = self.monthly_delta
        return d


def recommend(
    u: Usage,
    pricing: Pricing | None = None,
    cpu_headroom: float = 0.25,
    memory_headroom: float = 0.20,
    min_cpu: float = 0.025,
    min_memory: int = 64 * MI,
) -> Recommendation:
    pricing = pricing or Pricing()
    cpu = max(min_cpu, math.ceil(u.cpu_p95 * (1 + cpu_headroom) * 100) / 100)
    mem_step = 16 * MI
    memory = max(
        min_memory, math.ceil(u.memory_max_bytes * (1 + memory_headroom) / mem_step) * mem_step
    )

    flags: list[str] = []
    if u.memory_limit and u.memory_max_bytes >= 0.9 * u.memory_limit:
        flags.append("oom-risk: peak memory is within 10% of the limit")
    if u.cpu_limit and u.cpu_p99 >= 0.8 * u.cpu_limit:
        flags.append("cpu-throttling: p99 CPU is above 80% of the CPU limit; remove the limit")
    if u.cpu_request is not None and u.cpu_p95 > u.cpu_request:
        flags.append("under-requested: p95 CPU exceeds the request, pods contend for CPU")
    if (u.cpu_request and u.cpu_request > 2 * cpu) or (
        u.memory_request and u.memory_request > 1.5 * memory
    ):
        flags.append("over-provisioned")
    if u.cpu_request is None or u.memory_request is None:
        flags.append("missing-requests: scheduler is guessing, set requests")

    cur_cpu = u.cpu_request or 0.0
    cur_mem = u.memory_request or 0
    return Recommendation(
        namespace=u.namespace,
        workload=u.workload,
        container=u.container,
        replicas=u.replicas,
        current={
            "cpu_request": format_cpu(cur_cpu) if u.cpu_request is not None else None,
            "cpu_limit": format_cpu(u.cpu_limit) if u.cpu_limit else None,
            "memory_request": format_memory(cur_mem) if u.memory_request is not None else None,
            "memory_limit": format_memory(u.memory_limit) if u.memory_limit else None,
        },
        recommended={
            "cpu_request": format_cpu(cpu),
            "cpu_limit": None,
            "memory_request": format_memory(memory),
            "memory_limit": format_memory(memory),
        },
        monthly_cost_current=pricing.monthly(cur_cpu, cur_mem, u.replicas),
        monthly_cost_recommended=pricing.monthly(cpu, memory, u.replicas),
        flags=flags,
    )


def load_usage_file(text: str) -> list[Usage]:
    doc = json.loads(text)
    items = doc["workloads"] if isinstance(doc, dict) else doc
    return [Usage.from_dict(item) for item in items]


def format_table(recs: list[Recommendation]) -> str:
    names = [f"{r.namespace}/{r.workload}:{r.container}" for r in recs]
    w = max([len("WORKLOAD"), *map(len, names)])
    rows = [
        f"{'WORKLOAD':<{w}}  {'CPU REQ':>12}  {'MEM REQ':>12}  {'$/MONTH':>15}  {'DELTA':>8}  FLAGS"
    ]
    total = 0.0
    for name, r in zip(names, recs, strict=True):
        cpu = f"{r.current['cpu_request'] or '-'}>{r.recommended['cpu_request']}"
        mem = f"{r.current['memory_request'] or '-'}>{r.recommended['memory_request']}"
        cost = f"{r.monthly_cost_current:.2f}>{r.monthly_cost_recommended:.2f}"
        flags = ", ".join(f.split(":")[0] for f in r.flags)
        rows.append(
            f"{name:<{w}}  {cpu:>12}  {mem:>12}  {cost:>15}  {r.monthly_delta:>+8.2f}  {flags}"
        )
        total += r.monthly_delta
    rows.append(f"{'TOTAL':<{w}}  {'':>12}  {'':>12}  {'':>15}  {total:>+8.2f}")
    return "\n".join(row.rstrip() for row in rows)


# Prometheus source ------------------------------------------------------------------

Fetcher = Callable[[str], dict[str, Any]]


def _http_fetch(url: str) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        raise ValueError("Prometheus URL must be http or https")
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (scheme checked above)
        return json.loads(resp.read().decode())


class PrometheusSource:
    """Builds Usage records for one Deployment from cAdvisor and kube-state-metrics."""

    def __init__(self, base_url: str, fetch: Fetcher | None = None):
        self.base_url = base_url.rstrip("/")
        self.fetch = fetch or _http_fetch

    def query(self, promql: str) -> dict[str, float]:
        url = f"{self.base_url}/api/v1/query?" + urllib.parse.urlencode({"query": promql})
        body = self.fetch(url)
        if body.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {body.get('error', body)}")
        out: dict[str, float] = {}
        for series in body["data"]["result"]:
            key = series["metric"].get("container", "")
            out[key] = float(series["value"][1])
        return out

    def usage(self, namespace: str, deployment: str, window: str = "7d") -> list[Usage]:
        pod = f"{deployment}-[a-z0-9]+-[a-z0-9]{{5}}"
        sel = f'namespace="{namespace}",pod=~"{pod}",container!="",container!="POD"'
        rate = f"rate(container_cpu_usage_seconds_total{{{sel}}}[5m])"
        ksm = f'namespace="{namespace}",pod=~"{pod}"'
        cpu95 = self.query(f"max by (container) (quantile_over_time(0.95, {rate}[{window}:5m]))")
        cpu99 = self.query(f"max by (container) (quantile_over_time(0.99, {rate}[{window}:5m]))")
        working_set = f"container_memory_working_set_bytes{{{sel}}}"
        mem = self.query(f"max by (container) (max_over_time({working_set}[{window}]))")
        req = {
            r: self.query(
                f'max by (container) (kube_pod_container_resource_requests{{{ksm},resource="{r}"}})'
            )
            for r in ("cpu", "memory")
        }
        lim = {
            r: self.query(
                f'max by (container) (kube_pod_container_resource_limits{{{ksm},resource="{r}"}})'
            )
            for r in ("cpu", "memory")
        }
        replicas = self.query(
            f'kube_deployment_spec_replicas{{namespace="{namespace}",deployment="{deployment}"}}'
        )
        n_replicas = int(next(iter(replicas.values()), 1))
        results = []
        for container in sorted(cpu95):
            results.append(
                Usage(
                    namespace=namespace,
                    workload=deployment,
                    container=container,
                    replicas=n_replicas,
                    cpu_p95=cpu95[container],
                    cpu_p99=cpu99.get(container, cpu95[container]),
                    memory_max_bytes=int(mem.get(container, 0)),
                    cpu_request=req["cpu"].get(container),
                    cpu_limit=lim["cpu"].get(container),
                    memory_request=_int_or_none(req["memory"].get(container)),
                    memory_limit=_int_or_none(lim["memory"].get(container)),
                )
            )
        return results


def _int_or_none(v: float | None) -> int | None:
    return int(v) if v is not None else None
