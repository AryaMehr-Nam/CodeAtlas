"""Typer CLI entrypoint for CodeAtlas scanning, exporting, and studio serving."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from codeatlas import __version__
from codeatlas.analysis.engine import scan_repository
from codeatlas.exporters import estimate_tokens, export_analysis, write_export
from codeatlas.exporters._common import LAYER_LABELS, LAYER_ORDER
from codeatlas.models.analysis import AnalysisResult, ArchSmell, Severity, SmellKind
from codeatlas.models.config import ExportFormat, Layer, ThemeMode
from codeatlas.models.graph import Node, RouteInfo
from codeatlas.server.app import serve_studio, validate_loopback_host

app = typer.Typer(
    name="codeatlas",
    help="CodeAtlas static architecture mapper, dependency analyzer, and interactive studio.",
    no_args_is_help=True,
)

_SEVERITY_STYLES: dict[Severity, str] = {
    Severity.HIGH: "bold red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "cyan",
}

_SEVERITY_RANK: dict[Severity, int] = {
    Severity.HIGH: 0,
    Severity.MEDIUM: 1,
    Severity.LOW: 2,
}


def _stdout_console(*, record: bool = False) -> Console:
    return Console(record=record)


def _stderr_console() -> Console:
    return Console(stderr=True)


def _grade_style(score: float) -> str:
    if score >= 85.0:
        return "bold green"
    if score >= 70.0:
        return "bold yellow"
    return "bold red"


def _validate_repo_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.exists():
        _stderr_console().print(f"[red]Error:[/red] Repository path does not exist: {resolved}")
        raise typer.Exit(code=2)
    if not resolved.is_dir():
        _stderr_console().print(f"[red]Error:[/red] Repository path is not a directory: {resolved}")
        raise typer.Exit(code=2)
    return resolved


def _locate_demo_repo() -> Path:
    candidates: list[Path] = [Path.cwd() / "demo" / "sample_saas"]
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "demo" / "sample_saas")
    candidates.append(Path("/app/demo/sample_saas"))

    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()

    checked = ", ".join(str(item) for item in candidates[:4])
    raise FileNotFoundError(
        f"Unable to locate bundled demo/sample_saas repository (checked: {checked})."
    )


def _build_health_panel(result: AnalysisResult) -> Panel:
    graph = result.graph
    health = result.health
    breakdown = health.breakdown
    violations_count = sum(1 for smell in health.smells if smell.kind == SmellKind.LAYER_VIOLATION)
    lang_counts = Counter(node.language.value for node in graph.nodes)
    lang_text = ", ".join(f"{lang}:{lang_counts[lang]}" for lang in sorted(lang_counts)) or "none"

    body = Text()
    body.append("Repository: ", style="bold")
    body.append(f"{graph.repo_name} ({graph.root_path})\n")
    body.append("Health Score: ", style="bold")
    body.append(f"{health.score:.1f}/100 ({health.grade})", style=_grade_style(health.score))
    body.append(f"  |  Modules: {len(graph.nodes)}  |  Edges: {len(graph.edges)}\n")
    body.append(f"Languages: {lang_text}\n")
    body.append(
        "Breakdown: "
        f"base={breakdown.base_score:.1f}, "
        f"cycles=-{breakdown.cycle_penalty:.1f}, "
        f"violations=-{breakdown.violation_penalty:.1f}, "
        f"god=-{breakdown.god_penalty:.1f}, "
        f"dead=-{breakdown.dead_penalty:.1f}\n"
    )
    body.append(
        "Findings: "
        f"cycles={len(health.cycles)}, "
        f"violations={violations_count}, "
        f"hotspots={len(health.hotspots)}, "
        f"dead_files={len(health.dead_files)}, "
        f"warnings={len(graph.warnings)}"
    )
    if graph.truncated:
        body.append(
            f"\nTruncated: scan stopped at {len(graph.nodes)} files "
            f"(seen={graph.total_files_seen})",
            style="yellow",
        )

    return Panel(
        body,
        title="[bold cyan]CodeAtlas Architecture Summary[/bold cyan]",
        box=box.ROUNDED,
        border_style="cyan",
    )


def _build_layer_table(result: AnalysisResult) -> Table:
    nodes_by_layer: dict[Layer, list[Node]] = {layer: [] for layer in LAYER_ORDER}
    for node in result.graph.nodes:
        nodes_by_layer[node.layer].append(node)

    table = Table(title="Layer Breakdown", box=box.ROUNDED)
    table.add_column("Layer", style="bold")
    table.add_column("Modules", justify="right")
    table.add_column("Total LOC", justify="right")
    table.add_column("Avg Complexity", justify="right")
    table.add_column("Routes", justify="right")

    for layer in LAYER_ORDER:
        layer_nodes = nodes_by_layer[layer]
        count = len(layer_nodes) or result.health.layer_counts.get(layer, 0)
        if count == 0:
            continue
        total_loc = sum(node.loc for node in layer_nodes)
        avg_cc = (
            sum(node.complexity for node in layer_nodes) / len(layer_nodes) if layer_nodes else 0.0
        )
        route_count = sum(len(node.routes) for node in layer_nodes)
        table.add_row(
            LAYER_LABELS[layer],
            str(count),
            str(total_loc),
            f"{avg_cc:.1f}",
            str(route_count),
        )

    return table


def _render_routes_section(console: Console, result: AnalysisResult) -> None:
    route_entries: list[tuple[str, RouteInfo]] = []
    for node in sorted(result.graph.nodes, key=lambda item: item.id):
        for route in node.routes:
            route_entries.append((node.id, route))

    route_entries.sort(
        key=lambda item: (item[1].path, item[1].method, item[0], item[1].line, item[1].handler)
    )
    if not route_entries:
        console.print("[dim]API Route Inventory: No HTTP or API routes detected.[/dim]")
        return

    table = Table(title="API Route Inventory", box=box.ROUNDED)
    table.add_column("Method", style="bold cyan", no_wrap=True)
    table.add_column("Path", overflow="fold")
    table.add_column("Handler", overflow="fold")
    table.add_column("Module", overflow="fold")
    for node_id, route in route_entries:
        handler_label = route.handler or "-"
        if route.framework:
            handler_label = f"{handler_label} [{route.framework}]"
        table.add_row(
            route.method or "ANY",
            route.path,
            handler_label,
            node_id,
        )
    console.print(table)


def _render_cycles_section(console: Console, result: AnalysisResult) -> None:
    cycles = sorted(result.health.cycles, key=lambda item: (item.id, item.nodes))
    if not cycles:
        console.print("[green]Cycles:[/green] No circular dependencies detected.")
        return

    table = Table(title="Circular Dependencies", box=box.ROUNDED, border_style="red")
    table.add_column("Cycle ID", style="bold red", no_wrap=True)
    table.add_column("Nodes", justify="right", no_wrap=True)
    table.add_column("Edges", justify="right", no_wrap=True)
    table.add_column("Path")
    for cycle in cycles:
        cycle_path = cycle.display_path or " -> ".join(cycle.nodes)
        table.add_row(cycle.id, str(len(cycle.nodes)), str(len(cycle.edges)), cycle_path)
    console.print(table)


def _render_violations_section(console: Console, result: AnalysisResult) -> None:
    violations = sorted(
        (smell for smell in result.health.smells if smell.kind == SmellKind.LAYER_VIOLATION),
        key=lambda smell: (
            _SEVERITY_RANK[smell.severity],
            smell.edges[0] if smell.edges else ("", ""),
            smell.title,
        ),
    )
    if not violations:
        console.print(
            "[green]Layer Violations:[/green] No architectural layer violations detected."
        )
        return

    table = Table(title="Layer Violations", box=box.ROUNDED, border_style="yellow")
    table.add_column("Severity", no_wrap=True)
    table.add_column("Rule", no_wrap=True)
    table.add_column("Details", overflow="fold")
    for smell in violations:
        sev_style = _SEVERITY_STYLES[smell.severity]
        detail_text = Text()
        detail_text.append(f"{smell.title}\n", style="bold")
        detail_text.append(smell.description)
        table.add_row(
            Text(smell.severity.value.upper(), style=sev_style),
            smell.rule_id,
            detail_text,
        )
    console.print(table)


def _render_hotspots_section(
    console: Console,
    result: AnalysisResult,
    node_by_id: dict[str, Node],
) -> None:
    hotspot_ids = sorted(
        set(result.health.hotspots)
        | {
            node_id
            for smell in result.health.smells
            if smell.kind == SmellKind.GOD_MODULE
            for node_id in smell.nodes
        }
    )
    if not hotspot_ids:
        console.print("[green]Hotspots:[/green] No God module hotspots detected.")
        return

    table = Table(title="Hotspots (God Modules)", box=box.ROUNDED, border_style="yellow")
    table.add_column("Module", style="bold", no_wrap=True)
    table.add_column("Layer", no_wrap=True)
    table.add_column("LOC", justify="right", no_wrap=True)
    table.add_column("CC", justify="right", no_wrap=True)
    table.add_column("In", justify="right", no_wrap=True)
    table.add_column("Out", justify="right", no_wrap=True)
    table.add_column("Instab", justify="right", no_wrap=True)
    for node_id in hotspot_ids:
        node = node_by_id.get(node_id)
        if node is None:
            table.add_row(node_id, "-", "-", "-", "-", "-", "-")
        else:
            table.add_row(
                node_id,
                LAYER_LABELS[node.layer],
                str(node.loc),
                str(node.complexity),
                str(node.fan_in),
                str(node.fan_out),
                f"{node.instability:.2f}",
            )
    console.print(table)


def _render_dead_files_section(
    console: Console,
    result: AnalysisResult,
    node_by_id: dict[str, Node],
) -> None:
    dead_ids = sorted(
        set(result.health.dead_files)
        | {
            node_id
            for smell in result.health.smells
            if smell.kind == SmellKind.DEAD_CODE
            for node_id in smell.nodes
        }
    )
    if not dead_ids:
        console.print("[green]Dead Files:[/green] No dead-code candidates detected.")
        return

    table = Table(title="Dead Files", box=box.ROUNDED)
    table.add_column("Module", style="bold", no_wrap=True)
    table.add_column("Layer", no_wrap=True)
    table.add_column("LOC", justify="right", no_wrap=True)
    table.add_column("Fan-In", justify="right", no_wrap=True)
    for node_id in dead_ids:
        node = node_by_id.get(node_id)
        if node is None:
            table.add_row(node_id, "-", "-", "0")
        else:
            table.add_row(
                node_id,
                LAYER_LABELS[node.layer],
                str(node.loc),
                str(node.fan_in),
            )
    console.print(table)


def _render_warnings_section(console: Console, result: AnalysisResult) -> None:
    if not result.graph.warnings:
        console.print("[green]Parse Warnings:[/green] None.")
        return

    table = Table(title="Parse Warnings", box=box.ROUNDED, border_style="yellow")
    table.add_column("File", style="bold", overflow="fold")
    table.add_column("Message", overflow="fold")
    table.add_column("Fallback", no_wrap=True)
    for warning in sorted(result.graph.warnings, key=lambda item: (item.path, item.message)):
        table.add_row(warning.path, warning.message, warning.fallback_used or "-")
    console.print(table)


def _render_console_dashboard(result: AnalysisResult, *, record: bool = False) -> Console:
    console = _stdout_console(record=record)
    node_by_id = {node.id: node for node in result.graph.nodes}

    console.print(_build_health_panel(result))
    if result.graph.nodes:
        console.print(_build_layer_table(result))
    else:
        console.print("[dim]Layer Breakdown: No supported source files found.[/dim]")
    _render_routes_section(console, result)
    _render_cycles_section(console, result)
    _render_violations_section(console, result)
    _render_hotspots_section(console, result, node_by_id)
    _render_dead_files_section(console, result, node_by_id)
    _render_warnings_section(console, result)
    return console


def _print_export_confirmation(
    result: AnalysisResult,
    *,
    fmt: ExportFormat,
    output: Path,
    content: str,
    theme: ThemeMode | None = None,
) -> None:
    byte_count = len(content.encode("utf-8"))
    line_count = len(content.splitlines())
    details = (
        f"Format: {fmt.value}\n"
        f"Output: {output}\n"
        f"Repository: {result.graph.repo_name} "
        f"(Score {result.health.score:.1f}/100, Grade {result.health.grade})\n"
        f"Size: {byte_count} bytes ({line_count} lines)"
    )
    if fmt == ExportFormat.LLMS:
        details += f" (~{int(estimate_tokens(content))} est. tokens)"
    if fmt == ExportFormat.HTML and theme is not None:
        details += f"\nTheme: {theme.value}"

    _stdout_console().print(
        Panel(
            details,
            title="[bold green]Export Complete[/bold green]",
            box=box.ROUNDED,
            border_style="green",
        )
    )


def _print_startup_panel(
    repo_root: Path,
    url: str,
    *,
    watch: bool,
    is_demo: bool = False,
) -> None:
    title = "CodeAtlas Demo Studio" if is_demo else "CodeAtlas Studio"
    watch_label = "enabled (live SSE)" if watch else "disabled"
    body = (
        f"URL: {url}\n"
        f"Repository: {repo_root}\n"
        f"File Watch: {watch_label}\n"
        "Press Ctrl+C to stop the server."
    )
    _stdout_console().print(
        Panel(
            body,
            title=f"[bold cyan]{title}[/bold cyan]",
            box=box.ROUNDED,
            border_style="cyan",
        )
    )
    sys.stdout.flush()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"codeatlas {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-V",
            help="Show the CodeAtlas version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """CodeAtlas static architecture mapper, dependency analyzer, and interactive studio."""
    del version


@app.command()
def scan(
    path: Annotated[Path, typer.Argument(help="Path to the repository to scan.")],
    fmt: Annotated[
        ExportFormat,
        typer.Option(
            "--format",
            "-f",
            case_sensitive=False,
            help="Output format: console, json, markdown, mermaid, llms, or html.",
        ),
    ] = ExportFormat.CONSOLE,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Optional output file path to write the rendered report.",
        ),
    ] = None,
    fail_on_cycles: Annotated[
        bool,
        typer.Option(
            "--fail-on-cycles",
            help="Exit with code 1 if any circular dependencies are detected.",
        ),
    ] = False,
    fail_on_violations: Annotated[
        bool,
        typer.Option(
            "--fail-on-violations",
            help="Exit with code 1 if any architectural layer violations are detected.",
        ),
    ] = False,
    max_tokens: Annotated[
        int,
        typer.Option(
            "--max-tokens",
            min=1,
            help="Maximum token budget when rendering llms.txt output.",
        ),
    ] = 8000,
) -> None:
    """Scan a repository and display or export its architectural analysis."""
    repo_root = _validate_repo_path(path)
    result = scan_repository(repo_root)

    if fmt == ExportFormat.CONSOLE:
        recorded_console = _render_console_dashboard(result, record=output is not None)
        if output is not None:
            plain_text = recorded_console.export_text(clear=False)
            write_export(output, plain_text)
            _stdout_console().print(f"[green]Wrote console report to {output}[/green]")
    else:
        rendered = export_analysis(result, fmt, max_tokens=max_tokens)
        if output is not None:
            write_export(output, rendered)
            _print_export_confirmation(result, fmt=fmt, output=output, content=rendered)
        else:
            typer.echo(rendered, nl=False)

    violations: list[ArchSmell] = [
        smell for smell in result.health.smells if smell.kind == SmellKind.LAYER_VIOLATION
    ]
    failed_cycles = fail_on_cycles and bool(result.health.cycles)
    failed_violations = fail_on_violations and bool(violations)
    if failed_cycles or failed_violations:
        reasons: list[str] = []
        if failed_cycles:
            reasons.append(f"{len(result.health.cycles)} cycle(s) detected")
        if failed_violations:
            reasons.append(f"{len(violations)} layer violation(s) detected")
        _stderr_console().print(f"[bold red]Quality gate failed:[/bold red] {', '.join(reasons)}.")
        raise typer.Exit(code=1)


@app.command()
def serve(
    path: Annotated[
        Path, typer.Argument(help="Path to the repository to serve in CodeAtlas Studio.")
    ],
    host: Annotated[
        str,
        typer.Option(
            "--host",
            help="Loopback host to bind (127.0.0.1, localhost, or ::1).",
        ),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option(
            "--port",
            "-p",
            min=0,
            max=65535,
            help="TCP port to bind (0 selects an available ephemeral port).",
        ),
    ] = 8770,
    watch: Annotated[
        bool,
        typer.Option(
            "--watch/--no-watch",
            help="Watch repository files for changes and push live SSE updates.",
        ),
    ] = False,
    no_open: Annotated[
        bool,
        typer.Option(
            "--no-open",
            help="Do not automatically open the studio in your default browser.",
        ),
    ] = False,
) -> None:
    """Launch the hardened localhost CodeAtlas web studio for a repository."""
    repo_root = _validate_repo_path(path)
    try:
        validated_host = validate_loopback_host(host)
    except ValueError as exc:
        _stderr_console().print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    def _on_ready(url: str) -> None:
        _print_startup_panel(repo_root, url, watch=watch, is_demo=False)

    try:
        serve_studio(
            repo_root,
            host=validated_host,
            port=port,
            watch=watch,
            open_browser=not no_open,
            on_ready=_on_ready,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        _stderr_console().print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def export(
    path: Annotated[Path, typer.Argument(help="Path to the repository to scan and export.")],
    fmt: Annotated[
        ExportFormat,
        typer.Option(
            "--format",
            "-f",
            case_sensitive=False,
            help="Export format: json, markdown, mermaid, llms, or html.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Destination file path for the exported artifact.",
        ),
    ],
    max_tokens: Annotated[
        int,
        typer.Option(
            "--max-tokens",
            min=1,
            help="Maximum token budget when exporting llms.txt format.",
        ),
    ] = 8000,
    theme: Annotated[
        ThemeMode,
        typer.Option(
            "--theme",
            case_sensitive=False,
            help="Default UI theme for standalone HTML exports (obsidian or paper).",
        ),
    ] = ThemeMode.OBSIDIAN,
) -> None:
    """Export architectural analysis to JSON, Markdown, Mermaid, llms.txt, or standalone HTML."""
    if fmt == ExportFormat.CONSOLE:
        _stderr_console().print(
            "[red]Error:[/red] Format 'console' is not supported for 'codeatlas export'. "
            "Choose one of: json, markdown, mermaid, llms, html."
        )
        raise typer.Exit(code=2)

    repo_root = _validate_repo_path(path)
    result = scan_repository(repo_root)
    content = export_analysis(result, fmt, max_tokens=max_tokens, default_theme=theme)
    write_export(output, content)
    _print_export_confirmation(result, fmt=fmt, output=output, content=content, theme=theme)


@app.command()
def demo(
    port: Annotated[
        int,
        typer.Option(
            "--port",
            "-p",
            min=0,
            max=65535,
            help="TCP port to bind (0 selects an available ephemeral port).",
        ),
    ] = 8770,
    no_open: Annotated[
        bool,
        typer.Option(
            "--no-open",
            help="Do not automatically open the studio in your default browser.",
        ),
    ] = False,
) -> None:
    """Scan and launch CodeAtlas Studio for the bundled demo/sample_saas repository."""
    try:
        demo_root = _locate_demo_repo()
    except FileNotFoundError as exc:
        _stderr_console().print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    result = scan_repository(demo_root)
    _render_console_dashboard(result)

    def _on_ready(url: str) -> None:
        _print_startup_panel(demo_root, url, watch=False, is_demo=True)

    try:
        serve_studio(
            demo_root,
            host="127.0.0.1",
            port=port,
            watch=False,
            open_browser=not no_open,
            on_ready=_on_ready,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        _stderr_console().print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


__all__ = ["app"]
