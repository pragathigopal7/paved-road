"""Unit tests for the Pulumi program using Pulumi's mock engine: no cloud, no cluster.

The mocks record every resource the program declares, so the tests assert on the
actual inputs Pulumi would send to AWS and Kubernetes.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pulumi
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


class Mocks(pulumi.runtime.Mocks):
    def __init__(self) -> None:
        self.resources: list[pulumi.runtime.MockResourceArgs] = []

    def new_resource(self, args: pulumi.runtime.MockResourceArgs):
        self.resources.append(args)
        outputs = dict(args.inputs)
        if args.typ.startswith("aws:"):
            outputs.setdefault("arn", f"arn:aws:mock:us-west-2:123456789012:{args.name}")
        if args.typ == "aws:amp/workspace:Workspace":
            outputs["prometheusEndpoint"] = (
                "https://aps-workspaces.us-west-2.amazonaws.com/workspaces/ws-1234/"
            )
        if args.typ == "aws:ecr/repository:Repository":
            outputs["repositoryUrl"] = (
                f"123456789012.dkr.ecr.us-west-2.amazonaws.com/{args.inputs['name']}"
            )
        return [f"{args.name}-id", outputs]

    def call(self, args: pulumi.runtime.MockCallArgs):
        return {}

    def of_type(self, typ: str) -> list[pulumi.runtime.MockResourceArgs]:
        return [r for r in self.resources if r.typ == typ]

    def named(self, typ: str, name: str) -> pulumi.runtime.MockResourceArgs:
        [match] = [r for r in self.of_type(typ) if r.name == name]
        return match


MOCKS = Mocks()
pulumi.runtime.set_mocks(MOCKS, preview=False)

from paved_platform import build, kubeconfig  # noqa: E402
from paved_platform.config import (  # noqa: E402
    ConfigError,
    Foundation,
    PlatformConfig,
    Team,
    load_teams,
)


def make_config(teams_yaml: str | None = None) -> PlatformConfig:
    return PlatformConfig(
        environment="dev",
        region="us-west-2",
        github_org="pragathigopal7",
        foundation=Foundation.from_terraform_json((HERE / "tf-outputs.json").read_text()),
        teams=load_teams(teams_yaml or (HERE.parent / "teams.yaml").read_text()),
        traces_endpoint="http://tempo.observability:4318",
        logs_endpoint="https://logs.example.com",
    )


CFG = make_config()
PLATFORM = build(CFG)


def after_deploy(check):
    """Run `check` once every declared resource has been registered with the mocks."""
    return pulumi.Output.all(*[r.urn for r in PLATFORM.resources]).apply(lambda _: check())


@pulumi.runtime.test
def test_team_namespaces_enforce_restricted_pod_security():
    def check():
        for team in ("payments", "search"):
            ns = MOCKS.named("kubernetes:core/v1:Namespace", f"{team}-ns")
            labels = ns.inputs["metadata"]["labels"]
            assert labels["pod-security.kubernetes.io/enforce"] == "restricted"
            assert labels["team"] == team

    return after_deploy(check)


@pulumi.runtime.test
def test_quota_default_deny_and_no_load_balancers():
    def check():
        quota = MOCKS.named("kubernetes:core/v1:ResourceQuota", "payments-quota")
        hard = quota.inputs["spec"]["hard"]
        assert hard["requests.cpu"] == "20" and hard["pods"] == "200"
        assert hard["services.loadbalancers"] == "0"
        deny = MOCKS.named("kubernetes:networking.k8s.io/v1:NetworkPolicy", "payments-default-deny")
        assert deny.inputs["spec"] == {"podSelector": {}, "policyTypes": ["Ingress"]}

    return after_deploy(check)


@pulumi.runtime.test
def test_ecr_repositories_are_immutable_scanned_and_encrypted():
    def check():
        repos = MOCKS.of_type("aws:ecr/repository:Repository")
        assert sorted(r.inputs["name"] for r in repos) == [
            "indexer",
            "ledger-api",
            "orders-consumer",
        ]
        for r in repos:
            assert r.inputs["imageTagMutability"] == "IMMUTABLE"
            assert r.inputs["imageScanningConfiguration"]["scanOnPush"] is True
            assert r.inputs["encryptionConfigurations"][0]["encryptionType"] == "KMS"
        assert len(MOCKS.of_type("aws:ecr/lifecyclePolicy:LifecyclePolicy")) == 3

    return after_deploy(check)


@pulumi.runtime.test
def test_ci_push_role_trusts_only_team_repos_on_main():
    def check():
        role = MOCKS.named("aws:iam/role:Role", "payments-ci-push")
        stmt = json.loads(role.inputs["assumeRolePolicy"])["Statement"][0]
        assert stmt["Principal"]["Federated"].endswith(
            "oidc-provider/token.actions.githubusercontent.com"
        )
        subs = stmt["Condition"]["StringLike"]["token.actions.githubusercontent.com:sub"]
        assert subs == ["repo:pragathigopal7/paved-road:ref:refs/heads/main"]
        policy = json.loads(
            MOCKS.named("aws:iam/rolePolicy:RolePolicy", "payments-ci-push").inputs["policy"]
        )
        push = [s for s in policy["Statement"] if s["Action"] != "ecr:GetAuthorizationToken"][0]
        assert all("payments-" in arn for arn in push["Resource"])  # only this team's repos

    return after_deploy(check)


@pulumi.runtime.test
def test_sso_access_is_namespace_scoped_and_optional():
    def check():
        assoc = MOCKS.of_type("aws:eks/accessPolicyAssociation:AccessPolicyAssociation")
        assert [a.name for a in assoc] == ["payments-sso-edit"]  # search has no SSO role
        scope = assoc[0].inputs["accessScope"]
        assert scope == {"type": "namespace", "namespaces": ["payments"]}
        assert assoc[0].inputs["policyArn"].endswith("AmazonEKSEditPolicy")

    return after_deploy(check)


@pulumi.runtime.test
def test_runner_scale_sets_are_isolated_and_unprivileged():
    def check():
        release = MOCKS.named("kubernetes:helm.sh/v3:Release", "arc-search")
        values = release.inputs["values"]
        assert values["runnerScaleSetName"] == "paved-search"
        assert (values["minRunners"], values["maxRunners"]) == (1, 20)
        assert values["containerMode"]["type"] == "kubernetes"  # no Docker in Docker
        spec = values["template"]["spec"]
        assert spec["nodeSelector"] == {"paved.dev/workload": "ci"}
        assert spec["tolerations"][0]["key"] == "paved.dev/ci"
        runner = spec["containers"][0]
        assert not runner.get("securityContext", {}).get("privileged", False)
        assert ":latest" not in runner["image"]
        assert values["template"]["metadata"]["labels"]["team"] == "search"

    return after_deploy(check)


@pulumi.runtime.test
def test_platform_runner_exists_for_deploy_and_drift_workflows():
    workflows = HERE.parents[2] / ".github" / "workflows"
    targets = {
        line.split(":", 1)[1].strip()
        for name in ("platform-deploy.yml", "drift.yml")
        for line in (workflows / name).read_text().splitlines()
        if line.strip().startswith("runs-on:")
    }

    def check():
        release = MOCKS.named("kubernetes:helm.sh/v3:Release", "arc-platform")
        values = release.inputs["values"]
        assert targets == {values["runnerScaleSetName"]} == {"paved-platform"}
        # Registered on the platform repository only, not the whole account.
        assert values["githubConfigUrl"] == "https://github.com/pragathigopal7/paved-road"
        assert "runnerGroup" not in values
        assert values["containerMode"]["type"] == "kubernetes"
        # On demand general pool: a spot reclaim must not kill a terraform apply.
        spec = values["template"]["spec"]
        assert spec["nodeSelector"] == {"paved.dev/pool": "general"}
        assert "tolerations" not in spec
        assert not spec["containers"][0].get("securityContext", {}).get("privileged", False)
        assert release.inputs["namespace"] == "arc-runners"

    return after_deploy(check)


def test_platform_repository_is_configurable_and_validated():
    assert dataclasses.replace(CFG, platform_repository="acme/infra").platform_repository == (
        "acme/infra"
    )
    with pytest.raises(ConfigError, match="owner/repo"):
        dataclasses.replace(CFG, platform_repository="not a repo")


@pulumi.runtime.test
def test_metrics_pipeline_writes_to_amp_with_pod_identity():
    def check():
        kps = MOCKS.named("kubernetes:helm.sh/v3:Release", "kube-prometheus-stack")
        rw = kps.inputs["values"]["prometheus"]["prometheusSpec"]["remoteWrite"][0]
        assert rw["url"].endswith("/workspaces/ws-1234/api/v1/remote_write")
        assert rw["sigv4"] == {"region": "us-west-2"}
        otel = MOCKS.named("kubernetes:helm.sh/v3:Release", "otel-collector")
        env = {e["name"]: e["value"] for e in otel.inputs["values"]["extraEnvs"]}
        assert env["CLUSTER_NAME"] == "paved-dev"
        writers = {
            (p.inputs["namespace"], p.inputs["serviceAccount"])
            for p in MOCKS.of_type("aws:eks/podIdentityAssociation:PodIdentityAssociation")
            if p.name.startswith("amp-writer")
        }
        assert writers == {("monitoring", "kps-prometheus"), ("observability", "otel-collector")}

    return after_deploy(check)


@pulumi.runtime.test
def test_cluster_autoscaler_can_only_resize_this_clusters_node_groups():
    def check():
        release = MOCKS.named("kubernetes:helm.sh/v3:Release", "cluster-autoscaler")
        assert release.inputs["values"]["autoDiscovery"]["clusterName"] == "paved-dev"
        policy = json.loads(
            MOCKS.named("aws:iam/rolePolicy:RolePolicy", "cluster-autoscaler").inputs["policy"]
        )
        [mutating] = [s for s in policy["Statement"] if "Condition" in s]
        assert "autoscaling:SetDesiredCapacity" in mutating["Action"]
        tag = mutating["Condition"]["StringEquals"]
        assert tag == {"aws:ResourceTag/k8s.io/cluster-autoscaler/paved-dev": "owned"}
        identity = MOCKS.named(
            "aws:eks/podIdentityAssociation:PodIdentityAssociation", "cluster-autoscaler"
        )
        assert identity.inputs["namespace"] == "kube-system"

    return after_deploy(check)


@pulumi.runtime.test
def test_charts_are_pinned():
    def check():
        for release in MOCKS.of_type("kubernetes:helm.sh/v3:Release"):
            assert release.inputs.get("version"), f"{release.name} has no pinned chart version"

    return after_deploy(check)


def test_kubeconfig_uses_exec_auth_not_a_static_token():
    doc = json.loads(kubeconfig(CFG))
    user = doc["users"][0]["user"]
    assert "token" not in user
    assert user["exec"]["args"][:2] == ["eks", "get-token"]


QUOTA = {"cpu": "1", "memory": "1Gi", "pods": 1}


@pytest.mark.parametrize(
    "team,match",
    [
        ({"name": "kube-system", "repositories": ["a/b"], "quota": QUOTA}, "reserved"),
        ({"name": "platform", "repositories": ["a/b"], "quota": QUOTA}, "reserved"),
        ({"name": "Pay", "repositories": ["a/b"], "quota": QUOTA}, "DNS label"),
        ({"name": "pay", "repositories": [], "quota": QUOTA}, "repositories"),
        ({"name": "pay", "repositories": ["a/b"], "quota": {**QUOTA, "cpu": "lots"}}, "quantity"),
        (
            {
                "name": "pay",
                "repositories": ["a/b"],
                "quota": QUOTA,
                "runners": {"min": 5, "max": 2},
            },
            "runners",
        ),
    ],
)
def test_invalid_team_config_is_rejected(team, match):
    with pytest.raises(ConfigError, match=match):
        Team.from_dict(team)


def test_duplicate_services_across_teams_are_rejected():
    dup = """
teams:
  - {name: a, repositories: [o/a], services: [api], quota: {cpu: "1", memory: 1Gi, pods: 10}}
  - {name: b, repositories: [o/b], services: [api], quota: {cpu: "1", memory: 1Gi, pods: 10}}
"""
    with pytest.raises(ConfigError, match="unique"):
        make_config(dup)
