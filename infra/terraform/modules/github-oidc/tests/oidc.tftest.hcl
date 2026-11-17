# The policy documents are rendered locally by the real provider (no API calls), so
# these tests check the actual JSON that IAM would receive.

provider "aws" {
  region                      = "us-west-2"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

variables {
  role_name         = "paved-test-ci-plan"
  oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
  subjects          = ["repo:pragathigopal7/paved-road:pull_request"]
  state_bucket      = "paved-road-tfstate"
  state_key_prefix  = "platform/dev/"
  state_kms_key_arn = "arn:aws:kms:us-west-2:123456789012:key/00000000-0000-0000-0000-000000000000"
}

run "trust_is_pinned_to_repo_and_audience" {
  command = plan

  assert {
    condition     = jsondecode(data.aws_iam_policy_document.trust.json).Statement[0].Condition.StringLike["token.actions.githubusercontent.com:sub"] == "repo:pragathigopal7/paved-road:pull_request"
    error_message = "Trust must be limited to the configured subject."
  }

  assert {
    condition     = jsondecode(data.aws_iam_policy_document.trust.json).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    error_message = "Trust must require the STS audience."
  }

  assert {
    condition     = aws_iam_role.this.max_session_duration == 3600
    error_message = "CI sessions should last at most an hour."
  }
}

run "plan_role_can_lock_but_not_write_state" {
  command = plan

  assert {
    condition     = !contains([for s in jsondecode(data.aws_iam_policy_document.state.json).Statement : s.Sid], "WriteState")
    error_message = "A plan role must not be able to overwrite state."
  }

  assert {
    condition     = one([for s in jsondecode(data.aws_iam_policy_document.state.json).Statement : s.Resource if s.Sid == "LockFiles"]) == "arn:aws:s3:::paved-road-tfstate/platform/dev/*.tflock"
    error_message = "Lock file writes should be limited to .tflock objects in this environment."
  }
}

run "apply_role_can_write_state" {
  command = plan

  variables {
    state_write = true
    subjects    = ["repo:pragathigopal7/paved-road:environment:prod"]
  }

  assert {
    condition     = contains([for s in jsondecode(data.aws_iam_policy_document.state.json).Statement : s.Sid], "WriteState")
    error_message = "The apply role needs to write state."
  }
}

run "rejects_wildcard_repositories" {
  command = plan

  variables {
    subjects = ["repo:pragathigopal7/*:ref:refs/heads/main"]
  }

  expect_failures = [var.subjects]
}
