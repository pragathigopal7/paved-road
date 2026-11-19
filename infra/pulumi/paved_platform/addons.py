"""Cluster wide platform add-ons: metrics, telemetry pipeline, autoscaling, CI."""

from __future__ import annotations

import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

from paved_platform.config import PlatformConfig
from paved_platform.helpers import PLATFORM_DIR, deep_merge, helm_release, policy_json, values_file

# Namespace -> Pod Security level. Node exporter needs host access; ARC job pods run
# arbitrary CI images, so they get baseline rather than restricted.
SYSTEM_NAMESPACES = {
    "monitoring": "privileged",
    "observability": "restricted",
    "keda": "baseline",
    "arc-systems": "restricted",
    "arc-runners": "baseline",
}

POD_IDENTITY_TRUST = policy_json(
    [
        {
            "Effect": "Allow",
            "Principal": {"Service": "pods.eks.amazonaws.com"},
            "Action": ["sts:AssumeRole", "sts:TagSession"],
        }
    ]
)


class PlatformAddons(pulumi.ComponentResource):
    def __init__(
        self,
        cfg: PlatformConfig,
        k8s_provider: k8s.Provider,
        opts: pulumi.ResourceOptions | None = None,
    ):
        super().__init__("paved:platform:Addons", cfg.environment, None, opts)
        self.resources: list[pulumi.Resource] = []
        child = pulumi.ResourceOptions(parent=self)
        kopts = pulumi.ResourceOptions(parent=self, provider=k8s_provider)

        namespaces = {
            name: k8s.core.v1.Namespace(
                name,
                metadata={
                    "name": name,
                    "labels": {
                        "paved.dev/managed": "true",
                        "pod-security.kubernetes.io/enforce": level,
                    },
                },
                opts=kopts,
            )
            for name, level in SYSTEM_NAMESPACES.items()
        }
        self.namespaces = namespaces
        self.resources += list(namespaces.values())

        def in_ns(name: str, *extra: pulumi.Resource) -> pulumi.ResourceOptions:
            return pulumi.ResourceOptions(
                parent=self, provider=k8s_provider, depends_on=[namespaces[name], *extra]
            )

        gp3 = k8s.storage.v1.StorageClass(
            "gp3",
            metadata={
                "name": "gp3",
                "annotations": {"storageclass.kubernetes.io/is-default-class": "true"},
            },
            provisioner="ebs.csi.aws.com",
            parameters={"type": "gp3", "encrypted": "true"},
            volume_binding_mode="WaitForFirstConsumer",
            allow_volume_expansion=True,
            reclaim_policy="Delete",
            opts=kopts,
        )

        # Long term metrics: Amazon Managed Prometheus, written with Pod Identity.
        self.amp = aws.amp.Workspace("metrics", alias=f"paved-{cfg.environment}", opts=child)
        writer = aws.iam.Role(
            "amp-writer",
            name=f"paved-{cfg.environment}-amp-writer",
            assume_role_policy=POD_IDENTITY_TRUST,
            opts=child,
        )
        writer_policy = aws.iam.RolePolicyAttachment(
            "amp-writer",
            role=writer.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonPrometheusRemoteWriteAccess",
            opts=child,
        )
        associations = [
            aws.eks.PodIdentityAssociation(
                f"amp-writer-{ns}",
                cluster_name=cfg.foundation.cluster_name,
                namespace=ns,
                service_account=sa,
                role_arn=writer.arn,
                opts=child,
            )
            for ns, sa in (("monitoring", "kps-prometheus"), ("observability", "otel-collector"))
        ]
        remote_write_url = self.amp.prometheus_endpoint.apply(lambda e: f"{e}api/v1/remote_write")

        kps = helm_release(
            "kube-prometheus-stack",
            "kube-prometheus-stack",
            "monitoring",
            remote_write_url.apply(
                lambda url: deep_merge(
                    values_file("prometheus/kube-prometheus-stack-values.yaml"),
                    {
                        "prometheus": {
                            "prometheusSpec": {
                                "externalLabels": {"cluster": cfg.foundation.cluster_name},
                                "remoteWrite": [{"url": url, "sigv4": {"region": cfg.region}}],
                            }
                        }
                    },
                )
            ),
            in_ns("monitoring", gp3, *associations),
        )

        alerts = k8s.yaml.v2.ConfigFile(
            "platform-alerts",
            file=str(PLATFORM_DIR / "prometheus" / "alerts.yaml"),
            opts=pulumi.ResourceOptions(parent=self, provider=k8s_provider, depends_on=[kps]),
        )

        collector = helm_release(
            "otel-collector",
            "opentelemetry-collector",
            "observability",
            remote_write_url.apply(
                lambda url: deep_merge(
                    values_file("otel-collector/values.yaml"),
                    {
                        "extraEnvs": [
                            {"name": "AMP_REMOTE_WRITE_URL", "value": url},
                            {"name": "TRACES_OTLP_ENDPOINT", "value": cfg.traces_endpoint},
                            {"name": "LOGS_ENDPOINT", "value": cfg.logs_endpoint},
                            {"name": "CLUSTER_NAME", "value": cfg.foundation.cluster_name},
                            {"name": "AWS_REGION", "value": cfg.region},
                        ]
                    },
                )
            ),
            in_ns("observability", kps, *associations),
        )

        # Cluster Autoscaler: scales the managed node groups, including the spot CI pool
        # from zero. EKS tags each node group's ASG for auto discovery; the role can only
        # resize ASGs carrying this cluster's tag.
        autoscaler_role = aws.iam.Role(
            "cluster-autoscaler",
            name=f"paved-{cfg.environment}-cluster-autoscaler",
            assume_role_policy=POD_IDENTITY_TRUST,
            opts=child,
        )
        autoscaler_policy = aws.iam.RolePolicy(
            "cluster-autoscaler",
            role=autoscaler_role.id,
            policy=policy_json(
                [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "autoscaling:DescribeAutoScalingGroups",
                            "autoscaling:DescribeAutoScalingInstances",
                            "autoscaling:DescribeLaunchConfigurations",
                            "autoscaling:DescribeScalingActivities",
                            "autoscaling:DescribeTags",
                            "ec2:DescribeImages",
                            "ec2:DescribeInstanceTypes",
                            "ec2:DescribeLaunchTemplateVersions",
                            "ec2:GetInstanceTypesFromInstanceRequirements",
                            "eks:DescribeNodegroup",
                        ],
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "autoscaling:SetDesiredCapacity",
                            "autoscaling:TerminateInstanceInAutoScalingGroup",
                        ],
                        "Resource": "*",
                        "Condition": {
                            "StringEquals": {
                                "aws:ResourceTag/k8s.io/cluster-autoscaler/"
                                f"{cfg.foundation.cluster_name}": "owned"
                            }
                        },
                    },
                ]
            ),
            opts=child,
        )
        autoscaler_identity = aws.eks.PodIdentityAssociation(
            "cluster-autoscaler",
            cluster_name=cfg.foundation.cluster_name,
            namespace="kube-system",
            service_account="cluster-autoscaler",
            role_arn=autoscaler_role.arn,
            opts=child,
        )
        autoscaler = helm_release(
            "cluster-autoscaler",
            "cluster-autoscaler",
            "kube-system",
            {
                "autoDiscovery": {"clusterName": cfg.foundation.cluster_name},
                "awsRegion": cfg.region,
                "rbac": {"serviceAccount": {"name": "cluster-autoscaler"}},
                "extraArgs": {
                    "balance-similar-node-groups": True,
                    "expander": "least-waste",
                    "skip-nodes-with-local-storage": False,
                    "scale-down-unneeded-time": "5m",
                },
                "serviceMonitor": {"enabled": True, "namespace": "monitoring"},
            },
            pulumi.ResourceOptions(
                parent=self, provider=k8s_provider, depends_on=[autoscaler_identity, kps]
            ),
        )

        # KEDA scales Kafka consumers on lag (see the service template's ScaledObject).
        keda = helm_release(
            "keda",
            "keda",
            "keda",
            {"prometheus": {"operator": {"enabled": True, "serviceMonitor": {"enabled": True}}}},
            in_ns("keda", kps),
        )

        self.arc_controller = helm_release(
            "arc",
            "arc-controller",
            "arc-systems",
            values_file("arc/controller-values.yaml"),
            in_ns("arc-systems", kps),
        )

        self.resources += [
            gp3,
            self.amp,
            writer,
            writer_policy,
            *associations,
            autoscaler_role,
            autoscaler_policy,
            autoscaler_identity,
            autoscaler,
            kps,
            alerts,
            collector,
            keda,
            self.arc_controller,
        ]
        self.register_outputs({"amp_remote_write_url": remote_write_url})
