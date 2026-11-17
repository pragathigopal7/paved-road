---
title: Kafka consumer lag is growing or the consumer stalled
alerts: [KafkaConsumerLagHigh, KafkaConsumerStalled]
tags: [kafka, consumer, lag, backlog, throughput, keda, rebalance]
severity: page
---
# Kafka consumer lag is growing or the consumer stalled

**Impact:** events on `{{topic}}` are processed late. Anything downstream of
`{{consumergroup}}` (projections, notifications, analytics) is stale by the lag divided
by the consume rate.

## 1. Is it one partition or all of them?

```bash
kubectl -n {{namespace}} exec deploy/{{consumergroup}} -- \
  python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8080/metrics').read().decode())" \
  | grep kafka_consumer_lag
```

Or in Prometheus:

```promql
sum by (partition) (kafka_consumer_lag{namespace="{{namespace}}", consumergroup="{{consumergroup}}"})
```

* **One partition:** a hot key or a poison message. Go to step 3.
* **All partitions, consume rate near zero:** the consumer is stuck. Go to step 2.
* **All partitions, consume rate normal but below produce rate:** capacity. Go to step 4.

## 2. Consumer stuck (KafkaConsumerStalled)

```bash
kubectl -n {{namespace}} get pods -l app.kubernetes.io/name={{consumergroup}} -o wide
kubectl -n {{namespace}} logs deploy/{{consumergroup}} --since=15m | grep -E '"level": "(error|warning)"' | tail -50
```

* Pods restarting: follow [pod-crashloop](pod-crashloop.md).
* `consumer error` logs with broker or auth errors: check broker health and the
  NetworkPolicy egress ports (9092 to 9098) before touching the app.
* Logs show `handler failed, retrying` on the same offset: a downstream dependency is
  down. Retries are bounded, so messages will dead letter; watch
  `dead_letter_messages_total` and decide whether to pause instead (step 5).
* Repeated `partitions revoked` / `partitions assigned`: rebalance storm. Usually a
  handler slower than `max.poll.interval.ms` (default 5 minutes). Find the slow call in
  traces (`service.name={{consumergroup}}`, longest `process {{topic}}` spans).

## 3. One hot partition

Find the offset the partition is sitting on, then the trace for that offset
(`messaging.kafka.offset` span attribute). If one message fails forever and is not
being dead lettered, the handler is catching too broadly; raise `PermanentError`.

## 4. Not enough capacity

```bash
kubectl -n {{namespace}} get scaledobject {{consumergroup}} -o yaml | grep -A3 -E 'maxReplicaCount|conditions'
kubectl -n {{namespace}} get hpa
```

* KEDA at `maxReplicaCount`, and replicas equal the partition count: more pods will not
  help. Increase partitions (needs the topic owner) or make the handler faster.
* KEDA not scaling: check `kubectl -n keda logs deploy/keda-operator` for auth errors
  to the brokers.

## 5. Mitigate

* Scale manually as a stopgap: `kubectl -n {{namespace}} scale deploy/{{consumergroup}} --replicas=<n>`
  (KEDA will take over again on its next loop; pause it with the
  `autoscaling.keda.sh/paused-replicas` annotation if you need it to stick).
* If a downstream outage is turning every message into a dead letter, scale to zero to
  stop the bleed, fix the dependency, then scale back up. Offsets are committed only
  after handling, so nothing is lost.

## Afterwards

Record the peak lag and time to drain in the incident doc. If lag alerts are noisy,
tune `lagThreshold` per service rather than raising the global alert threshold.
