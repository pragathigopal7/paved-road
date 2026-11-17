---
title: OpenTelemetry collector is dropping or refusing telemetry
alerts: [OtelCollectorExportFailing, OtelCollectorRefusingData]
tags: [opentelemetry, otel, collector, traces, logs, metrics, observability, backpressure]
severity: ticket
---
# OpenTelemetry collector is dropping or refusing telemetry

Services send OTLP to `otel-collector.observability:4318`. The collector batches and
exports traces to the tracing backend, metrics to Prometheus remote write, and logs to
OpenSearch. When it drops data, every team loses visibility at once, so treat this as
high priority even though nothing user facing is broken.

## 1. Refusing (backpressure) or failing to export?

```promql
sum by (receiver) (rate(otelcol_receiver_refused_spans_total[5m]))
sum by (exporter) (rate(otelcol_exporter_send_failed_spans_total[5m]))
max by (exporter) (otelcol_exporter_queue_size / otelcol_exporter_queue_capacity)
```

* **Refused at the receiver:** the `memory_limiter` processor is shedding load. The
  collector is undersized for current volume. Step 2.
* **Export failures on `{{exporter}}`:** the backend is down, throttling, or
  credentials expired. Step 3.

## 2. Undersized

```bash
kubectl -n observability top pods -l app.kubernetes.io/name=opentelemetry-collector
kubectl -n observability get hpa
```

Scale out (the deployment is stateless) and raise memory if pods sit at the limit.
Then find who got loud:

```promql
topk(5, sum by (service_name) (rate(otelcol_receiver_accepted_spans_total[5m])))
```

A service emitting a span per loop iteration is a bug in that service. Tail sampling
keeps errors and slow traces, so dropping its sample rate is safe.

## 3. Export failing

```bash
kubectl -n observability logs deploy/otel-collector --since=10m | grep -i -E 'error|throttl|401|403' | tail -30
```

* 401 or 403 to Amazon Managed Prometheus: the IRSA role or `sigv4auth` region is wrong.
* 429 from the backend: raise `sending_queue.queue_size` to ride out bursts and talk to
  the backend owner about limits.
* TLS or DNS errors: check the collector's egress NetworkPolicy.

## Afterwards

Exporter queues are in memory. Data dropped during the incident is gone; note the gap
on affected dashboards so nobody reads it as an outage of the services themselves.
