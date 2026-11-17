# Offline tests: the AWS provider is mocked, so these run in CI without credentials.

mock_provider "aws" {
  mock_data "aws_eks_addon_version" {
    defaults = {
      version = "v1.0.0-eksbuild.1"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{}"
    }
  }

  # Mocked computed values must still look like ARNs to pass provider validation.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/mock"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-west-2:123456789012:log-group:mock"
    }
  }

  mock_resource "aws_kms_key" {
    defaults = {
      arn = "arn:aws:kms:us-west-2:123456789012:key/00000000-0000-0000-0000-000000000000"
    }
  }

  mock_resource "aws_launch_template" {
    defaults = {
      id = "lt-0123456789abcdef0"
    }
  }

  mock_resource "aws_eks_cluster" {
    defaults = {
      certificate_authority = [{ data = "Y2EtYnVuZGxl" }]
    }
  }
}

variables {
  cluster_name       = "paved-test"
  kubernetes_version = "1.33"
  subnet_ids         = ["subnet-aaa", "subnet-bbb", "subnet-ccc"]
  admin_role_arns    = ["arn:aws:iam::123456789012:role/platform-admin"]

  node_groups = {
    general = {
      instance_types = ["m7g.large"]
      ami_type       = "AL2023_ARM_64_STANDARD"
      min_size       = 2
      max_size       = 4
      desired_size   = 2
    }
    ci-runners = {
      instance_types = ["m7i.xlarge", "m6i.xlarge"]
      capacity_type  = "SPOT"
      min_size       = 0
      max_size       = 6
      desired_size   = 0
      taints         = [{ key = "paved.dev/ci", value = "true", effect = "NO_SCHEDULE" }]
    }
  }
}

run "control_plane_is_private_and_encrypted" {
  assert {
    condition     = aws_eks_cluster.this.vpc_config[0].endpoint_public_access == false
    error_message = "The API endpoint must be private by default."
  }

  assert {
    condition     = contains(aws_eks_cluster.this.encryption_config[0].resources, "secrets")
    error_message = "Kubernetes Secrets must use KMS envelope encryption."
  }

  assert {
    condition     = aws_kms_key.eks.enable_key_rotation
    error_message = "The secrets KMS key must rotate."
  }

  assert {
    condition     = aws_eks_cluster.this.access_config[0].authentication_mode == "API"
    error_message = "Use access entries, not the aws-auth ConfigMap."
  }

  assert {
    condition     = length(aws_eks_cluster.this.enabled_cluster_log_types) == 5
    error_message = "All control plane log types should be enabled (audit included)."
  }

  assert {
    condition     = aws_eks_cluster.this.upgrade_policy[0].support_type == "STANDARD"
    error_message = "Extended support should be opted out of to avoid the surcharge."
  }
}

run "nodes_block_pod_access_to_instance_credentials" {
  assert {
    condition = alltrue([
      for lt in aws_launch_template.node :
      lt.metadata_options[0].http_tokens == "required" && lt.metadata_options[0].http_put_response_hop_limit == 1
    ])
    error_message = "IMDSv2 with hop limit 1 is required so pods cannot use node credentials."
  }

  assert {
    condition     = alltrue([for lt in aws_launch_template.node : lt.block_device_mappings[0].ebs[0].encrypted == "true"])
    error_message = "Node root volumes must be encrypted."
  }
}

run "ci_pool_is_spot_and_tainted" {
  assert {
    condition     = aws_eks_node_group.this["ci-runners"].capacity_type == "SPOT"
    error_message = "CI runners should run on spot."
  }

  assert {
    condition     = one(aws_eks_node_group.this["ci-runners"].taint).key == "paved.dev/ci"
    error_message = "CI nodes must be tainted so services do not land there."
  }

  assert {
    condition     = aws_eks_node_group.this["general"].labels["paved.dev/pool"] == "general"
    error_message = "Every node should carry its pool label."
  }
}

run "network_policy_and_prefix_delegation_enabled" {
  assert {
    condition     = jsondecode(aws_eks_addon.before_nodes["vpc-cni"].configuration_values).enableNetworkPolicy == "true"
    error_message = "VPC CNI must enforce NetworkPolicy; services rely on it."
  }

  assert {
    condition     = jsondecode(aws_eks_addon.before_nodes["vpc-cni"].configuration_values).env.ENABLE_PREFIX_DELEGATION == "true"
    error_message = "Prefix delegation should be on for pod density."
  }

  assert {
    condition     = one(aws_eks_addon.ebs_csi.pod_identity_association).service_account == "ebs-csi-controller-sa"
    error_message = "EBS CSI should authenticate with Pod Identity."
  }
}

run "rejects_api_open_to_the_internet" {
  command = plan

  variables {
    endpoint_public_access = true
    public_access_cidrs    = ["0.0.0.0/0"]
  }

  expect_failures = [var.public_access_cidrs]
}

run "rejects_inconsistent_scaling" {
  command = plan

  variables {
    node_groups = {
      bad = {
        instance_types = ["m7g.large"]
        min_size       = 3
        max_size       = 2
        desired_size   = 2
      }
    }
  }

  expect_failures = [var.node_groups]
}
