"""Entry point: `pulumi up -s dev` after the Terraform foundation is applied."""

from pathlib import Path

import pulumi

from paved_platform import build
from paved_platform.config import load

cfg = load(Path(__file__).parent, pulumi.Config(), pulumi.Config("aws").require("region"))
platform = build(cfg)

pulumi.export("amp_workspace_id", platform.addons.amp.id)
pulumi.export("team_namespaces", sorted(platform.teams))
pulumi.export("ci_push_roles", {name: t.push_role.arn for name, t in platform.teams.items()})
pulumi.export(
    "runner_scale_sets",
    {name: r.values["runnerScaleSetName"] for name, r in platform.runners.items()},
)
