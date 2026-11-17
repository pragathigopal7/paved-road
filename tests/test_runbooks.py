import re

import pytest
import yaml

from conftest import ROOT
from paved import runbooks

ALERTS_FILE = ROOT / "platform" / "prometheus" / "alerts.yaml"
RUNBOOK_URL = re.compile(r"/src/paved/runbooks/([a-z0-9-]+)\.md$")


@pytest.fixture(scope="module")
def books():
    return runbooks.load()


@pytest.fixture(scope="module")
def alert_rules():
    doc = yaml.safe_load(ALERTS_FILE.read_text())
    return [r for g in doc["spec"]["groups"] for r in g["rules"] if "alert" in r]


def test_all_runbooks_parse_with_titles(books):
    assert len(books) >= 8
    for slug, b in books.items():
        assert b.title and b.body.startswith("# "), slug
        assert b.severity in {"page", "ticket"}, slug


def test_every_alert_links_to_a_runbook_that_claims_it(books, alert_rules):
    for rule in alert_rules:
        url = rule["annotations"]["runbook_url"]
        m = RUNBOOK_URL.search(url)
        assert m, f"{rule['alert']} has a malformed runbook_url {url}"
        assert m.group(1) in books, f"{rule['alert']} links to missing runbook {m.group(1)}"
        assert rule["alert"] in books[m.group(1)].alerts, rule["alert"]
        assert rule["labels"]["severity"] in {"page", "ticket"}


def test_every_runbook_alert_exists(books, alert_rules):
    defined = {r["alert"] for r in alert_rules}
    for b in books.values():
        for alert in b.alerts:
            assert alert in defined, f"{b.slug} claims undefined alert {alert}"


def test_relative_links_between_runbooks_resolve(books):
    for b in books.values():
        for target in re.findall(r"\]\(([a-z0-9-]+)\.md\)", b.body):
            assert target in books, f"{b.slug} links to missing {target}.md"


@pytest.mark.parametrize(
    "query,slug",
    [
        ("consumer lag growing", "kafka-consumer-lag"),
        ("OOMKilled restart", "pod-crashloop"),
        ("jobs queued github actions", "gha-runner-backlog"),
        ("terraform lock", "terraform-state-lock"),
        ("spans dropped collector", "otel-collector-dropping"),
        ("poison message dlq", "dead-letter-growth"),
    ],
)
def test_search_ranks_the_right_runbook_first(books, query, slug):
    hits = runbooks.search(books, query)
    assert hits and hits[0][0].slug == slug, [(b.slug, s) for b, s in hits]


def test_triage_fills_labels_and_leaves_unknowns_visible(books):
    book = runbooks.for_alert(books, "KafkaConsumerLagHigh")
    text = book.render({"namespace": "payments", "consumergroup": "orders-consumer"})
    assert "kubectl -n payments exec deploy/orders-consumer" in text
    assert "{{topic}}" in text  # not provided, so left for the human
    assert {"namespace", "consumergroup", "topic"} <= book.placeholders()


def test_front_matter_is_required():
    with pytest.raises(ValueError):
        runbooks.parse("# no front matter\n", "x")
