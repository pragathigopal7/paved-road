terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Partial configuration: bucket, key and region come from env/<env>.backend.hcl so
  # every environment has its own state object and lock.
  backend "s3" {}
}
