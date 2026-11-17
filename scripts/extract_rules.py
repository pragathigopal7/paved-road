"""Extract the rule groups from a PrometheusRule manifest so promtool can check them.

usage: python scripts/extract_rules.py platform/prometheus/alerts.yaml build/prometheus-rules.yaml
"""

import sys
from pathlib import Path

import yaml


def main(src: str, dst: str) -> None:
    doc = yaml.safe_load(Path(src).read_text())
    if doc.get("kind") != "PrometheusRule":
        raise SystemExit(f"{src} is not a PrometheusRule")
    out = Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump({"groups": doc["spec"]["groups"]}, sort_keys=False))


if __name__ == "__main__":
    main(*sys.argv[1:3])
