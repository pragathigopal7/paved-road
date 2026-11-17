"""Self hosted GitHub Actions runners: one autoscaling ARC scale set per team."""

from __future__ import annotations

import pulumi
import pulumi_kubernetes as k8s

from paved_platform.config import PlatformConfig, Team
from paved_platform.helpers import deep_merge, helm_release, values_file

RUNNER_NAMESPACE = "arc-runners"


def scale_set_values(team: Team, cfg: PlatformConfig) -> dict:
    """Platform defaults from platform/arc, plus what differs per team."""
    return deep_merge(
        values_file("arc/runner-scale-set-values.yaml"),
        {
            "githubConfigUrl": f"https://github.com/{cfg.github_org}",
            # Workflows target this with `runs-on: paved-<team>`.
            "runnerScaleSetName": f"paved-{team.name}",
            # GitHub runner group: limits which repositories can use these runners.
            "runnerGroup": f"paved-{team.name}",
            "minRunners": team.runners.min,
            "maxRunners": team.runners.max,
            "template": {"metadata": {"labels": {"team": team.name}}},
        },
    )


class RunnerScaleSet(pulumi.ComponentResource):
    def __init__(
        self,
        team: Team,
        cfg: PlatformConfig,
        k8s_provider: k8s.Provider,
        controller: pulumi.Resource,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("paved:platform:RunnerScaleSet", team.name, None, opts)
        self.values = scale_set_values(team, cfg)
        self.release = helm_release(
            f"arc-{team.name}",
            "arc-runner-scale-set",
            RUNNER_NAMESPACE,
            self.values,
            pulumi.ResourceOptions(parent=self, provider=k8s_provider, depends_on=[controller]),
        )
        self.resources = [self.release]
        self.register_outputs({"scale_set": self.values["runnerScaleSetName"]})
