# Design decisions

Short records of the choices that shape this platform, what they cost, and when I would
revisit them.

## 1. Terraform for the foundation, Pulumi for the platform layer

**Decision.** Terraform owns the VPC, EKS and IAM trust. Pulumi (Python) owns everything
inside the cluster plus per team AWS resources. The hand off is `terraform output -json`.

**Why.** The foundation changes rarely, is expensive to get wrong, and benefits from
Terraform's plan review culture and module ecosystem. Team tenancy is the opposite: it
is driven by data (teams.yaml), loops over teams and services, and is easiest to keep
correct with typed config, validation and real unit tests, which a general purpose
language gives you.

**Cost.** Two tools and two state files. The boundary has to stay clean: Pulumi never
modifies anything Terraform created.

**Revisit** if the team only knows one of the two; either can do both jobs.

## 2. S3 native state locking

**Decision.** `use_lockfile = true` (Terraform 1.10+), no DynamoDB table.

**Why.** One less resource to provision, pay for and grant IAM on. The plan role can be
limited to writing `*.tflock` objects only, which is a tighter permission than DynamoDB
table access.

## 3. GitHub OIDC with environment pinned trust

**Decision.** No AWS keys in GitHub. The apply role trusts only
`repo:<org>/<repo>:environment:<env>`, and the environment requires reviewers.

**Why.** Approval is enforced by AWS, not by the workflow file. Someone who edits the
workflow to skip review still cannot assume the role, because GitHub will not issue a
token with that environment claim without the approval.

## 4. Self hosted runners: ARC scale sets, kubernetes mode, spot

**Decision.** Actions Runner Controller scale sets per team, ephemeral pods, kubernetes
container mode, on a tainted spot node group that scales from zero.

**Why.** Deploys must reach a private EKS endpoint, so they run inside the VPC.
Ephemeral runners mean no state leaks between jobs. Kubernetes mode runs job containers
as sibling pods, avoiding privileged Docker in Docker. Spot cuts CI compute cost sharply,
and a reclaimed runner just reruns the job.

**Cost.** Kubernetes mode needs a per job volume and does not support every Docker
feature (for example `docker build` inside the job; use BuildKit or a remote builder).
Spot reclaims can kill long applies, so the platform's own `paved-platform` scale set
runs on the on demand general pool instead; the state lock runbook covers the cases that
still leave a lock behind. That scale set also creates a bootstrap loop (the runner that
deploys the platform is deployed by the platform), so the first `pulumi up` per
environment is run by hand.

## 5. No CPU limits by default

**Decision.** The policy requires CPU and memory requests and a memory limit, but not a
CPU limit. `paved rightsize` flags existing CPU limits that throttle.

**Why.** CPU is compressible. A CPU limit causes CFS throttling even when the node has
idle cores, which shows up as tail latency that is hard to trace. Memory is not
compressible, so it gets a limit equal to the request for predictable OOM behavior.

## 6. EKS Pod Identity over IRSA

**Decision.** All in cluster AWS access (EBS CSI, Prometheus remote write, collector,
Cluster Autoscaler) uses Pod Identity associations.

**Why.** No per role OIDC trust policies to maintain, and roles can be reused across
clusters. IRSA remains an option for tools that do not support Pod Identity yet.

## 7. Scale consumers on lag, not CPU

**Decision.** The service template ships a KEDA ScaledObject on Kafka lag, capped at the
partition count.

**Why.** A consumer waiting on a slow dependency is idle on CPU while lag grows, so CPU
based autoscaling scales in exactly the wrong direction.

## 8. Alerts are tested code

**Decision.** Alert rules have promtool unit tests, and a Python test keeps alerts and
runbooks linked in both directions.

**Why.** An alert that never fires, or fires without a runbook, is worse than no alert:
it trains people to ignore pages. Lag alerts require lag to be both high and growing, so
a backlog that is draining does not wake anyone.

## 9. Policy in CI first, admission control next

**Decision.** `paved check` runs in CI with PR annotations and SARIF. The namespace level
restricted Pod Security Standard is the only admission control today.

**Why.** Feedback in the pull request is cheaper than a rejected deploy. The same rules
should move into admission (Kyverno or ValidatingAdmissionPolicy with CEL) so manifests
applied outside CI are covered too. That is the next step.

## What I would add next

* Admission enforcement of the paved check rules (ValidatingAdmissionPolicy).
* Karpenter in place of Cluster Autoscaler for faster, bin packed spot capacity.
* External Secrets Operator to sync the ARC GitHub App key from Secrets Manager.
* A second collector tier with the load balancing exporter so tail sampling can keep all
  error and slow traces.
* Cost allocation dashboards from the `team` label (already on every pod and resource).
