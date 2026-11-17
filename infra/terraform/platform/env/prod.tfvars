environment                 = "prod"
state_bucket                = "paved-road-tfstate-123456789012-us-west-2"
state_kms_key_arn           = "arn:aws:kms:us-west-2:123456789012:key/00000000-0000-0000-0000-000000000000"
create_github_oidc_provider = false # dev already created it in this account
single_nat_gateway          = false
vpc_cidr                    = "10.41.0.0/16"
admin_role_arns             = ["arn:aws:iam::123456789012:role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_PlatformAdmin_0123456789abcdef"]

node_groups = {
  general = {
    instance_types = ["m7g.xlarge", "m6g.xlarge"]
    ami_type       = "AL2023_ARM_64_STANDARD"
    min_size       = 3
    max_size       = 12
    desired_size   = 3
  }

  ci-runners = {
    instance_types = ["m7i.xlarge", "m6i.xlarge", "m7a.xlarge", "m6a.xlarge", "c7i.2xlarge"]
    capacity_type  = "SPOT"
    min_size       = 1
    max_size       = 20
    desired_size   = 1
    disk_size_gb   = 100
    labels         = { "paved.dev/workload" = "ci" }
    taints         = [{ key = "paved.dev/ci", value = "true", effect = "NO_SCHEDULE" }]
  }
}
