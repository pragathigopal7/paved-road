"""paved: command line for the paved road platform."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from paved import __version__, policy, rightsize, runbooks, scaffold
from paved.manifests import ManifestError, load_paths


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="paved")
def main() -> None:
    """Tooling for an observable by default internal developer platform."""


# new ----------------------------------------------------------------------------------


@main.command("new")
@click.argument("name")
@click.option("--team", required=True, help="Owning team; also the default namespace.")
@click.option("--topic", required=True, help="Kafka topic to consume.")
@click.option("--template", default="kafka-consumer", show_default=True)
@click.option("--namespace", default=None, help="Kubernetes namespace (default: team).")
@click.option(
    "--registry",
    "image_registry",
    default=scaffold.ServiceSpec.image_registry,
    show_default=True,
    help="Container registry for the image.",
)
@click.option("--env", "environment", default="dev", show_default=True)
@click.option(
    "--bootstrap-servers", default=scaffold.ServiceSpec.bootstrap_servers, show_default=True
)
@click.option(
    "--max-replicas",
    default=6,
    show_default=True,
    type=int,
    help="KEDA ceiling; keep at or below the partition count.",
)
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Output directory (default: ./NAME).",
)
@click.option("--force", is_flag=True, help="Overwrite a non empty output directory.")
@click.option("--dry-run", is_flag=True, help="List the files without writing them.")
def new_cmd(
    name,
    team,
    topic,
    template,
    namespace,
    image_registry,
    environment,
    bootstrap_servers,
    max_replicas,
    out_dir,
    force,
    dry_run,
) -> None:
    """Generate a service that passes `paved check` on day one."""
    spec = scaffold.ServiceSpec(
        name=name,
        team=team,
        topic=topic,
        namespace=namespace,
        image_registry=image_registry,
        environment=environment,
        bootstrap_servers=bootstrap_servers,
        max_replicas=max_replicas,
    )
    try:
        files = scaffold.render(spec, template)
        if dry_run:
            for rel in sorted(files):
                click.echo(rel)
            return
        target = Path(out_dir or name)
        written = scaffold.write(files, target, force=force)
    except scaffold.ScaffoldError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"created {len(written)} files in {target}/")
    click.echo(f"next: cd {target} && pip install -r requirements-dev.txt && pytest")


# check --------------------------------------------------------------------------------


@main.command("check")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json", "github", "sarif"]),
    default="text",
    show_default=True,
)
@click.option("--strict", is_flag=True, help="Fail on warnings as well as errors.")
@click.option("--rule", "rules", multiple=True, help="Only run these rule IDs.")
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False),
    default=None,
    help="Write the report to a file instead of stdout.",
)
def check_cmd(paths, fmt, strict, rules, output) -> None:
    """Check Kubernetes manifests against the platform policy."""
    try:
        objects = load_paths(paths)
    except ManifestError as exc:
        raise click.ClickException(str(exc)) from exc
    report = policy.check(objects, only=rules or None)
    rendered = {
        "text": policy.format_text,
        "json": policy.format_json,
        "github": policy.format_github,
        "sarif": policy.format_sarif,
    }[fmt](report)
    if output:
        Path(output).write_text(rendered + "\n", encoding="utf-8")
        click.echo(policy.format_text(report).splitlines()[-1], err=True)
    else:
        click.echo(rendered)
    sys.exit(1 if report.failed(strict) else 0)


@main.group("policy")
def policy_group() -> None:
    """Inspect the platform policy."""


@policy_group.command("list")
def policy_list() -> None:
    """List every rule with its severity and rationale."""
    for r in sorted(policy.RULES.values(), key=lambda r: r.id):
        click.echo(f"{r.id}  {r.severity:<7}  {r.name}")
        click.echo(f"       {r.summary} {r.rationale}")


# rightsize ----------------------------------------------------------------------------


@main.command("rightsize")
@click.option(
    "--usage",
    "usage_file",
    type=click.Path(exists=True, dir_okay=False),
    help="JSON file of observed usage (see examples/usage.json).",
)
@click.option("--prometheus", "prom_url", help="Prometheus base URL to query instead.")
@click.option("--namespace", help="Namespace, with --prometheus.")
@click.option("--deployment", help="Deployment name, with --prometheus.")
@click.option("--window", default="7d", show_default=True, help="Lookback, with --prometheus.")
@click.option("--vcpu-hour", type=float, default=rightsize.DEFAULT_VCPU_HOUR, show_default=True)
@click.option("--gb-hour", type=float, default=rightsize.DEFAULT_GB_HOUR, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def rightsize_cmd(
    usage_file, prom_url, namespace, deployment, window, vcpu_hour, gb_hour, as_json
) -> None:
    """Recommend requests and limits from real usage, with the monthly cost delta."""
    if bool(usage_file) == bool(prom_url):
        raise click.UsageError("pass exactly one of --usage or --prometheus")
    if usage_file:
        usages = rightsize.load_usage_file(Path(usage_file).read_text(encoding="utf-8"))
    else:
        if not (namespace and deployment):
            raise click.UsageError("--prometheus needs --namespace and --deployment")
        usages = rightsize.PrometheusSource(prom_url).usage(namespace, deployment, window)
    pricing = rightsize.Pricing(vcpu_hour, gb_hour)
    recs = [rightsize.recommend(u, pricing) for u in usages]
    if as_json:
        click.echo(json.dumps([r.to_dict() for r in recs], indent=2))
    else:
        click.echo(rightsize.format_table(recs))


# runbook ------------------------------------------------------------------------------


@main.group("runbook")
def runbook_group() -> None:
    """Search and render failure triage playbooks."""


@runbook_group.command("list")
def runbook_list() -> None:
    for slug, b in runbooks.load().items():
        alerts = ", ".join(b.alerts) or "-"
        click.echo(f"{slug:<26} {b.severity:<7} {b.title}  [{alerts}]")


@runbook_group.command("search")
@click.argument("query", nargs=-1, required=True)
def runbook_search(query) -> None:
    hits = runbooks.search(runbooks.load(), " ".join(query))
    if not hits:
        raise click.ClickException("no matching runbooks")
    for b, score in hits:
        click.echo(f"{score:>6.2f}  {b.slug:<26} {b.title}")


@runbook_group.command("show")
@click.argument("slug")
@click.option("--label", "labels", multiple=True, help="key=value to fill placeholders.")
def runbook_show(slug, labels) -> None:
    books = runbooks.load()
    if slug not in books:
        raise click.ClickException(f"no runbook {slug!r}; try `paved runbook list`")
    click.echo(books[slug].render(_parse_labels(labels)))


@runbook_group.command("triage")
@click.argument("alert_name")
@click.option("--label", "labels", multiple=True, help="Alert labels as key=value.")
def runbook_triage(alert_name, labels) -> None:
    """Print the runbook for a firing alert with its labels filled in."""
    book = runbooks.for_alert(runbooks.load(), alert_name)
    if book is None:
        raise click.ClickException(f"no runbook handles alert {alert_name!r}")
    click.echo(book.render(_parse_labels(labels)))


def _parse_labels(pairs) -> dict[str, str]:
    out = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise click.BadParameter(f"expected key=value, got {pair!r}", param_hint="--label")
        out[key.strip()] = value.strip()
    return out


# mcp ----------------------------------------------------------------------------------


@main.command("mcp")
@click.option(
    "--workspace",
    type=click.Path(file_okay=False, exists=True),
    default=".",
    show_default=True,
    help="Only files under this directory can be read or written.",
)
def mcp_cmd(workspace) -> None:
    """Run the MCP server (stdio) so AI assistants can use these tools."""
    try:
        from paved.mcp_server import build_server
    except ImportError as exc:  # pragma: no cover
        raise click.ClickException("install the mcp extra: pip install 'paved-road[mcp]'") from exc
    build_server(Path(workspace)).run("stdio")


if __name__ == "__main__":
    main()
