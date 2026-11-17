# Offline tests: the AWS provider is mocked, so these run in CI without credentials.

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-west-2a", "us-west-2b", "us-west-2c", "us-west-2d"]
    }
  }

  mock_data "aws_region" {
    defaults = {
      region = "us-west-2"
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
}

variables {
  name         = "paved-test"
  cluster_name = "paved-test"
}

run "dev_shares_one_nat_gateway" {
  variables {
    single_nat_gateway = true
  }

  assert {
    condition     = length(aws_nat_gateway.this) == 1
    error_message = "single_nat_gateway should create exactly one NAT gateway."
  }

  assert {
    condition     = length(aws_route.private_nat) == 3
    error_message = "Every private route table still needs a default route."
  }
}

run "prod_has_a_nat_gateway_per_az" {
  variables {
    single_nat_gateway = false
  }

  assert {
    condition     = length(aws_nat_gateway.this) == 3
    error_message = "Each AZ needs its own NAT gateway to survive an AZ outage."
  }

  assert {
    condition     = length(aws_eip.nat) == 3
    error_message = "Each NAT gateway needs its own Elastic IP."
  }
}

run "subnet_layout" {
  assert {
    condition     = [for s in aws_subnet.private : s.cidr_block] == ["10.40.32.0/19", "10.40.64.0/19", "10.40.96.0/19"]
    error_message = "Private subnets should be /19s after the public range."
  }

  assert {
    condition     = [for s in aws_subnet.public : s.cidr_block] == ["10.40.0.0/24", "10.40.1.0/24", "10.40.2.0/24"]
    error_message = "Public subnets should be small /24s."
  }

  assert {
    condition     = alltrue([for s in aws_subnet.public : s.map_public_ip_on_launch == false])
    error_message = "Nothing should get a public IP implicitly."
  }

  assert {
    condition     = alltrue([for s in aws_subnet.private : s.tags["kubernetes.io/role/internal-elb"] == "1"])
    error_message = "Private subnets need the internal-elb tag for the AWS Load Balancer Controller."
  }

  assert {
    condition     = aws_flow_log.this.traffic_type == "ALL"
    error_message = "VPC flow logs should capture all traffic."
  }
}

run "rejects_a_vpc_too_small_for_pods" {
  command = plan

  variables {
    cidr = "10.40.0.0/20"
  }

  expect_failures = [var.cidr]
}
