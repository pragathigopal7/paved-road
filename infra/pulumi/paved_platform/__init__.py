"""Platform layer: add-ons, team tenancy and CI runners on the Terraform built cluster."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pulumi
import pulumi_kubernetes as k8s

from paved_platform.addons import PlatformAddons
from paved_platform.config import PlatformConfig
from paved_platform.runners import (
    RunnerScaleSet,
    platform_scale_set_values,
    scale_set_values,
)
from paved_platform.tenancy import TeamTenancy


def kubeconfig(cfg: PlatformConfig) -> str:
    """Kubeconfig that authenticates with `aws eks get-token`, so no static token exists."""
    f = cfg.foundation
    return json.dumps(
        {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "name": f.cluster_name,
                    "cluster": {
                        "server": f.cluster_endpoint,
                        "certificate-authority-data": f.cluster_certificate_authority,
                    },
                }
            ],
            "contexts": [
                {
                    "name": f.cluster_name,
                    "context": {"cluster": f.cluster_name, "user": f.cluster_name},
                }
            ],
            "current-context": f.cluster_name,
            "users": [
                {
                    "name": f.cluster_name,
                    "user": {
                        "exec": {
                            "apiVersion": "client.authentication.k8s.io/v1beta1",
                            "command": "aws",
                            "args": [
                                "eks",
                                "get-token",
                                "--cluster-name",
                                f.cluster_name,
                                "--region",
                                cfg.region,
                            ],
                            "interactiveMode": "Never",
                        }
                    },
                }
            ],
        }
    )


@dataclass
class Platform:
    addons: PlatformAddons
    teams: dict[str, TeamTenancy] = field(default_factory=dict)
    runners: dict[str, RunnerScaleSet] = field(default_factory=dict)

    @property
    def resources(self) -> list[pulumi.Resource]:
        out = list(self.addons.resources)
        for t in self.teams.values():
            out += t.resources
        for r in self.runners.values():
            out += r.resources
        return out


def build(cfg: PlatformConfig) -> Platform:
    provider = k8s.Provider("eks", kubeconfig=kubeconfig(cfg), enable_server_side_apply=True)
    addons = PlatformAddons(cfg, provider)
    platform = Platform(addons)
    platform.runners["platform"] = RunnerScaleSet(
        "platform",
        platform_scale_set_values(cfg),
        provider,
        controller=addons.arc_controller,
        opts=pulumi.ResourceOptions(depends_on=[addons.namespaces["arc-runners"]]),
    )
    for team in cfg.teams:
        platform.teams[team.name] = TeamTenancy(team, cfg, provider)
        platform.runners[team.name] = RunnerScaleSet(
            team.name,
            scale_set_values(team, cfg),
            provider,
            controller=addons.arc_controller,
            opts=pulumi.ResourceOptions(depends_on=[platform.teams[team.name]]),
        )
    return platform
