# Terraform foundation

```text
bootstrap/          state bucket and KMS key (apply once per account, local state)
modules/network/    VPC, subnets, NAT, S3 endpoint, flow logs
modules/eks/        cluster, node groups, managed add-ons
modules/github-oidc/  IAM role for GitHub Actions through OIDC
platform/           root module per environment (env/<env>.tfvars, env/<env>.backend.hcl)
```

## First time setup

```bash
# 1. State bucket
terraform -chdir=bootstrap init
terraform -chdir=bootstrap apply
#    put the bucket name and KMS key ARN into platform/env/<env>.backend.hcl and .tfvars

# 2. Foundation
cd platform
terraform init -backend-config=env/dev.backend.hcl
terraform plan -var-file=env/dev.tfvars -out=tfplan
terraform apply tfplan

# 3. Wire up GitHub
terraform output ci_plan_role_arn   # repository variable AWS_PLAN_ROLE_ARN
terraform output ci_apply_role_arn  # environment variable AWS_DEPLOY_ROLE_ARN on "dev"
```

After that, changes go through pull requests and `platform-deploy.yml`.

## Tests

Every module has a `tests/` directory with `terraform test` suites. The AWS provider is
mocked, so they run in seconds without credentials:

```bash
terraform -chdir=modules/eks init -backend=false && terraform -chdir=modules/eks test
```

`make test-terraform` from the repository root runs fmt, validate and tests for every
module (set `TERRAFORM=tofu` to use OpenTofu).
