variable "region" {
  description = "AWS region for the state bucket."
  type        = string
  default     = "us-west-2"
}

variable "name_prefix" {
  description = "Prefix for the bucket and KMS alias."
  type        = string
  default     = "paved-road"

  validation {
    condition     = can(regex("^[a-z0-9-]{3,24}$", var.name_prefix))
    error_message = "name_prefix must be 3 to 24 lowercase letters, digits or hyphens."
  }
}
