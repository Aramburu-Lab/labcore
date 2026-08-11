from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from labcore.labdocs import render
from labcore.labdocs.graph import build_graph, classify, dag_html, mermaid_dag
from labcore.meta import MetaBlock

FIXTURE = Path(__file__).parent / "fixtures" / "demo_project"
STEPS = ["fetch", "load", "qc_filter", "normalise", "differential", "figures"]
ORDERS = ["00", "10", "20", "30", "40", "50"]

# `(src|href)="http..."` in any form is the defect prior_art.md §3 records against
# the prior-art report generator: it breaks offline viewing and strict CSP.
EXTERNAL_REF = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", re.I)
RUN_ROW = re.compile(r"<td>(\d{2})</td><td><strong[^>]*>([a-z0-9_]+)</strong></td>")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A throwaway copy of the demo fixture — rendering writes into the tree."""
    dst = tmp_path / "demo_project"
    shutil.copytree(FIXTURE, dst)
    return dst


@pytest.fixture
def rendered(root: Path) -> Path:
    render.render_all(root)
    return root


def test_render_all_writes_all_three_artifacts(root: Path):
    written = render.render_all(root)

    assert len(written) == 3
    assert (root / "explain_codebase.html").is_file()
    assert (root / "codebase_manifest.json").is_file()
    assert (root / "knowledge" / "codebase_map.md").is_file()


def test_manifest_records_every_step_in_run_order(rendered: Path):
    manifest = json.loads((rendered / "codebase_manifest.json").read_text())

    assert manifest["flavour"] == "analysis"
    assert [s["name"] for s in manifest["steps"]] == STEPS
    assert [s["order"] for s in manifest["steps"]] == [0, 10, 20, 30, 40, 50]
    assert ["fetch", "load"] in manifest["edges"]
    assert manifest["helpers"][0]["name"] == "helpers"


def test_html_contains_every_script_in_run_order(rendered: Path):
    html = (rendered / "explain_codebase.html").read_text()

    positions = [html.index(f">{name}</strong>") for name in STEPS]
    assert positions == sorted(positions)
    assert "Drop low-quality cells by MAD thresholds." in html


def test_html_is_self_contained(rendered: Path):
    html = (rendered / "explain_codebase.html").read_text()

    assert not EXTERNAL_REF.search(html)
    assert "http://" not in html
    assert "https://" not in html
    assert "<style>" in html and "--c1:#0072B2" in html
    assert "getElementById('tg')" in html


def test_run_order_table_has_one_row_per_script(rendered: Path):
    html = (rendered / "explain_codebase.html").read_text()

    rows = RUN_ROW.findall(html)
    assert [order for order, _ in rows] == ORDERS
    assert [name for _, name in rows] == STEPS


def test_external_input_and_terminal_step_get_pills(rendered: Path):
    html = (rendered / "explain_codebase.html").read_text()

    assert '<code>settings/samples.tsv</code> <span class="pill p2">external</span>' in html
    assert '<span class="pill p3">final</span>' in html


def test_check_is_empty_when_fresh_and_flags_edited_metadata(root: Path):
    render.render_all(root)
    assert render.render_all(root, check=True) == []

    step = root / "scripts" / "python" / "normalise.py"
    step.write_text(
        step.read_text().replace(
            "summary: Scale counts by size factors so libraries are comparable.",
            "summary: Scale counts so libraries become comparable across runs.",
        )
    )

    stale = render.render_all(root, check=True)
    assert stale
    assert root / "explain_codebase.html" in stale


def test_check_reports_missing_artifacts(root: Path):
    assert len(render.render_all(root, check=True)) == 3


def test_cli_entry_points_delegate_to_the_same_code(root: Path):
    render.render_all(root)

    assert render.check_stale(root) == []
    assert "fetch --> load" in build_graph(root)


def test_dag_carries_the_fetch_load_edge_and_a_terminal_figures(root: Path):
    project = render.load_project(root)

    mermaid = mermaid_dag(project)
    assert "fetch --> load" in mermaid
    assert 'figures["figures<br/>python · 50"]:::terminal' in mermaid
    assert classify(project) == {
        "fetch": "entry",
        "load": "step",
        "qc_filter": "step",
        "normalise": "step",
        "differential": "step",
        "figures": "terminal",
    }


def test_dag_html_matches_the_reference_markup(root: Path):
    block = dag_html(render.load_project(root))

    assert block.startswith('<div class="dag">')
    assert '<div class="node src">fetch<small>bash · 00</small></div>' in block
    assert '<div class="node term">figures<small>python · 50 · final</small></div>' in block
    assert block.count('<div class="dagrow">') == 3
    assert "entry point" in block and "terminal / deliverable" in block


def test_map_is_the_same_run_order_as_markdown(rendered: Path):
    text = (rendered / "knowledge" / "codebase_map.md").read_text()

    header = "| # | Script | Path | Inputs | Outputs | Key options | Output meaning | Goes to |"
    assert header in text
    assert all(f"**{name}**" in text for name in STEPS)
    assert "`settings/samples.tsv` (external)" in text
    assert "**final**" in text
    assert "```mermaid" in text


def test_generated_files_declare_themselves_generated(rendered: Path):
    html = (rendered / "explain_codebase.html").read_text()
    manifest = json.loads((rendered / "codebase_manifest.json").read_text())
    map_md = (rendered / "knowledge" / "codebase_map.md").read_text()

    assert render.BANNER in html
    assert manifest["_generated"] == render.BANNER
    assert map_md.startswith(f"<!-- {render.BANNER} -->")
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", html)
    assert manifest["labcore_version"] == render.labcore_version()


def test_piped_output_renders_as_a_stream_not_a_filename():
    step = MetaBlock(
        path=Path("scripts/bash/stream.sh"),
        data={
            "name": "stream",
            "order": 0,
            "summary": "Pipe straight into the consumer.",
            "outputs": [{"pipe": "stdout", "to": "count", "desc": "Filtered records."}],
        },
        start_line=1,
    )

    cell = render._outputs_cell(step.outputs)
    assert "stdout" in cell and "count" in cell
    assert ".sh" not in cell


def _write_pipeline(root: Path) -> Path:
    """Minimal nf-core-shaped project: one module sidecar plus a -with-dag file."""
    module = root / "modules" / "local" / "fastqc"
    module.mkdir(parents=True)
    (root / "main.nf").write_text("workflow {}\n")
    (module / "meta.yml").write_text(
        "name: fastqc\n"
        "tools:\n"
        "  - fastqc:\n"
        "      version: 0.12.1\n"
        "input:\n"
        "  - reads:\n"
        "      type: file\n"
        "output:\n"
        "  - html:\n"
        "      type: file\n"
    )
    (module / "main.nf").write_text('process FASTQC { container "biocontainers/fastqc:0.12.1" }\n')
    (root / "pipeline_dag.mmd").write_text("flowchart TB\n  v0([FASTQC])\n")
    return root


def test_pipeline_flavour_gets_an_inventory_and_no_invented_run_order(tmp_path: Path):
    root = _write_pipeline(tmp_path / "pipe_project")
    render.render_all(root)
    html = (root / "explain_codebase.html").read_text()

    assert "Component inventory" in html
    assert "Run order" not in html
    assert "Dataflow (Nextflow" in html
    assert "v0([FASTQC])" in html
    assert "fastqc 0.12.1" in html
    assert "biocontainers/fastqc:0.12.1" in html
