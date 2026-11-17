"""Generate a new service from a paved road template.

Templates live in paved/templates/<name>/ as files ending in .tmpl with @@variable@@
placeholders. (@@ rather than {{ }} because templates contain GitHub Actions
expressions.) Path parts starting with dot_ become dotfiles (dot_github is written as
.github), because Python packaging skips real dotfiles.

The contract, enforced by tests: anything this module generates passes `paved check`
with zero errors and zero warnings. Teams start compliant instead of retrofitting.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

_PLACEHOLDER = re.compile(r"@@([a-z_]+)@@")
_DNS_LABEL = re.compile(r"^[a-z]([a-z0-9-]*[a-z0-9])?$")
_TOPIC = re.compile(r"^[a-zA-Z0-9._-]{1,249}$")

# Deployment names are used in pod names (<name>-<rs hash>-<pod hash>), which must stay
# within 63 characters.
MAX_SERVICE_NAME = 47


class ScaffoldError(ValueError):
    pass


@dataclass
class ServiceSpec:
    name: str
    team: str
    topic: str
    namespace: str | None = None
    image_registry: str = "123456789012.dkr.ecr.us-west-2.amazonaws.com"
    environment: str = "dev"
    bootstrap_servers: str = "kafka-bootstrap.kafka.svc.cluster.local:9092"
    max_replicas: int = 6
    python_version: str = "3.12"
    paved_ref: str = "main"

    def validate(self) -> None:
        if not _DNS_LABEL.match(self.name) or len(self.name) > MAX_SERVICE_NAME:
            raise ScaffoldError(
                f"service name {self.name!r} must be a lowercase DNS label of at most "
                f"{MAX_SERVICE_NAME} characters"
            )
        if not _DNS_LABEL.match(self.team):
            raise ScaffoldError(f"team {self.team!r} must be a lowercase DNS label")
        if self.namespace and not _DNS_LABEL.match(self.namespace):
            raise ScaffoldError(f"namespace {self.namespace!r} must be a lowercase DNS label")
        if not _TOPIC.match(self.topic):
            raise ScaffoldError(f"topic {self.topic!r} is not a valid Kafka topic name")
        if not 1 <= self.max_replicas <= 100:
            raise ScaffoldError("max_replicas must be between 1 and 100")

    def values(self) -> dict[str, str]:
        d = {k: str(v) for k, v in asdict(self).items()}
        d["service_name"] = self.name
        d["namespace"] = self.namespace or self.team
        d["consumer_group"] = self.name
        d["dlq_topic"] = f"{self.topic}.dlq"
        d["image"] = f"{self.image_registry.rstrip('/')}/{self.name}"
        return d


def available_templates() -> list[str]:
    root = resources.files("paved") / "templates"
    return sorted(e.name for e in root.iterdir() if e.is_dir() and not e.name.startswith("_"))


def _walk(node: Traversable, prefix: str = "") -> list[tuple[str, Traversable]]:
    out: list[tuple[str, Traversable]] = []
    for entry in sorted(node.iterdir(), key=lambda e: e.name):
        if entry.name == "__pycache__":
            continue
        rel = f"{prefix}{entry.name}"
        if entry.is_dir():
            out.extend(_walk(entry, rel + "/"))
        else:
            out.append((rel, entry))
    return out


def render(spec: ServiceSpec, template: str = "kafka-consumer") -> dict[str, str]:
    """Return {relative path: file content} without touching the filesystem."""
    spec.validate()
    if template not in available_templates():
        raise ScaffoldError(f"unknown template {template!r}; have {available_templates()}")
    values = spec.values()
    files: dict[str, str] = {}
    root = resources.files("paved") / "templates" / template
    for rel, entry in _walk(root):
        if not rel.endswith(".tmpl"):
            continue
        out_path = rel[: -len(".tmpl")]
        out_path = "/".join(
            "." + p[len("dot_") :] if p.startswith("dot_") else p for p in out_path.split("/")
        )
        text = entry.read_text(encoding="utf-8")

        def sub(m: re.Match[str], _rel: str = rel) -> str:
            key = m.group(1)
            if key not in values:
                raise ScaffoldError(f"{_rel}: unknown placeholder @@{key}@@")
            return values[key]

        files[out_path] = _PLACEHOLDER.sub(sub, text)
    return files


def write(files: dict[str, str], out_dir: str | Path, force: bool = False) -> list[Path]:
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()) and not force:
        raise ScaffoldError(f"{out} exists and is not empty (use --force to overwrite)")
    written = []
    for rel, content in sorted(files.items()):
        target = (out / rel).resolve()
        if out.resolve() not in target.parents:
            raise ScaffoldError(f"refusing to write outside {out}: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written
