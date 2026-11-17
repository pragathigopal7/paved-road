variable "role_name" {
  description = "IAM role name."
  type        = string
}

variable "description" {
  description = "IAM role description."
  type        = string
  default     = "Assumed by GitHub Actions through OIDC"
}

variable "oidc_provider_arn" {
  description = "ARN of the token.actions.githubusercontent.com OIDC provider."
  type        = string
}

variable "subjects" {
  description = <<-EOT
    Allowed `sub` claims, for example:
      repo:org/repo:pull_request
      repo:org/repo:ref:refs/heads/main
      repo:org/repo:environment:prod
  EOT
  type        = list(string)

  validation {
    condition     = length(var.subjects) > 0 && alltrue([for s in var.subjects : can(regex("^repo:[^*]+/[^*]+:.+$", s))])
    error_message = "Each subject must name an exact org/repo (no wildcards in the repository part)."
  }
}

variable "managed_policy_arns" {
  description = "AWS managed or customer managed policies to attach."
  type        = list(string)
  default     = []
}

variable "permissions_boundary_arn" {
  description = "Optional permissions boundary for the role."
  type        = string
  default     = null
}

variable "state_bucket" {
  description = "Terraform state bucket name."
  type        = string
}

variable "state_key_prefix" {
  description = "State key prefix this role may use, for example platform/prod/."
  type        = string
}

variable "state_kms_key_arn" {
  description = "KMS key that encrypts the state bucket."
  type        = string
}

variable "state_write" {
  description = "Allow writing the state object (apply roles only)."
  type        = bool
  default     = false
}
