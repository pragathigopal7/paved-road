output "state_bucket" {
  description = "Put this in infra/terraform/platform/env/<env>.backend.hcl."
  value       = aws_s3_bucket.state.bucket
}

output "state_kms_key_arn" {
  description = "KMS key that encrypts state objects."
  value       = aws_kms_key.state.arn
}
