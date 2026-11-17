"""Platform policy for Kubernetes workloads.

The rules encode the paved road contract: a service that ships on the platform is
secure by default (restricted pod security, network policy, pinned images) and
observable by default (metrics scraped, traces exported, JSON logs, ownership labels).

Every rule can be waived per workload with two annotations, and a waiver without a
reason is itself an error, so exceptions stay visible in code review:

    metadata:
      annotations:
        paved.dev/waive: "PR012,PR015"
        paved.dev/waive-reason: "batch importer, logs go to S3 via sidecar"
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from paved import __version__
from paved.manifests import K8sObject, selector_matches

WAIVE_ANNOTATION = "paved.dev/waive"
WAIVE_REASON_ANNOTATION = "paved.dev/waive-reason"
API_ACCESS_ANNOTATION = "paved.dev/needs-api-access"

LONG_RUNNING = {"Deployment", "StatefulSet", "DaemonSet"}
OWNERSHIP_LABELS = ("app.kubernetes.io/name", "team")
SEVERITIES = ("error", "warning")


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    severity: str
    summary: str
    rationale: str
    check: Callable[[K8sObject, Bundle], Iterator[str]] = field(repr=False, compare=False)


@dataclass
class Finding:
    rule_id: str
    rule_name: str
    severity: str
    message: str
    object_ref: str
    path: str
    line: int
    waived: bool = False
    waive_reason: str | None = None

    @property
    def blocking(self) -> bool:
        return self.severity == "error" and not self.waived


class Bundle:
    """All objects under review, so rules can look across objects (Service, PDB, ...)."""

    def __init__(self, objects: Iterable[K8sObject]):
        self.objects = list(objects)

    def of_kind(self, *kinds: str) -> list[K8sObject]:
        return [o for o in self.objects if o.kind in kinds]

    def same_namespace(self, a: K8sObject, b: K8sObject) -> bool:
        # Namespaces are often injected later by Kustomize; treat a missing one as a match.
        return a.namespace is None or b.namespace is None or a.namespace == b.namespace


RULES: dict[str, Rule] = {}


def rule(rule_id: str, name: str, severity: str, summary: str, rationale: str):
    assert severity in SEVERITIES

    def register(fn: Callable[[K8sObject, Bundle], Iterator[str]]):
        RULES[rule_id] = Rule(rule_id, name, severity, summary, rationale, fn)
        return fn

    return register


def _csc(container: dict[str, Any]) -> dict[str, Any]:
    return container.get("securityContext") or {}


def _effective(container: dict[str, Any], pod_spec: dict[str, Any], key: str) -> Any:
    """Container securityContext wins over the pod level setting, as in Kubernetes."""
    value = _csc(container).get(key)
    if value is None:
        value = (pod_spec.get("securityContext") or {}).get(key)
    return value


def _env(container: dict[str, Any]) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in container.get("env") or []:
        if isinstance(item, dict) and "name" in item:
            env[str(item["name"])] = str(item.get("value", "<valueFrom>"))
    return env


# Security rules ------------------------------------------------------------------


@rule(
    "PR001",
    "run-as-non-root",
    "error",
    "Containers must run as a non root user.",
    "A container breakout from root lands as root on the node.",
)
def _run_as_non_root(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    spec = obj.pod_spec()
    for c in obj.containers(include_init=True):
        if _effective(c, spec, "runAsNonRoot") is not True:
            yield f"container '{c.get('name')}' does not set runAsNonRoot: true"
        if _effective(c, spec, "runAsUser") == 0:
            yield f"container '{c.get('name')}' sets runAsUser: 0"


@rule(
    "PR002",
    "read-only-root-filesystem",
    "error",
    "Containers must mount the root filesystem read only.",
    "Stops an attacker from dropping tools or persisting changes in the image layer.",
)
def _read_only_rootfs(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    for c in obj.containers(include_init=True):
        if _csc(c).get("readOnlyRootFilesystem") is not True:
            yield f"container '{c.get('name')}' does not set readOnlyRootFilesystem: true"


@rule(
    "PR003",
    "no-privilege-escalation",
    "error",
    "allowPrivilegeEscalation must be false.",
    "Blocks setuid binaries from gaining more privileges than the parent process.",
)
def _no_priv_esc(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    for c in obj.containers(include_init=True):
        if _csc(c).get("allowPrivilegeEscalation") is not False:
            yield f"container '{c.get('name')}' does not set allowPrivilegeEscalation: false"


@rule(
    "PR004",
    "drop-all-capabilities",
    "error",
    "Containers must drop ALL Linux capabilities.",
    "Required by the restricted Pod Security Standard; add back only what is needed.",
)
def _drop_all_caps(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    for c in obj.containers(include_init=True):
        drop = {str(x).upper() for x in (_csc(c).get("capabilities") or {}).get("drop") or []}
        if "ALL" not in drop:
            yield f"container '{c.get('name')}' does not drop ALL capabilities"


@rule(
    "PR005",
    "no-privileged-or-host-namespaces",
    "error",
    "No privileged containers, hostNetwork, hostPID or hostIPC.",
    "Each of these removes the isolation boundary between the pod and the node.",
)
def _no_privileged(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    spec = obj.pod_spec()
    for key in ("hostNetwork", "hostPID", "hostIPC"):
        if spec.get(key) is True:
            yield f"pod spec sets {key}: true"
    for c in obj.containers(include_init=True):
        if _csc(c).get("privileged") is True:
            yield f"container '{c.get('name')}' is privileged"


@rule(
    "PR006",
    "seccomp-runtime-default",
    "warning",
    "Pods should use the RuntimeDefault (or Localhost) seccomp profile.",
    "Filters dangerous syscalls at almost no cost.",
)
def _seccomp(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    spec = obj.pod_spec()
    pod_profile = ((spec.get("securityContext") or {}).get("seccompProfile") or {}).get("type")
    for c in obj.containers(include_init=True):
        profile = (_csc(c).get("seccompProfile") or {}).get("type") or pod_profile
        if profile not in ("RuntimeDefault", "Localhost"):
            yield f"container '{c.get('name')}' has no RuntimeDefault seccomp profile"


@rule(
    "PR009",
    "pinned-image",
    "error",
    "Images must use an explicit tag other than latest, or a digest.",
    "Mutable tags make rollbacks and incident timelines unreliable.",
)
def _pinned_image(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    for c in obj.containers(include_init=True):
        image = str(c.get("image") or "")
        if not image:
            yield f"container '{c.get('name')}' has no image"
            continue
        if "@sha256:" in image:
            continue
        last = image.rsplit("/", 1)[-1]
        tag = last.split(":", 1)[1] if ":" in last else None
        if tag in (None, "", "latest"):
            yield f"container '{c.get('name')}' image '{image}' is not pinned to a tag or digest"


@rule(
    "PR014",
    "network-policy",
    "error",
    "Every workload must be selected by a NetworkPolicy.",
    "The platform default denies ingress; a policy documents who may call the service.",
)
def _network_policy(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    labels = obj.pod_labels()
    for np in bundle.of_kind("NetworkPolicy"):
        if bundle.same_namespace(obj, np) and selector_matches(
            (np.data.get("spec") or {}).get("podSelector"), labels
        ):
            return
    yield "no NetworkPolicy in the bundle selects this workload's pods"


@rule(
    "PR016",
    "service-account-token",
    "warning",
    "Disable automountServiceAccountToken unless the pod calls the Kubernetes API.",
    "A mounted token is a credential an attacker can use against the API server.",
)
def _sa_token(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    if obj.pod_annotations().get(API_ACCESS_ANNOTATION) == "true":
        return
    if obj.pod_spec().get("automountServiceAccountToken") is not False:
        yield (
            "automountServiceAccountToken is not false; set it, or annotate the pod "
            f"with {API_ACCESS_ANNOTATION}: 'true' if it needs the API"
        )


@rule(
    "PR017",
    "dedicated-service-account",
    "warning",
    "Workloads should run under their own ServiceAccount.",
    "Sharing 'default' means sharing IAM (IRSA) and RBAC with every other pod.",
)
def _dedicated_sa(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    sa = obj.pod_spec().get("serviceAccountName")
    if not sa or sa == "default":
        yield "pod runs as the 'default' ServiceAccount"


# Reliability and cost rules -----------------------------------------------------


@rule(
    "PR007",
    "resources",
    "error",
    "Set CPU and memory requests and a memory limit.",
    "Requests drive scheduling and cost allocation; a memory limit contains leaks. "
    "CPU limits are optional on purpose because CFS throttling hurts tail latency.",
)
def _resources(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    for c in obj.containers():
        res = c.get("resources") or {}
        requests = res.get("requests") or {}
        limits = res.get("limits") or {}
        missing = [f"requests.{k}" for k in ("cpu", "memory") if k not in requests]
        if "memory" not in limits:
            missing.append("limits.memory")
        if missing:
            yield f"container '{c.get('name')}' is missing {', '.join(missing)}"


@rule(
    "PR008",
    "health-probes",
    "error",
    "Long running containers need readiness and liveness probes.",
    "Without readiness, rollouts send traffic to pods that cannot serve it.",
)
def _probes(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    if obj.kind not in LONG_RUNNING:
        return
    for c in obj.containers():
        missing = [p for p in ("readinessProbe", "livenessProbe") if not c.get(p)]
        if missing:
            yield f"container '{c.get('name')}' is missing {', '.join(missing)}"


@rule(
    "PR015",
    "disruption-budget",
    "warning",
    "Workloads with more than one replica should have a PodDisruptionBudget.",
    "Node upgrades and spot reclaims otherwise evict every replica at once.",
)
def _pdb(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    if obj.kind not in ("Deployment", "StatefulSet"):
        return
    replicas = (obj.data.get("spec") or {}).get("replicas", 1)
    scaled = any(
        ((h.data.get("spec") or {}).get("scaleTargetRef") or {}).get("name") == obj.name
        for h in bundle.of_kind("HorizontalPodAutoscaler")
    )
    if (isinstance(replicas, int) and replicas <= 1) and not scaled:
        return
    labels = obj.pod_labels()
    for pdb in bundle.of_kind("PodDisruptionBudget"):
        # A PDB without a selector matches nothing, unlike an empty NetworkPolicy selector.
        selector = (pdb.data.get("spec") or {}).get("selector")
        if selector and bundle.same_namespace(obj, pdb) and selector_matches(selector, labels):
            return
    yield "runs more than one replica but no PodDisruptionBudget selects it"


# Observability rules --------------------------------------------------------------


@rule(
    "PR010",
    "metrics-scraped",
    "error",
    "Long running workloads must be scraped by Prometheus.",
    "Use a ServiceMonitor or PodMonitor (preferred) or prometheus.io/scrape annotations.",
)
def _metrics(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    if obj.kind not in LONG_RUNNING:
        return
    labels = obj.pod_labels()
    if obj.pod_annotations().get("prometheus.io/scrape") == "true":
        return
    for pm in bundle.of_kind("PodMonitor"):
        if selector_matches((pm.data.get("spec") or {}).get("selector"), labels):
            return
    services = [
        s
        for s in bundle.of_kind("Service")
        if bundle.same_namespace(obj, s)
        and (s.data.get("spec") or {}).get("selector")
        and selector_matches({"matchLabels": s.data["spec"]["selector"]}, labels)
    ]
    for sm in bundle.of_kind("ServiceMonitor"):
        sel = (sm.data.get("spec") or {}).get("selector")
        if any(selector_matches(sel, s.labels) for s in services):
            return
    yield "no ServiceMonitor, PodMonitor or prometheus.io/scrape annotation covers this workload"


@rule(
    "PR011",
    "tracing-configured",
    "error",
    "Containers must export OpenTelemetry traces.",
    "Set OTEL_EXPORTER_OTLP_ENDPOINT and OTEL_SERVICE_NAME (or service.name in "
    "OTEL_RESOURCE_ATTRIBUTES) so spans reach the collector with the right identity.",
)
def _tracing(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    for c in obj.containers():
        env = _env(c)
        missing = []
        if "OTEL_EXPORTER_OTLP_ENDPOINT" not in env:
            missing.append("OTEL_EXPORTER_OTLP_ENDPOINT")
        if "OTEL_SERVICE_NAME" not in env and "service.name=" not in env.get(
            "OTEL_RESOURCE_ATTRIBUTES", ""
        ):
            missing.append("OTEL_SERVICE_NAME")
        if missing:
            yield f"container '{c.get('name')}' is missing env {', '.join(missing)}"


@rule(
    "PR012",
    "json-logs",
    "warning",
    "Containers should log JSON (LOG_FORMAT=json).",
    "Structured logs are parsed once at the collector instead of by regex in every query.",
)
def _json_logs(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    for c in obj.containers():
        if _env(c).get("LOG_FORMAT", "").lower() != "json":
            yield f"container '{c.get('name')}' does not set LOG_FORMAT=json"


@rule(
    "PR013",
    "ownership-labels",
    "error",
    "Pods must carry app.kubernetes.io/name and team labels.",
    "Labels route alerts to an owner and attribute cost; unlabeled pods are orphans.",
)
def _ownership(obj: K8sObject, bundle: Bundle) -> Iterator[str]:
    labels = obj.pod_labels()
    missing = [k for k in OWNERSHIP_LABELS if not labels.get(k)]
    if missing:
        yield f"pod template is missing labels {', '.join(missing)}"


# Engine ---------------------------------------------------------------------------


def _waivers(obj: K8sObject) -> tuple[set[str], str | None]:
    raw = obj.annotations.get(WAIVE_ANNOTATION, "")
    ids = {x.strip().upper() for x in raw.split(",") if x.strip()}
    reason = obj.annotations.get(WAIVE_REASON_ANNOTATION, "").strip() or None
    return ids, reason


def evaluate(objects: Iterable[K8sObject], only: Iterable[str] | None = None) -> list[Finding]:
    """Run every rule against every workload in the bundle."""
    bundle = Bundle(objects)
    selected = [RULES[r] for r in sorted(RULES) if not only or r in set(only)]
    findings: list[Finding] = []
    for obj in bundle.objects:
        if not obj.is_workload:
            continue
        waived_ids, reason = _waivers(obj)
        if waived_ids and not reason:
            findings.append(
                Finding(
                    "PR000",
                    "waiver-needs-reason",
                    "error",
                    f"{WAIVE_ANNOTATION} is set without {WAIVE_REASON_ANNOTATION}; "
                    "the waiver is ignored",
                    obj.ref,
                    obj.path,
                    obj.line,
                )
            )
        for r in selected:
            for message in r.check(obj, bundle):
                waived = r.id in waived_ids and reason is not None
                findings.append(
                    Finding(
                        r.id,
                        r.name,
                        r.severity,
                        message,
                        obj.ref,
                        obj.path,
                        obj.line,
                        waived=waived,
                        waive_reason=reason if waived else None,
                    )
                )
    return findings


@dataclass
class Report:
    findings: list[Finding]
    workloads: int

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.blocking)

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning" and not f.waived)

    @property
    def waived(self) -> int:
        return sum(1 for f in self.findings if f.waived)

    def failed(self, strict: bool = False) -> bool:
        return self.errors > 0 or (strict and self.warnings > 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workloads": self.workloads,
            "errors": self.errors,
            "warnings": self.warnings,
            "waived": self.waived,
            "findings": [asdict(f) for f in self.findings],
        }


def check(objects: Iterable[K8sObject], only: Iterable[str] | None = None) -> Report:
    objs = list(objects)
    return Report(evaluate(objs, only), sum(1 for o in objs if o.is_workload))


# Output formats -----------------------------------------------------------------------


def format_text(report: Report) -> str:
    lines: list[str] = []
    for f in report.findings:
        tag = "WAIVED" if f.waived else f.severity.upper()
        lines.append(f"{f.path}:{f.line}  {tag:<7} {f.rule_id} {f.rule_name}  {f.object_ref}")
        lines.append(f"    {f.message}")
        if f.waived:
            lines.append(f"    waiver reason: {f.waive_reason}")
    verdict = "FAIL" if report.failed() else "PASS"
    lines.append(
        f"{verdict}: {report.workloads} workloads, {report.errors} errors, "
        f"{report.warnings} warnings, {report.waived} waived"
    )
    return "\n".join(lines)


def format_github(report: Report) -> str:
    """GitHub Actions workflow commands, rendered as inline PR annotations."""
    out = []
    for f in report.findings:
        if f.waived:
            continue
        level = "error" if f.severity == "error" else "warning"
        msg = f"{f.object_ref}: {f.message}".replace("%", "%25").replace("\n", "%0A")
        out.append(f"::{level} file={f.path},line={f.line},title={f.rule_id} {f.rule_name}::{msg}")
    out.append(format_text(report).splitlines()[-1])
    return "\n".join(out)


def format_sarif(report: Report, base: str | Path = ".") -> str:
    """SARIF 2.1.0 for GitHub code scanning (upload with codeql-action/upload-sarif)."""
    base_path = Path(base).resolve()
    all_rules = [
        Rule(
            "PR000",
            "waiver-needs-reason",
            "error",
            "Waivers must include a reason.",
            "Unexplained exceptions rot.",
            lambda o, b: iter(()),
        ),
        *(RULES[r] for r in sorted(RULES)),
    ]

    def uri(p: str) -> str:
        try:
            return Path(p).resolve().relative_to(base_path).as_posix()
        except ValueError:
            return Path(p).as_posix()

    results = []
    for f in report.findings:
        result: dict[str, Any] = {
            "ruleId": f.rule_id,
            "level": "error" if f.severity == "error" else "warning",
            "message": {"text": f"{f.object_ref}: {f.message}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": uri(f.path)},
                        "region": {"startLine": f.line},
                    }
                }
            ],
        }
        if f.waived:
            result["suppressions"] = [{"kind": "inSource", "justification": f.waive_reason}]
        results.append(result)

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "paved",
                        "version": __version__,
                        "informationUri": "https://github.com/pragathigopal7/paved-road",
                        "rules": [
                            {
                                "id": r.id,
                                "name": r.name,
                                "shortDescription": {"text": r.summary},
                                "fullDescription": {"text": r.rationale},
                                "defaultConfiguration": {
                                    "level": "error" if r.severity == "error" else "warning"
                                },
                            }
                            for r in all_rules
                        ],
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)


def format_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2)
