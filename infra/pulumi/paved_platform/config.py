"""Typed, validated configuration for the platform layer.

Inputs come from three places: Pulumi stack config, `terraform output -json` from the
foundation layer, and teams.yaml. Everything is validated before any resource is
declared, so a typo in teams.yaml fails `pulumi preview` with a clear message instead
of half applying.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DNS_LABEL = re.compile(r"^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_QUANTITY = re.compile(r"^[0-9]+(\.[0-9]+)?(m|Ki|Mi|Gi|Ti)?$")
RESERVED_NAMESPACES = {
    "default",
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "monitoring",
    "observability",
    "keda",
    "arc-systems",
    "arc-runners",
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Quota:
    cpu: str
    memory: str
    pods: int


@dataclass(frozen=True)
class Runners:
    min: int = 0
    max: int = 5


@dataclass(frozen=True)
class Team:
    name: str
    repositories: tuple[str, ...]
    services: tuple[str, ...]
    quota: Quota
    runners: Runners = field(default_factory=Runners)
    sso_role_arn: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Team:
        name = str(d.get("name", ""))
        if not _DNS_LABEL.match(name) or name in RESERVED_NAMESPACES:
            raise ConfigError(
                f"team name {name!r} must be a DNS label and not a reserved namespace"
            )
        repos = tuple(d.get("repositories") or ())
        if not repos or not all(_REPO.match(r) for r in repos):
            raise ConfigError(f"team {name}: repositories must be a non empty list of owner/repo")
        services = tuple(d.get("services") or ())
        if not all(_DNS_LABEL.match(s) for s in services):
            raise ConfigError(f"team {name}: service names must be DNS labels")
        q = d.get("quota") or {}
        try:
            quota = Quota(cpu=str(q["cpu"]), memory=str(q["memory"]), pods=int(q["pods"]))
        except KeyError as exc:
            raise ConfigError(f"team {name}: quota needs cpu, memory and pods") from exc
        for value in (quota.cpu, quota.memory):
            if not _QUANTITY.match(value):
                raise ConfigError(f"team {name}: {value!r} is not a Kubernetes quantity")
        r = d.get("runners") or {}
        runners = Runners(min=int(r.get("min", 0)), max=int(r.get("max", 5)))
        if not 0 <= runners.min <= runners.max <= 100:
            raise ConfigError(f"team {name}: need 0 <= runners.min <= runners.max <= 100")
        sso = d.get("sso_role_arn")
        if sso and not str(sso).startswith("arn:aws:iam::"):
            raise ConfigError(f"team {name}: sso_role_arn must be an IAM role ARN")
        return cls(name, repos, services, quota, runners, sso)


@dataclass(frozen=True)
class Foundation:
    """Outputs of infra/terraform/platform."""

    cluster_name: str
    cluster_endpoint: str
    cluster_certificate_authority: str
    github_oidc_provider_arn: str

    @classmethod
    def from_terraform_json(cls, text: str) -> Foundation:
        raw = json.loads(text)
        values = {
            k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in raw.items()
        }
        try:
            return cls(**{f: values[f] for f in cls.__dataclass_fields__})
        except KeyError as exc:
            raise ConfigError(f"terraform outputs are missing {exc}") from exc


@dataclass(frozen=True)
class PlatformConfig:
    environment: str
    region: str
    github_org: str
    foundation: Foundation
    teams: tuple[Team, ...]
    traces_endpoint: str
    logs_endpoint: str

    def __post_init__(self) -> None:
        names = [t.name for t in self.teams]
        if len(names) != len(set(names)):
            raise ConfigError("team names must be unique")
        services = [s for t in self.teams for s in t.services]
        if len(services) != len(set(services)):
            raise ConfigError("service names must be unique across teams (one ECR repo each)")


def load_teams(text: str) -> tuple[Team, ...]:
    doc = yaml.safe_load(text) or {}
    return tuple(Team.from_dict(t) for t in doc.get("teams") or [])


def load(base_dir: Path, cfg: Any, region: str) -> PlatformConfig:
    """Build PlatformConfig from a pulumi.Config-like object."""
    return PlatformConfig(
        environment=cfg.require("environment"),
        region=region,
        github_org=cfg.require("githubOrg"),
        foundation=Foundation.from_terraform_json(
            (base_dir / cfg.require("terraformOutputs")).read_text()
        ),
        teams=load_teams((base_dir / (cfg.get("teamsFile") or "teams.yaml")).read_text()),
        traces_endpoint=cfg.require("tracesEndpoint"),
        logs_endpoint=cfg.require("logsEndpoint"),
    )
