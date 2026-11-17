---
title: Terraform plan or apply is stuck on a state lock
alerts: []
tags: [terraform, state, lock, s3, ci, deploy, drift]
severity: ticket
---
# Terraform plan or apply is stuck on a state lock

State lives in S3 with native lock files (`use_lockfile = true`), one key per
environment. The deploy workflow uses a GitHub `concurrency` group per environment, so
two pipeline runs should never contend. A held lock almost always means a run was
cancelled or a runner pod died mid apply.

## 1. Read the lock before touching it

The error prints the lock ID, who holds it, and when it was taken.

```bash
aws s3 cp s3://$STATE_BUCKET/platform/{{environment}}/terraform.tfstate.tflock - | jq .
```

* `Who` is a CI runner and the matching workflow run is still in progress: wait.
* The run is cancelled or failed, or the runner pod is gone: the lock is stale.
* `Who` is a person: ask them. Never break a lock someone is actively applying with.

## 2. Release a stale lock

```bash
cd infra/terraform/platform
terraform init -backend-config=env/{{environment}}.backend.hcl
terraform force-unlock <LOCK_ID>
```

## 3. Check what the interrupted apply did

An apply killed halfway can leave resources created but not recorded, or recorded but
half configured.

```bash
terraform plan -var-file=env/{{environment}}.tfvars -detailed-exitcode
```

Read the plan carefully. A "create" for something that exists in AWS means it was
created but not saved: import it (`terraform import`) rather than letting Terraform
make a duplicate. The state bucket is versioned, so a corrupted state can be restored
from the previous object version.

## Afterwards

If runner pods are dying during applies, the `ci-runners` spot node group is too
aggressive for deploys. Deploy jobs can use the on demand `system` pool by targeting
a runner scale set with a matching node selector.
