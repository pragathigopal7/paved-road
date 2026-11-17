---
title: Pod is crash looping
alerts: [PodCrashLooping]
tags: [kubernetes, pod, crashloop, restart, oom, oomkilled, probe]
severity: ticket
---
# Pod is crash looping

## 1. Why did the last container exit?

```bash
kubectl -n {{namespace}} describe pod {{pod}} | sed -n '/Last State/,/Ready/p'
kubectl -n {{namespace}} logs {{pod}} -c {{container}} --previous --tail=100
kubectl -n {{namespace}} get events --field-selector involvedObject.name={{pod}} --sort-by=.lastTimestamp
```

| Last state | Likely cause | Next step |
| --- | --- | --- |
| `OOMKilled` (exit 137) | Memory limit too low or a leak | Step 2 |
| Exit 1 with a stack trace | App bug or bad config | Check the latest rollout, step 3 |
| Killed after `Liveness probe failed` | Event loop blocked or probe too strict | Step 4 |
| `CreateContainerConfigError` | Missing Secret or ConfigMap | `kubectl describe` names it |

## 2. OOMKilled

```bash
paved rightsize --prometheus $PROM_URL --namespace {{namespace}} --deployment <deployment>
```

If peak working set sits at the limit and grows with uptime, it is a leak: raise the
limit as a stopgap and open a bug. If it is flat, apply the recommended request and limit.

## 3. Bad rollout

```bash
kubectl -n {{namespace}} rollout history deploy/<deployment>
kubectl -n {{namespace}} rollout undo deploy/<deployment>
```

Roll back first, debug second. `maxUnavailable: 0` in the paved road template means old
pods keep serving while new ones crash, so a rollback is usually invisible to users.

## 4. Liveness probe kills a healthy pod

The template's `/healthz` fails only when the poll loop has not run for 60 seconds. If a
handler legitimately takes longer, fix the handler (time out the downstream call). Do not
just loosen the probe; a stuck consumer that is never restarted is worse.
