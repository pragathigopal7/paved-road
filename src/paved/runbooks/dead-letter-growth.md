---
title: Dead letter topic is growing
alerts: [DeadLetterMessagesIncreasing]
tags: [kafka, dead letter, dlq, poison message, schema, replay]
severity: ticket
---
# Dead letter topic is growing

Messages land in `{{topic}}.dlq` when a handler raises `PermanentError` or exhausts its
retries. A trickle is normal; a steady rate means something upstream or downstream
changed.

## 1. Why are they failing?

```promql
sum by (reason) (rate(dead_letter_messages_total{namespace="{{namespace}}", topic="{{topic}}"}[15m]))
```

```bash
kcat -b $BOOTSTRAP -t {{topic}}.dlq -C -o -10 -e -f 'reason=%h\n%s\n\n'
```

Every dead letter carries `dlq.reason`, `dlq.error`, `dlq.source` (topic/partition/offset)
and the original `traceparent`, so you can jump from a parked message to the full trace
of the request that produced it.

## 2. Decide

* Same error for every message, starting at a deploy time: roll back whichever side
  deployed (producer or consumer). See the error budget runbook if the SLO is burning.
* A handful of distinct, genuinely bad messages: leave them parked, file a bug with the
  producer, and acknowledge the alert.

## 3. Replay

After the fix, replay from the DLQ back to `{{topic}}` at a limited rate and watch
`messages_processed_total{outcome="dead_letter"}` stay flat while it drains.
