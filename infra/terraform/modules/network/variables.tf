variable "name" {
  description = "Name used for the VPC and its resources."
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster that will use these subnets (for discovery tags)."
  type        = string
}

variable "cidr" {
  description = "VPC CIDR. A /16 gives three /19 private subnets for nodes and pods."
  type        = string
  default     = "10.40.0.0/16"

  validation {
    condition     = can(cidrhost(var.cidr, 0)) && tonumber(split("/", var.cidr)[1]) <= 16
    error_message = "cidr must be a valid IPv4 CIDR of /16 or larger."
  }
}

variable "az_count" {
  description = "Number of availability zones to spread across."
  type        = number
  default     = 3

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 4
    error_message = "az_count must be between 2 and 4."
  }
}

variable "single_nat_gateway" {
  description = "Use one NAT gateway instead of one per AZ (cheaper, not AZ fault tolerant)."
  type        = bool
  default     = false
}

variable "flow_log_retention_days" {
  description = "CloudWatch retention for VPC flow logs."
  type        = number
  default     = 30
}
