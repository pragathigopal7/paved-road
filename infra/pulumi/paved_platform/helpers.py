from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pulumi
import pulumi_kubernetes as k8s
import yaml

from paved_platform.versions import CHARTS

REPO_ROOT = Path(__file__).resolve().parents[3]
PLATFORM_DIR = REPO_ROOT / "platform"


def values_file(relative: str) -> dict[str, Any]:
    return yaml.safe_load((PLATFORM_DIR / relative).read_text()) or {}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge override into a copy of base. Dicts merge, everything else replaces."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def helm_release(
    name: str,
    chart_key: str,
    namespace: str,
    values: dict[str, Any] | pulumi.Output,
    opts: pulumi.ResourceOptions,
) -> k8s.helm.v3.Release:
    repo, chart, version = CHARTS[chart_key]
    return k8s.helm.v3.Release(
        name,
        k8s.helm.v3.ReleaseArgs(
            name=name,
            chart=chart,
            version=version,
            namespace=namespace,
            repository_opts=k8s.helm.v3.RepositoryOptsArgs(repo=repo) if repo else None,
            values=values,
            atomic=True,
            cleanup_on_fail=True,
            max_history=10,
            timeout=600,
        ),
        opts=opts,
    )


def policy_json(statements: list[dict[str, Any]]) -> str:
    return json.dumps({"Version": "2012-10-17", "Statement": statements})
