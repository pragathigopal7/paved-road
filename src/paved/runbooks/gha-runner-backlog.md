---
title: GitHub Actions jobs are queued on self hosted runners
alerts: [GitHubRunnerJobsQueued, GitHubRunnerStartupSlow]
tags: [github actions, ci, runners, arc, self hosted, queue, scale set]
severity: ticket
---
# GitHub Actions jobs are queued on self hosted runners

Self hosted runners run on EKS through Actions Runner Controller (ARC) scale sets, one
per team, on the `ci-runners` spot node group. Each job gets a fresh ephemeral pod.

## 1. Where is the bottleneck?

```promql
gha_assigned_jobs{name="{{name}}"} - gha_running_jobs{name="{{name}}"}   # waiting for a runner
gha_desired_runners{name="{{name}}"}                                       # what ARC wants
gha_registered_runners{name="{{name}}"}                                    # what GitHub sees
```

* `desired` is at `maxRunners`: the scale set is capped. Raise `max_runners` for the
  team in `infra/pulumi/teams.yaml` if the load is real, or find the workflow that fans
  out (a matrix that grew).
* `desired` above `registered`: pods are not starting. Step 2.
* Everything looks fine but jobs wait: the workflow `runs-on` label does not match the
  scale set name `{{name}}`, so GitHub never routes the job here.

## 2. Runner pods not starting

```bash
kubectl -n {{namespace}} get pods -l actions.github.com/scale-set-name={{name}}
kubectl -n {{namespace}} get events --sort-by=.lastTimestamp | tail -20
kubectl -n arc-systems logs deploy/arc-gha-rs-controller --tail=100
```

* `Pending` with `Insufficient cpu`: the node group is at max size or spot capacity is
  short. Check the Cluster Autoscaler or Karpenter logs, and the node group's
  `max_size` and instance types in Terraform.
* `ImagePullBackOff`: the runner image moved or ECR auth broke.
* Listener pod restarting with 401: the GitHub App private key in the
  `arc-github-app` Secret was rotated or revoked.

## 3. Mitigate

Teams can fall back to GitHub hosted runners by changing `runs-on` to
`ubuntu-latest` for jobs that do not need VPC access. Deploy jobs must stay on self
hosted runners because the deploy role trusts only that path.

## Afterwards

Check `gha_job_startup_duration_seconds` p95 for the week. Above 60s regularly means
`minRunners` should be raised during working hours.
