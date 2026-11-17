# An IAM role GitHub Actions can assume through OIDC. No access keys exist anywhere:
# the workflow trades its short lived GitHub token for one hour AWS credentials, and
# the trust policy pins exactly which repository and ref or environment may do so.

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = var.subjects
    }
  }
}

resource "aws_iam_role" "this" {
  name                 = var.role_name
  description          = var.description
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  permissions_boundary = var.permissions_boundary_arn
}

resource "aws_iam_role_policy_attachment" "managed" {
  for_each = toset(var.managed_policy_arns)

  role       = aws_iam_role.this.name
  policy_arn = each.value
}

# Terraform state access. Read only roles can still take the lock (plan holds it),
# but can only write lock files, never the state object itself.
data "aws_iam_policy_document" "state" {
  statement {
    sid       = "ListStatePrefix"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.state_bucket}"]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.state_key_prefix}*"]
    }
  }

  statement {
    sid       = "ReadState"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.state_bucket}/${var.state_key_prefix}*"]
  }

  statement {
    sid       = "LockFiles"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["arn:aws:s3:::${var.state_bucket}/${var.state_key_prefix}*.tflock"]
  }

  dynamic "statement" {
    for_each = var.state_write ? [1] : []

    content {
      sid       = "WriteState"
      actions   = ["s3:PutObject"]
      resources = ["arn:aws:s3:::${var.state_bucket}/${var.state_key_prefix}*"]
    }
  }

  statement {
    sid       = "StateKey"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [var.state_kms_key_arn]
  }
}

resource "aws_iam_role_policy" "state" {
  name   = "terraform-state"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.state.json
}
