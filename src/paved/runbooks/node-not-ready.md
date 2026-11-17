---
title: EKS node is NotReady
alerts: [NodeNotReady]
tags: [kubernetes, eks, node, notready, kubelet, ec2, spot, capacity]
severity: page
---
# EKS node is NotReady

## 1. Look at the node

```bash
kubectl get node {{node}} -o wide
kubectl describe node {{node}} | sed -n '/Conditions/,/Addresses/p'
kubectl get pods -A --field-selector spec.nodeName={{node}} -o wide
```

Note the node group (`eks.amazonaws.com/nodegroup` label) and capacity type
(`eks.amazonaws.com/capacityType`).

## 2. Ask EC2

```bash
ID=$(kubectl get node {{node}} -o jsonpath='{.spec.providerID}' | awk -F/ '{print $NF}')
aws ec2 describe-instance-status --instance-ids "$ID" --include-all-instances
aws ec2 get-console-output --instance-id "$ID" --latest --output text | tail -50
```

* Instance `stopping` / `terminated` on a **SPOT** node: a reclaim. The `ci-runners`
  node group is spot on purpose; ARC reschedules runners. Nothing to do unless many
  nodes go at once (check the spot placement score for the region and widen the
  instance type list in `infra/terraform/platform/env/<env>.tfvars`).
* Instance status check failed: hardware. Cordon and let the node group replace it:
  `kubectl cordon {{node}} && kubectl drain {{node}} --ignore-daemonsets --delete-emptydir-data`
  then `aws ec2 terminate-instances --instance-ids "$ID"`.
* Instance fine, kubelet not reporting: usually disk or memory pressure, or the VPC
  CNI running out of IPs. Check `DiskPressure` and `MemoryPressure` conditions and
  `kubectl -n kube-system logs ds/aws-node -c aws-node --tail=50`.

## 3. Is it more than one node?

```promql
count by (eks_amazonaws_com_nodegroup) (kube_node_status_condition{condition="Ready", status="true"} == 0)
```

Several nodes in one AZ at once points at the AZ or the subnet (IP exhaustion), not the
nodes. Check free IPs per private subnet in the VPC console or with
`aws ec2 describe-subnets --filters Name=tag:kubernetes.io/role/internal-elb,Values=1`.

## Afterwards

PodDisruptionBudgets and zone topology spread in the service template should have kept
services up. If a service went down with one node, it is missing one of them; `paved
check` flags both.
