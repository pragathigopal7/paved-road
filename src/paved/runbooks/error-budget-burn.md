---
title: Consumer error budget is burning
alerts: [ConsumerErrorBudgetBurnFast, ConsumerErrorBudgetBurnSlow]
tags: [slo, error budget, burn rate, errors, dead letter, reliability]
severity: page
---
# Consumer error budget is burning

The SLO is 99.9% of messages on each topic handled without being dead lettered, over 30
days. The fast burn alert fires when the 1h and 5m error ratios both exceed 14.4 times
the budget (2% of the monthly budget gone in an hour). The slow burn alert uses 6h and
30m windows at 6 times.

## 1. Confirm and scope

```promql
sum by (topic, outcome) (rate(messages_processed_total{namespace="{{namespace}}"}[5m]))
sum by (reason) (rate(dead_letter_messages_total{namespace="{{namespace}}", topic="{{topic}}"}[5m]))
```

* `reason="permanent"`: the input changed. A producer shipped a schema change or bad
  data. Go to step 2.
* `reason="retries_exhausted"`: a dependency is failing. Go to step 3.

## 2. Bad input

Sample dead letters; the headers say why each one was parked:

```bash
kcat -b $BOOTSTRAP -t {{topic}}.dlq -C -o -5 -e -f '%h\n%s\n\n'
```

Check `dlq.error` and `dlq.source`. Find the producing service from the `traceparent`
header in your tracing backend, then page its owner. Do not change the consumer to
accept the bad shape without agreeing on the contract.

## 3. Failing dependency

Open a trace for a failed message (`app.outcome = dead_letter`) and find the failing
child span. Check that dependency's dashboard and status page. If it will be down for a
while, stop consuming (scale to zero) so messages wait on the topic instead of filling
the DLQ.

## 4. Recover

After the fix, replay the DLQ into the main topic with a rate limit, and watch the error
ratio while it drains. Replays must be idempotent, which the handler contract requires.

## Afterwards

Burned budget is shown on the service SLO dashboard. More than half the budget spent in
a month means reliability work goes ahead of features for that service.
