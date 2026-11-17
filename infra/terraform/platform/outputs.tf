# infra/pulumi reads these through `terraform output -json` (see infra/pulumi/README).

output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "Kubernetes API server endpoint."
  value       = module.eks.cluster_endpoint
}

output "cluster_certificate_authority" {
  description = "Base64 CA bundle for the API server."
  value       = module.eks.cluster_certificate_authority
}

output "vpc_id" {
  description = "VPC ID."
  value       = module.network.vpc_id
}

output "private_subnet_ids" {
  description = "Private subnet IDs."
  value       = module.network.private_subnet_ids
}

output "ci_plan_role_arn" {
  description = "Set as the AWS_PLAN_ROLE_ARN repository variable."
  value       = module.ci_plan.role_arn
}

output "ci_apply_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN variable on the GitHub environment."
  value       = module.ci_apply.role_arn
}

output "github_oidc_provider_arn" {
  description = "Reused by Pulumi for per team ECR push roles."
  value       = local.github_oidc_provider_arn
}

output "kubeconfig_command" {
  description = "Command to get kubectl access."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}
