# labcore

Shared lab logic, pinned by tag. The third of the three sync channels: Copier owns
the project shell, nf-core owns the pipeline skeleton, and **labcore owns all reusable
logic** (build plan ADR-2, ADR-4).

Logic is never copied into projects. A bugfix in the figure theme reaches you as a
version bump, not bundled inside a scaffold update you may not want.

## Install

```bash
pixi add --pypi "labcore @ git+https://github.com/aramburu-lab/labcore.git@v0.1.0"
```

Extras: `viz` (matplotlib + plotly), `io` (duckdb + pyarrow), `stats` (scipy), `all`.

## What's in it

| Module | Does |
|---|---|
| `labcore.viz` | Dual-theme figures. `save_figure()` writes `_white`/`_black` vector PDF; interactive Plotly is opt-in. |
| `labcore.io` | `write_table()` — writes a table plus a `<name>_README.md` provenance companion, and refuses a column with no description. |
| `labcore.frames` | Polars helpers: `report_size()`, `downcast()` that checks real min/max before narrowing. |
| `labcore.paths` | Resolves data roots from `settings/paths.toml`. Fails loudly on unset — never falls back. |
| `labcore.meta` | The `codebase-meta` parser + JSON Schema. |
| `labcore.stats` | Pairwise tests extracted from the Riboseq plotting lib. |
| `labcore.repro` | `seed_everything()`. |
| `labdocs` | The docs engine — `render`, `lint`, `graph`, `api`, `audit`. |

## labdocs

```bash
labdocs render          # -> explain_codebase.html, codebase_manifest.json, knowledge/codebase_map.md
labdocs render --check  # staleness guard, for the prek hook
labdocs lint            # metadata + naming enforcement
labdocs api             # -> knowledge/api_index.md
labdocs graph           # Mermaid DAG
labdocs audit ~/Scripts/*   # template drift across many repos
```

## Provenance

Most of this is **extracted, not written**. `labcore.viz` comes from a 1,407-line
`lib_plot_themes.py` that existed at seven paths in five divergent versions; `labcore.repro`
is lifted verbatim from `scAnalysis/lib/reproducibility.py`. See
`codebase_template/knowledge/prior_art.md` §3 for the full seed list and why each was
lifted or rewritten.

## Conventions

Figures emit `_white`/`_black`, not `_light`/`_dark` — `light`/`dark` remain the internal
theme argument. Four projects already depend on that spelling (D-005a).

Static figures are vector PDF with `pdf.fonttype=42` so text stays editable in Illustrator.
Interactive HTML is opt-in (D-005d).
