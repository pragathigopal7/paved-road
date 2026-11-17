"""Pinned chart versions in one place, so upgrades are a one line, reviewable diff."""

ARC_OCI = "oci://ghcr.io/actions/actions-runner-controller-charts"

CHARTS: dict[str, tuple[str | None, str, str]] = {
    # name: (repository or None for OCI, chart reference, version)
    "kube-prometheus-stack": (
        "https://prometheus-community.github.io/helm-charts",
        "kube-prometheus-stack",
        "69.8.2",
    ),
    "opentelemetry-collector": (
        "https://open-telemetry.github.io/opentelemetry-helm-charts",
        "opentelemetry-collector",
        "0.117.1",
    ),
    "keda": ("https://kedacore.github.io/charts", "keda", "2.17.0"),
    "cluster-autoscaler": (
        "https://kubernetes.github.io/autoscaler",
        "cluster-autoscaler",
        "9.43.2",
    ),
    "arc-controller": (None, f"{ARC_OCI}/gha-runner-scale-set-controller", "0.12.1"),
    "arc-runner-scale-set": (None, f"{ARC_OCI}/gha-runner-scale-set", "0.12.1"),
}
