variable "cluster_name" {
  description = "EKS cluster name."
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes minor version, for example 1.33."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the control plane ENIs and nodes."
  type        = list(string)
}

variable "endpoint_public_access" {
  description = "Expose the API server publicly (restricted by public_access_cidrs)."
  type        = bool
  default     = false
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach a public API endpoint."
  type        = list(string)
  default     = []

  validation {
    condition     = !contains(var.public_access_cidrs, "0.0.0.0/0")
    error_message = "Do not open the API server to 0.0.0.0/0; list office or VPN CIDRs."
  }
}

variable "admin_role_arns" {
  description = "IAM roles granted cluster admin through EKS access entries."
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "CloudWatch retention for control plane logs."
  type        = number
  default     = 90
}

variable "node_groups" {
  description = "Managed node groups keyed by pool name."
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

  validation {
    condition = alltrue([
      for g in values(var.node_groups) : contains(["ON_DEMAND", "SPOT"], g.capacity_type)
    ])
    error_message = "capacity_type must be ON_DEMAND or SPOT."
  }

  validation {
    condition = alltrue([
      for g in values(var.node_groups) : g.min_size <= g.desired_size && g.desired_size <= g.max_size
    ])
    error_message = "Each node group needs min_size <= desired_size <= max_size."
  }
}
