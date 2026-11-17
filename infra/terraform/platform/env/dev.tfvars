environment        = "dev"
state_bucket       = "paved-road-tfstate-123456789012-us-west-2"
state_kms_key_arn  = "arn:aws:kms:us-west-2:123456789012:key/00000000-0000-0000-0000-000000000000"
single_nat_gateway = true
vpc_cidr           = "10.40.0.0/16"

node_groups = {
  # Platform add-ons and services. Graviton: about 20% cheaper per vCPU than x86.
  general = {
    instance_types = ["m7g.large", "m6g.large"]
    ami_type       = "AL2023_ARM_64_STANDARD"
    min_size       = 2
    max_size       = 4
    desired_size   = 2
  }

  # Ephemeral GitHub Actions runners. Spot with a diversified instance list, scaled
  # from zero, and tainted so only runner pods land here.
  ci-runners = {
    instance_types = ["m7i.xlarge", "m6i.xlarge", "m7a.xlarge", "m6a.xlarge", "c7i.2xlarge"]
    capacity_type  = "SPOT"
    min_size       = 0
    max_size       = 6
    desired_size   = 0
    disk_size_gb   = 100
    labels         = { "paved.dev/workload" = "ci" }
    taints         = [{ key = "paved.dev/ci", value = "true", effect = "NO_SCHEDULE" }]
  }
}
