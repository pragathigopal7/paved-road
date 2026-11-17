"""Per team tenancy: everything a team needs to ship on the cluster, from one entry in
teams.yaml."""

from __future__ import annotations

import json

import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from paved_platform.config import PlatformConfig, Team
from paved_platform.helpers import policy_json

ECR_PUSH_ACTIONS = [
    "ecr:BatchCheckLayerAvailability",
    "ecr:BatchGetImage",
    "ecr:CompleteLayerUpload",
    "ecr:GetDownloadUrlForLayer",
    "ecr:InitiateLayerUpload",
    "ecr:PutImage",
    "ecr:UploadLayerPart",
]

ECR_LIFECYCLE = json.dumps(
    {
        "rules": [
            {
                "rulePriority": 1,
                "description": "Drop untagged layers from failed or superseded builds",
                "selection": {
                    "tagStatus": "untagged",
                    "countType": "sinceImagePushed",
                    "countUnit": "days",
                    "countNumber": 7,
                },
                "action": {"type": "expire"},
            },
            {
                "rulePriority": 2,
                "description": "Keep the last 100 images for rollbacks",
                "selection": {
                    "tagStatus": "any",
                    "countType": "imageCountMoreThan",
                    "countNumber": 100,
                },
                "action": {"type": "expire"},
            },
        ]
    }
)


class TeamTenancy(pulumi.ComponentResource):
    def __init__(
        self,
        team: Team,
        cfg: PlatformConfig,
        k8s_provider: k8s.Provider,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("paved:platform:TeamTenancy", team.name, None, opts)
        self.resources: list[pulumi.Resource] = []
        aws_opts = pulumi.ResourceOptions(parent=self)
        labels = {"team": team.name, "paved.dev/managed": "true"}

        # Namespace with the restricted Pod Security Standard enforced by the API server.
        # The paved road template already complies, so this only bites hand written YAML.
        ns = k8s.core.v1.Namespace(
            f"{team.name}-ns",
            metadata={
                "name": team.name,
                "labels": {
                    **labels,
                    "pod-security.kubernetes.io/enforce": "restricted",
                    "pod-security.kubernetes.io/enforce-version": "latest",
                    "pod-security.kubernetes.io/warn": "restricted",
                    "pod-security.kubernetes.io/audit": "restricted",
                },
            },
            opts=pulumi.ResourceOptions(parent=self, provider=k8s_provider),
        )
        in_ns = pulumi.ResourceOptions(parent=self, provider=k8s_provider, depends_on=[ns])
        meta = {"namespace": team.name, "labels": labels}

        quota = k8s.core.v1.ResourceQuota(
            f"{team.name}-quota",
            metadata={**meta, "name": "team-quota"},
            spec={
                "hard": {
                    "requests.cpu": team.quota.cpu,
                    "requests.memory": team.quota.memory,
                    "limits.memory": team.quota.memory,
                    "pods": str(team.quota.pods),
                    # Teams share the platform ingress instead of buying their own NLBs.
                    "services.loadbalancers": "0",
                }
            },
            opts=in_ns,
        )

        # Defaults for containers that forget requests, so the quota can still admit them.
        limits = k8s.core.v1.LimitRange(
            f"{team.name}-limits",
            metadata={**meta, "name": "container-defaults"},
            spec={
                "limits": [
                    {
                        "type": "Container",
                        "defaultRequest": {"cpu": "100m", "memory": "128Mi"},
                        "default": {"memory": "256Mi"},
                        "max": {"memory": "8Gi"},
                    }
                ]
            },
            opts=in_ns,
        )

        deny = k8s.networking.v1.NetworkPolicy(
            f"{team.name}-default-deny",
            metadata={**meta, "name": "default-deny-ingress"},
            spec={"podSelector": {}, "policyTypes": ["Ingress"]},
            opts=in_ns,
        )

        # One ECR repository per service: immutable tags, scanned on push, KMS encrypted.
        self.repositories: dict[str, aws.ecr.Repository] = {}
        for svc in team.services:
            repo = aws.ecr.Repository(
                f"{team.name}-{svc}",
                name=svc,
                image_tag_mutability="IMMUTABLE",
                image_scanning_configuration={"scan_on_push": True},
                encryption_configurations=[{"encryption_type": "KMS"}],
                tags={"team": team.name},
                opts=aws_opts,
            )
            lifecycle = aws.ecr.LifecyclePolicy(
                f"{team.name}-{svc}-lifecycle",
                repository=repo.name,
                policy=ECR_LIFECYCLE,
                opts=aws_opts,
            )
            self.repositories[svc] = repo
            self.resources += [repo, lifecycle]

        # CI push role: only the team's repositories, only from main, only these repos.
        trust = policy_json(
            [
                {
                    "Effect": "Allow",
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Principal": {"Federated": cfg.foundation.github_oidc_provider_arn},
                    "Condition": {
                        "StringEquals": {
                            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                        },
                        "StringLike": {
                            "token.actions.githubusercontent.com:sub": [
                                f"repo:{r}:ref:refs/heads/main" for r in team.repositories
                            ]
                        },
                    },
                }
            ]
        )
        self.push_role = aws.iam.Role(
            f"{team.name}-ci-push",
            name=f"paved-{cfg.environment}-{team.name}-ci-push",
            assume_role_policy=trust,
            max_session_duration=3600,
            tags={"team": team.name},
            opts=aws_opts,
        )
        repo_arns = [r.arn for r in self.repositories.values()]
        push_policy = aws.iam.RolePolicy(
            f"{team.name}-ci-push",
            role=self.push_role.id,
            policy=pulumi.Output.all(*repo_arns).apply(
                lambda arns: policy_json(
                    [
                        {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
                        *(
                            [
                                {
                                    "Effect": "Allow",
                                    "Action": ECR_PUSH_ACTIONS,
                                    "Resource": list(arns),
                                }
                            ]
                            if arns
                            else []
                        ),
                    ]
                )
            ),
            opts=aws_opts,
        )

        # kubectl access for the team's SSO role, scoped to its own namespace.
        if team.sso_role_arn:
            entry = aws.eks.AccessEntry(
                f"{team.name}-sso",
                cluster_name=cfg.foundation.cluster_name,
                principal_arn=team.sso_role_arn,
                type="STANDARD",
                opts=aws_opts,
            )
            assoc = aws.eks.AccessPolicyAssociation(
                f"{team.name}-sso-edit",
                cluster_name=cfg.foundation.cluster_name,
                principal_arn=team.sso_role_arn,
                policy_arn="arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy",
                access_scope={"type": "namespace", "namespaces": [team.name]},
                opts=pulumi.ResourceOptions(parent=self, depends_on=[entry]),
            )
            self.resources += [entry, assoc]

        self.resources += [ns, quota, limits, deny, self.push_role, push_policy]
        self.register_outputs(
            {
                "namespace": team.name,
                "push_role_arn": self.push_role.arn,
                "repositories": {s: r.repository_url for s, r in self.repositories.items()},
            }
        )
