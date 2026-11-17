output "cluster_name" {
  description = "EKS cluster name."
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "Kubernetes API server endpoint."
  value       = aws_eks_cluster.this.endpoint
}

output "cluster_certificate_authority" {
  description = "Base64 CA bundle for the API server."
  value       = aws_eks_cluster.this.certificate_authority[0].data
}

output "cluster_security_group_id" {
  description = "Security group EKS created for the control plane and nodes."
  value       = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
}

output "node_role_arn" {
  description = "IAM role used by managed nodes."
  value       = aws_iam_role.node.arn
}

output "node_groups" {
  description = "Node group names keyed by pool."
  value       = { for k, g in aws_eks_node_group.this : k => g.node_group_name }
}

output "admin_access_entries" {
  description = "Cluster admin access entries keyed by name."
  value       = { for k, e in aws_eks_access_entry.admin : k => e.principal_arn }
}
