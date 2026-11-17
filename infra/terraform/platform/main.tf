# Platform foundation for one environment: network, EKS, and the GitHub OIDC roles
# the deploy pipeline uses. Everything inside the cluster (tenancy, runners, collector)
# is layered on top by the Pulumi program in infra/pulumi, which reads these outputs.
#
#   terraform init -backend-config=env/dev.backend.hcl
#   terraform plan -var-file=env/dev.tfvars

provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}

locals {
  cluster_name = "paved-${var.environment}"

  tags = {
    environment = var.environment
    managed-by  = "terraform"
    repository  = "github.com/${var.github_repository}"
    cost-center = var.cost_center
  }

  state_key_prefix = "platform/${var.environment}/"
}

module "network" {
  source = "../modules/network"

  name               = local.cluster_name
  cluster_name       = local.cluster_name
  cidr               = var.vpc_cidr
  single_nat_gateway = var.single_nat_gateway
}

# GitHub Actions identity -------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_caller_identity" "current" {}

locals {
  github_oidc_provider_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
}

# Pull requests and main can plan with read only access.
module "ci_plan" {
  source = "../modules/github-oidc"

  role_name         = "${local.cluster_name}-ci-plan"
  description       = "Terraform plan from pull requests and main"
  oidc_provider_arn = local.github_oidc_provider_arn
  subjects = [
    "repo:${var.github_repository}:pull_request",
    "repo:${var.github_repository}:ref:refs/heads/main",
  ]
  managed_policy_arns = ["arn:aws:iam::aws:policy/ReadOnlyAccess"]
  state_bucket        = var.state_bucket
  state_key_prefix    = local.state_key_prefix
  state_kms_key_arn   = var.state_kms_key_arn
}

# Only a job running in the GitHub environment of the same name can apply. That
# environment has required reviewers and is limited to the main branch, so the trust
# policy, not the workflow file, is what enforces "reviewed changes only".
module "ci_apply" {
  source = "../modules/github-oidc"

  role_name                = "${local.cluster_name}-ci-apply"
  description              = "Terraform and Pulumi apply from the ${var.environment} environment"
  oidc_provider_arn        = local.github_oidc_provider_arn
  subjects                 = ["repo:${var.github_repository}:environment:${var.environment}"]
  managed_policy_arns      = var.apply_policy_arns
  permissions_boundary_arn = var.apply_permissions_boundary_arn
  state_bucket             = var.state_bucket
  state_key_prefix         = local.state_key_prefix
  state_kms_key_arn        = var.state_kms_key_arn
  state_write              = true
}

# Kubernetes ----------------------------------------------------------------------------

module "eks" {
  source = "../modules/eks"

  cluster_name           = local.cluster_name
  kubernetes_version     = var.kubernetes_version
  subnet_ids             = module.network.private_subnet_ids
  endpoint_public_access = length(var.api_allowed_cidrs) > 0
  public_access_cidrs    = var.api_allowed_cidrs
  admin_roles = merge(
    { for arn in var.admin_role_arns : arn => arn },
    { ci-apply = module.ci_apply.role_arn },
  )
  node_groups = var.node_groups
}
