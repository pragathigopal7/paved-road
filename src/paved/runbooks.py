"""Failure triage playbooks: load, search, and render runbooks for an alert.

Runbooks are Markdown with YAML front matter. The `alerts` list ties a runbook to
Prometheus alert names, and commands use {{label}} placeholders that are filled from
the firing alert's labels, so on call gets copy paste ready commands.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_TOKEN = re.compile(r"[a-z0-9]+")
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


@dataclass
class Runbook:
    slug: str
    title: str
    body: str
    alerts: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    severity: str = "ticket"

    def render(self, labels: dict[str, str] | None = None) -> str:
        """Fill {{label}} placeholders; leave unknown ones visible for the reader."""
        labels = labels or {}

        def sub(m: re.Match[str]) -> str:
            return labels.get(m.group(1), m.group(0))

        return _PLACEHOLDER.sub(sub, self.body)

    def placeholders(self) -> set[str]:
        return set(_PLACEHOLDER.findall(self.body))

    def summary(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "title": self.title,
            "alerts": self.alerts,
            "tags": self.tags,
            "severity": self.severity,
        }


def parse(text: str, slug: str) -> Runbook:
    m = _FRONT_MATTER.match(text)
    if not m:
        raise ValueError(f"runbook {slug} has no YAML front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    if "title" not in meta:
        raise ValueError(f"runbook {slug} front matter needs a title")
    return Runbook(
        slug=slug,
        title=str(meta["title"]),
        body=m.group(2).strip() + "\n",
        alerts=[str(a) for a in meta.get("alerts") or []],
        tags=[str(t) for t in meta.get("tags") or []],
        severity=str(meta.get("severity", "ticket")),
    )


def load(directory: str | Path | None = None) -> dict[str, Runbook]:
    """Load runbooks from a directory, or the ones shipped with the package."""
    books: dict[str, Runbook] = {}
    if directory is not None:
        for f in sorted(Path(directory).glob("*.md")):
            books[f.stem] = parse(f.read_text(encoding="utf-8"), f.stem)
        return books
    pkg = resources.files("paved") / "runbooks"
    for entry in sorted(pkg.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            slug = entry.name[:-3]
            books[slug] = parse(entry.read_text(encoding="utf-8"), slug)
    return books


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def split_camel(name: str) -> str:
    """'KafkaConsumerLagHigh' -> 'Kafka Consumer Lag High' so alert names are searchable."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)


def search(books: dict[str, Runbook], query: str, limit: int = 5) -> list[tuple[Runbook, float]]:
    """BM25 over title (x3), tags and alerts (x2) and body (x1)."""
    docs: dict[str, list[str]] = {}
    for slug, b in books.items():
        alert_words = " ".join(split_camel(a) for a in b.alerts)
        docs[slug] = (
            _tokens(b.title) * 3
            + _tokens(" ".join(b.tags) + " " + alert_words) * 2
            + _tokens(b.body)
        )
    if not docs:
        return []
    n = len(docs)
    avg_len = sum(len(d) for d in docs.values()) / n
    df: Counter[str] = Counter()
    for d in docs.values():
        df.update(set(d))
    k1, b_param = 1.5, 0.75
    scored = []
    for slug, d in docs.items():
        tf = Counter(d)
        score = 0.0
        for term in set(_tokens(query)):
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            denom = tf[term] + k1 * (1 - b_param + b_param * len(d) / avg_len)
            score += idf * tf[term] * (k1 + 1) / denom
        if score > 0:
            scored.append((books[slug], round(score, 3)))
    scored.sort(key=lambda x: (-x[1], x[0].slug))
    return scored[:limit]


def for_alert(books: dict[str, Runbook], alert_name: str) -> Runbook | None:
    for b in books.values():
        if alert_name in b.alerts:
            return b
    return None
