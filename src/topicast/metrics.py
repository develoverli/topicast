"""Prometheus metrics."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "topicast_http_requests_total",
    "HTTP requests handled.",
    ["method", "route", "status"],
    registry=REGISTRY,
)
MESSAGES_ACCEPTED = Counter(
    "topicast_messages_accepted_total",
    "Messages accepted into the queue.",
    ["alias", "source"],
    registry=REGISTRY,
)
MESSAGES_DEDUPLICATED = Counter(
    "topicast_messages_deduplicated_total",
    "Messages suppressed by the dedupe window.",
    ["alias"],
    registry=REGISTRY,
)
MESSAGES_DELIVERED = Counter(
    "topicast_messages_delivered_total",
    "Messages delivered to Telegram.",
    ["alias"],
    registry=REGISTRY,
)
MESSAGES_FAILED = Counter(
    "topicast_messages_failed_total",
    "Messages that permanently failed.",
    ["alias"],
    registry=REGISTRY,
)
MARKUP_FALLBACKS = Counter(
    "topicast_markup_fallbacks_total",
    "Messages re-sent as plain text because Telegram rejected the markup.",
    ["alias"],
    registry=REGISTRY,
)
TELEGRAM_RETRIES = Counter(
    "topicast_telegram_retries_total",
    "Delivery attempts rescheduled.",
    ["reason"],
    registry=REGISTRY,
)
QUEUE_DEPTH = Gauge(
    "topicast_queue_depth",
    "Messages waiting for delivery.",
    registry=REGISTRY,
)
DELIVERY_LATENCY = Histogram(
    "topicast_delivery_latency_seconds",
    "Time from acceptance to delivery.",
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 900),
    registry=REGISTRY,
)
