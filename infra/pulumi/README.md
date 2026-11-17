# Pulumi platform layer

Everything inside the cluster, plus per team AWS resources, built on the outputs of
`infra/terraform/platform`.

```text
__main__.py               entry point
teams.yaml                team onboarding (one entry per team)
paved_platform/config.py  typed, validated config from stack config, teams.yaml, TF outputs
paved_platform/addons.py  monitoring, OTel collector, KEDA, Cluster Autoscaler, ARC
paved_platform/tenancy.py namespace, quota, network policy, ECR, CI push role, SSO access
paved_platform/runners.py one GitHub Actions runner scale set per team
paved_platform/versions.py pinned Helm chart versions
tests/                    unit tests against Pulumi's mock engine
```

Helm values live in [`platform/`](../../platform) at the repository root so the local
compose stack and the cluster share them.

## Run

```bash
pip install -r requirements.txt
terraform -chdir=../terraform/platform output -json > tf-outputs.dev.json
pulumi login s3://<state-bucket>/pulumi
pulumi stack init dev --secrets-provider="awskms://alias/paved-road-tfstate?region=us-west-2"
pulumi up -s dev
```

In CI, `platform-deploy.yml` does the same after the Terraform apply.

## Test

```bash
pytest -o addopts="" -q tests
```

No cloud access needed: the mocks record every resource the program declares, and the
tests assert on the inputs Pulumi would send, for example that runner pods are not
privileged, push roles trust only the team's repositories on main, and the autoscaler can
only resize this cluster's node groups.
