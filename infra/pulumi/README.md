# Pulumi platform layer

Everything inside the cluster, plus per team AWS resources, built on the outputs of
`infra/terraform/platform`.

```text
__main__.py               entry point
teams.yaml                team onboarding (one entry per team)
paved_platform/config.py  typed, validated config from stack config, teams.yaml, TF outputs
paved_platform/addons.py  monitoring, OTel collector, KEDA, Cluster Autoscaler, ARC
paved_platform/tenancy.py namespace, quota, network policy, ECR, CI push role, SSO access
paved_platform/runners.py one GitHub Actions runner scale set per team, plus paved-platform
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

## First deploy

`platform-deploy.yml` and `drift.yml` run on `paved-platform`, a runner scale set this
program creates, so the first `pulumi up` cannot come from CI. Once per environment:

1. Apply the Terraform foundation by hand ([`infra/terraform/README.md`](../terraform/README.md)).
2. Run `pulumi up` (above) from somewhere that can reach the private EKS endpoint, as a
   role in `admin_role_arns`: a host inside the VPC, or temporarily set
   `endpoint_public_access = true` with your own /32 in `public_access_cidrs`, and turn
   it off again afterwards.
3. Install a GitHub App on the account with repository Administration read and write
   (plus organization Self hosted runners read and write for the team scale sets, if
   the account is an organization), then give ARC its credentials:

   ```bash
   kubectl -n arc-runners create secret generic arc-github-app \
     --from-literal=github_app_id=<app id> \
     --from-literal=github_app_installation_id=<installation id> \
     --from-file=github_app_private_key=<app>.private-key.pem
   ```

   The scale sets register with GitHub once the secret exists; ARC keeps retrying until
   then.

From then on every change goes through `platform-deploy.yml` on `paved-platform`. The
platform scale set is registered on this repository only, so no other repository can
run jobs on a runner that assumes the apply role, and it uses the on demand general pool
so a spot reclaim cannot interrupt an apply.

## Test

```bash
pytest -o addopts="" -q tests
```

No cloud access needed: the mocks record every resource the program declares, and the
tests assert on the inputs Pulumi would send, for example that runner pods are not
privileged, push roles trust only the team's repositories on main, and the autoscaler can
only resize this cluster's node groups.
