variable "environment" {
  description = "Environment name; also the GitHub environment allowed to apply."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-west-2"
}

variable "cost_center" {
  description = "Cost allocation tag applied to every resource."
  type        = string
  default     = "platform"
}

variable "github_repository" {
  description = "owner/repo whose workflows may assume the CI roles."
  type        = string
  default     = "pragathigopal7/paved-road"
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC provider (only one can exist per account)."
  type        = bool
  default     = true
}

variable "state_bucket" {
  description = "Bucket created by infra/terraform/bootstrap."
  type        = string
}

variable "state_kms_key_arn" {
  description = "KMS key created by infra/terraform/bootstrap."
  type        = string
}

variable "apply_policy_arns" {
  description = "Policies for the apply role. Pair broad policies with a permissions boundary."
  type        = list(string)
  default     = ["arn:aws:iam::aws:policy/AdministratorAccess"]
}

variable "apply_permissions_boundary_arn" {
  description = "Permissions boundary for the apply role."
  type        = string
  default     = null
}

variable "vpc_cidr" {
  description = "VPC CIDR."
  type        = string
  default     = "10.40.0.0/16"
}

variable "single_nat_gateway" {
  description = "One NAT gateway for the whole VPC (dev) instead of one per AZ (prod)."
  type        = bool
  default     = false
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version."
  type        = string
  default     = "1.33"
}

variable "api_allowed_cidrs" {
  description = "If non empty, expose the API endpoint publicly to only these CIDRs."
  type        = list(string)
  default     = []
}

variable "admin_role_arns" {
  description = "Human admin roles (for example the SSO platform admin role)."
  type        = list(string)
  default     = []
}

variable "node_groups" {
  description = "Managed node groups; see modules/eks for the schema."
  type = map(object({
    instance_types = list(string)
    capacity_type  = optional(string, "ON_DEMAND")
    ami_type       = optional(string, "AL2023_x86_64_STANDARD")
    min_size       = number
    max_size       = number
    desired_size   = number
    disk_size_gb   = optional(number, 50)
    labels         = optional(map(string), {})
    taints = optional(list(object({
      key    = string
      value  = optional(string)
      effect = string
    })), [])
  }))
}
