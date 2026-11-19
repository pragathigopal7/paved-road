"""Self hosted GitHub Actions runners: one autoscaling ARC scale set per team, plus
`paved-platform`, the scale set the platform's own deploy and drift workflows run on."""

from __future__ import annotations

import pulumi
import pulumi_kubernetes as k8s

from paved_platform.config import PlatformConfig, Team
from paved_platform.helpers import deep_merge, helm_release, values_file

RUNNER_NAMESPACE = "arc-runners"
PLATFORM_SCALE_SET = "paved-platform"


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


def platform_scale_set_values(cfg: PlatformConfig) -> dict:
    """The runner platform-deploy.yml and drift.yml target with `runs-on: paved-platform`.

    Registered on the platform repository itself, so no other repository can schedule
    jobs on a runner whose jobs assume the Terraform apply role. It runs on the on demand
    general pool instead of the spot CI pool: a spot reclaim in the middle of
    `terraform apply` leaves a held state lock and a half applied change.
    """
    values = deep_merge(
        values_file("arc/runner-scale-set-values.yaml"),
        {
            "githubConfigUrl": f"https://github.com/{cfg.platform_repository}",
            "runnerScaleSetName": PLATFORM_SCALE_SET,
            # Drift checks dev and prod in parallel while a deploy may be running.
            "minRunners": 0,
            "maxRunners": 3,
            "template": {"metadata": {"labels": {"team": "platform"}}},
        },
    )
    spec = values["template"]["spec"]
    spec["nodeSelector"] = {"paved.dev/pool": "general"}
    spec.pop("tolerations", None)
    return values


class RunnerScaleSet(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        values: dict,
        k8s_provider: k8s.Provider,
        controller: pulumi.Resource,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("paved:platform:RunnerScaleSet", name, None, opts)
        self.values = values
        self.release = helm_release(
            f"arc-{name}",
            "arc-runner-scale-set",
            RUNNER_NAMESPACE,
            self.values,
            pulumi.ResourceOptions(parent=self, provider=k8s_provider, depends_on=[controller]),
        )
        self.resources = [self.release]
        self.register_outputs({"scale_set": self.values["runnerScaleSetName"]})
