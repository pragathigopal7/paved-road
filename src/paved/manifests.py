"""Load Kubernetes manifests from files and directories, keeping source locations.

Every object remembers the file and line it came from so that policy findings can be
reported as GitHub annotations or SARIF results that point at the right place in a PR.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

YAML_SUFFIXES = {".yaml", ".yml"}

# Kinds whose pods we can inspect, and where the pod template lives in each.
WORKLOAD_POD_PATHS: dict[str, tuple[str, ...]] = {
    "Deployment": ("spec", "template"),
    "StatefulSet": ("spec", "template"),
    "DaemonSet": ("spec", "template"),
    "ReplicaSet": ("spec", "template"),
    "Job": ("spec", "template"),
    "CronJob": ("spec", "jobTemplate", "spec", "template"),
    "Pod": (),
}


class ManifestError(ValueError):
    """Raised when a file cannot be parsed as Kubernetes YAML."""


@dataclass(frozen=True)
class K8sObject:
    """One Kubernetes object plus where it was defined."""

    data: dict[str, Any]
    path: str
    line: int  # 1 based line where the YAML document starts
    doc_index: int = 0

    @property
    def kind(self) -> str:
        return str(self.data.get("kind", ""))

    @property
    def name(self) -> str:
        return str(self.metadata.get("name", "<unnamed>"))

    @property
    def namespace(self) -> str | None:
        ns = self.metadata.get("namespace")
        return str(ns) if ns else None

    @property
    def metadata(self) -> dict[str, Any]:
        meta = self.data.get("metadata") or {}
        return meta if isinstance(meta, dict) else {}

    @property
    def labels(self) -> dict[str, str]:
        return _str_dict(self.metadata.get("labels"))

    @property
    def annotations(self) -> dict[str, str]:
        return _str_dict(self.metadata.get("annotations"))

    @property
    def ref(self) -> str:
        ns = f"{self.namespace}/" if self.namespace else ""
        return f"{self.kind}/{ns}{self.name}"

    @property
    def is_workload(self) -> bool:
        return self.kind in WORKLOAD_POD_PATHS

    def pod_template(self) -> dict[str, Any] | None:
        """Return the pod template (metadata + spec) for workload kinds, else None."""
        if not self.is_workload:
            return None
        node: Any = self.data
        for key in WORKLOAD_POD_PATHS[self.kind]:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node if isinstance(node, dict) else None

    def pod_spec(self) -> dict[str, Any]:
        template = self.pod_template() or {}
        spec = template.get("spec") or {}
        return spec if isinstance(spec, dict) else {}

    def pod_labels(self) -> dict[str, str]:
        template = self.pod_template() or {}
        return _str_dict((template.get("metadata") or {}).get("labels"))

    def pod_annotations(self) -> dict[str, str]:
        template = self.pod_template() or {}
        return _str_dict((template.get("metadata") or {}).get("annotations"))

    def containers(self, include_init: bool = False) -> list[dict[str, Any]]:
        spec = self.pod_spec()
        found = [c for c in spec.get("containers") or [] if isinstance(c, dict)]
        if include_init:
            found += [c for c in spec.get("initContainers") or [] if isinstance(c, dict)]
        return found


def _str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def iter_yaml_files(paths: Iterable[str | Path]) -> Iterator[Path]:
    """Yield YAML files under the given files or directories, in a stable order."""
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            yield from sorted(f for f in p.rglob("*") if f.suffix in YAML_SUFFIXES and f.is_file())
        elif p.is_file():
            yield p
        else:
            raise ManifestError(f"path does not exist: {p}")


def load_text(text: str, path: str = "<string>") -> list[K8sObject]:
    """Parse a multi document YAML string into Kubernetes objects.

    Documents that are empty or are not Kubernetes objects (no apiVersion and kind)
    are skipped, so Kustomize files and Helm values can live next to manifests.
    """
    objects: list[K8sObject] = []
    loader = yaml.SafeLoader(text)
    index = 0
    try:
        while loader.check_node():
            node = loader.get_node()
            data = loader.construct_document(node)
            line = node.start_mark.line + 1
            index += 1
            if not isinstance(data, dict) or "kind" not in data or "apiVersion" not in data:
                continue
            if data["kind"] == "List":
                for item in data.get("items") or []:
                    if isinstance(item, dict) and "kind" in item:
                        objects.append(K8sObject(item, path, line, index - 1))
                continue
            objects.append(K8sObject(data, path, line, index - 1))
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: invalid YAML: {exc}") from exc
    finally:
        loader.dispose()
    return objects


def load_paths(paths: Iterable[str | Path]) -> list[K8sObject]:
    objects: list[K8sObject] = []
    for f in iter_yaml_files(paths):
        objects.extend(load_text(f.read_text(encoding="utf-8"), str(f)))
    return objects


def selector_matches(selector: dict[str, Any] | None, labels: dict[str, str]) -> bool:
    """Evaluate a Kubernetes label selector (matchLabels and matchExpressions).

    An empty selector matches everything, which is how Kubernetes treats an empty
    podSelector in a NetworkPolicy.
    """
    if not selector:
        return True
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(str(key)) != str(value):
            return False
    for expr in selector.get("matchExpressions") or []:
        key = str(expr.get("key"))
        op = expr.get("operator")
        values = {str(v) for v in expr.get("values") or []}
        present = key in labels
        if op == "In" and (not present or labels[key] not in values):
            return False
        if op == "NotIn" and present and labels[key] in values:
            return False
        if op == "Exists" and not present:
            return False
        if op == "DoesNotExist" and present:
            return False
    return True
