"""Markdown from result files: the per-run report, the trend table, and run-vs-run diffs.

Everything here reads `result.metrics` (which `evals/report.py --check` proves equal to
`recompute(result)`), so the markdown never contains a number the JSON cannot back.
"""

from collections.abc import Sequence

from evals.harness.metrics import CaseRow, Metrics, StageMetrics
from evals.harness.results import EvalResult

SMALL_SAMPLE_NOTE = (
    "Small sample: one hit more or less moves recall by {step} points. Read the 95% "
    "intervals, not just the point values, and do not treat small differences between runs "
    "as real."
)


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def ci(interval: tuple[float, float] | None) -> str:
    return "" if interval is None else f" [{interval[0] * 100:.0f}–{interval[1] * 100:.0f}]"


def num(x: float | None, unit: str = "") -> str:
    return "n/a" if x is None else f"{x:g}{unit}"


def usd(x: str | None) -> str:
    return "n/a" if x is None else f"${x}"


def sample_line(result: EvalResult) -> str:
    s = result.metrics.sample
    split = result.header.split + (" (test split included)" if result.header.include_test else "")
    return (
        f"**Sample:** {s.cases} cases ({s.with_issues} with issues, {s.clean} clean "
        f"[{s.clean_with_code} with code]), {s.issues} required issues, {s.optional} optional; "
        f"split `{split}`; {s.repeats} repeat(s), {s.case_runs} case-runs."
    )


def _stage_row(name: str, m: StageMetrics) -> str:
    return (
        f"| {name} | {m.tp} | {m.fp} ({m.duplicates}) | {m.fn} | {pct(m.precision)}"
        f"{ci(m.precision_ci95)} | {pct(m.recall)}{ci(m.recall_ci95)} | {pct(m.f1)} | "
        f"{num(m.fp_per_clean)} | {num(m.fp_per_clean_with_code)} |"
    )


def _stage_table(rows: Sequence[tuple[str, StageMetrics]]) -> list[str]:
    return [
        "| | TP | FP (of which duplicates) | FN | precision [95% CI] | recall [95% CI] | F1 | "
        "FP per clean case | FP per clean case with code |",
        "|---|---|---|---|---|---|---|---|---|",
        *(_stage_row(name, m) for name, m in rows),
    ]


def render_report(result: EvalResult) -> str:
    h, m = result.header, result.metrics
    posted = m.stages["posted"]
    u = m.usage
    step = round(100 / m.sample.issues, 1) if m.sample.issues else "n/a"
    review = h.review_config
    lines = [
        f"# Eval report `{h.run_id}`",
        "",
        sample_line(result),
        "",
        f"> {SMALL_SAMPLE_NOTE.format(step=step)}",
        "",
    ]
    if result.is_fake:
        lines += [
            "> **Fake provider:** no model was called. This is a smoke run, not a measurement.",
            "",
        ]
    if h.git_dirty:
        lines += ["> **Dirty tree:** the code had uncommitted changes; not reproducible.", ""]
    lines += [
        "| | |",
        "|---|---|",
        f"| Date (UTC) | {h.created_at:%Y-%m-%d %H:%M} → {result.finished_at:%H:%M} |",
        f"| Model | `{h.model}` ({h.provider}) |",
        f"| Pipeline | `{h.pipeline}`, prompts {', '.join(f'`{p}`' for p in h.prompt_versions)} |",
        f"| Code | `{h.git_sha[:12]}`{' (dirty)' if h.git_dirty else ''} |",
        f"| Dataset | `{h.dataset_name}` version `{h.dataset_version}` (split {h.splits_status}) |",
        f"| Matching | tolerance ±{h.tolerance} lines, categories `{h.categories_sha256}` |",
        f"| Review policy | confidence ≥ {review['confidence_threshold']}, at most "
        f"{review['max_comments']} comments, {review['llm_concurrency']} chunk(s) at a time |",
        f"| Budget | spent ${result.budget.spent_usd} of ${result.budget.max_cost_usd} "
        "(estimated) |",
        "",
        "## Headline (posted findings: what a PR author would see)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Precision | {pct(posted.precision)}{ci(posted.precision_ci95)} ({posted.tp} of "
        f"{posted.tp + posted.fp}) |",
        f"| Recall | {pct(posted.recall)}{ci(posted.recall_ci95)} ({posted.tp} of "
        f"{posted.tp + posted.fn}) |",
        f"| F1 | {pct(posted.f1)} |",
        f"| False positives per clean case | {num(posted.fp_per_clean)} ({posted.fp_on_clean} "
        f"over {posted.clean_runs} clean case-runs) |",
        f"| … clean cases with code only | {num(posted.fp_per_clean_with_code)} "
        f"({posted.fp_on_clean_with_code} over {posted.clean_with_code_runs}) |",
        f"| Severity agreement on hits | {pct(posted.severity_agreement)} |",
        f"| Invalid-output rate | {pct(u.invalid_output_rate)} ({u.invalid_outputs} of "
        f"{u.calls} calls) |",
        f"| Cost per case (estimated, list price) | {usd(u.cost_per_case_usd)} "
        f"({usd(u.cost_per_reviewed_case_usd)} per reviewed case) |",
        f"| Latency per case | {num(u.wall_ms_per_case, ' ms')} wall, "
        f"{num(u.model_latency_ms_per_case, ' ms')} model "
        f"({num(u.wall_ms_per_reviewed_case, ' ms')} wall per reviewed case) |",
        "",
        "## By pipeline stage",
        "",
        "`raw` = schema-valid model output, `anchored` = points at a changed line with real "
        "evidence, `posted` = after dedupe, confidence/severity thresholds and the comment cap. "
        "`raw` vs `posted` is the confidence-filter ablation.",
        "",
        *_stage_table(
            [
                ("raw", m.stages["raw"]),
                ("anchored", m.stages["anchored"]),
                ("posted", posted),
                ("posted, strict (±0 lines)", m.strict_posted),
            ]
        ),
        "",
    ]
    if m.sample.repeats > 1:
        lines += [
            "## Per repeat (posted)",
            "",
            "| Repeat | Precision | Recall | F1 |",
            "|---|---|---|---|",
            *(
                f"| {r.repeat} | {pct(r.precision)} | {pct(r.recall)} | {pct(r.f1)} |"
                for r in posted.per_repeat
            ),
            "",
        ]
    lines += [
        "## Per split (posted)",
        "",
        *_stage_table([(f"`{name}`", s) for name, s in m.per_split.items()]),
        "",
        "## Per category (posted)",
        "",
        "| Category | Issues | Found | Recall | Reported | Correct | Precision |",
        "|---|---|---|---|---|---|---|",
        *(
            f"| `{c.category}`{'' if c.known else ' (unknown)'} | {c.issues} | {c.found} | "
            f"{pct(c.recall)} | {c.predicted} | {c.correct} | {pct(c.precision)} |"
            for c in m.per_category
        ),
        "",
        "## Per agent (posted)",
        "",
        "| Agent | Findings | TP | FP | Precision |",
        "|---|---|---|---|---|",
        *(
            f"| `{a.agent}` | {a.findings} | {a.tp} | {a.fp} | {pct(a.precision)} |"
            for a in m.per_agent
        ),
        "",
        "## Per case (posted, summed over repeats)",
        "",
        "| Case | Split | Runs | Issues/run | TP | FP | FN | Duplicates | Optional hits |",
        "|---|---|---|---|---|---|---|---|---|",
        *(_case_row(c) for c in m.per_case),
        "",
        "## Model calls",
        "",
        f"- {u.calls} calls over {u.reviewed_runs} reviewed case-runs; {u.invalid_outputs} "
        f"invalid structured answers ({pct(u.invalid_output_rate)}).",
        f"- {u.chunks_failed} of {u.chunks_total} diff chunks still invalid after retries "
        f"({pct(u.chunk_failure_rate)}).",
        f"- Total estimated cost ${u.total_cost_usd}; slowest case-run {u.max_wall_ms} ms wall.",
        "",
    ]
    if m.unknown_categories:
        listed = ", ".join(f"`{k}` ×{v}" for k, v in m.unknown_categories.items())
        lines += [
            "## Categories not in categories.json (raw stage)",
            "",
            f"{listed}. A real hit under one of these names counts as a miss; review whether "
            "an alias is missing.",
            "",
        ]
    lines += [
        "---",
        "Matching rule and metric definitions: `docs/evals.md`. Costs are estimates at list "
        "price (`app/ai/llm/pricing.py`); free tiers bill nothing.",
        "",
    ]
    return "\n".join(lines)


def _case_row(c: CaseRow) -> str:
    return (
        f"| `{c.case}` | {c.split or ''} | {c.runs} | {c.issues} | {c.tp} | {c.fp} | {c.fn} | "
        f"{c.duplicates} | {c.optional_hits} |"
    )


def render_trend(results: Sequence[EvalResult]) -> str:
    lines = [
        "# Eval trend",
        "",
        "One row per recorded run (fake-provider runs excluded), oldest first. Generated by "
        "`python -m evals.report`; do not edit by hand. Headline stage = posted findings.",
        "",
        "| Date (UTC) | Run | Model | Prompts | Dataset | Split | Sample | Precision | Recall "
        "| F1 | FP/clean case | Invalid output | Cost/case | Wall/case |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.is_fake:
            continue
        h, m = r.header, r.metrics
        p, u = m.stages["posted"], m.usage
        lines.append(
            f"| {h.created_at:%Y-%m-%d} | [`{h.run_id}`]({h.run_id}.md) | `{h.model}` | "
            f"{'+'.join(h.prompt_versions)} | {h.dataset_version} | {h.split} | "
            f"{m.sample.cases} cases × {m.sample.repeats} | {pct(p.precision)} | "
            f"{pct(p.recall)} | {pct(p.f1)} | {num(p.fp_per_clean)} | "
            f"{pct(u.invalid_output_rate)} | {usd(u.cost_per_case_usd)} | "
            f"{num(u.wall_ms_per_case, ' ms')} |"
        )
    if len(lines) == 6:
        lines.append("| _no runs recorded yet_ | | | | | | | | | | | | | |")
    return "\n".join(lines) + "\n"


# --- comparison ------------------------------------------------------------------------------


def _delta(a: float | None, b: float | None, scale: float = 100) -> str:
    if a is None or b is None:
        return "n/a"
    d = (b - a) * scale
    return f"{d:+.1f}" + (" pts" if scale == 100 else "")


def compare_warnings(a: EvalResult, b: EvalResult) -> list[str]:
    ha, hb = a.header, b.header
    checks = [
        (ha.dataset_version != hb.dataset_version, "different dataset versions"),
        (ha.cases != hb.cases, "different case selections"),
        (ha.tolerance != hb.tolerance, "different matching tolerance"),
        (ha.categories_sha256 != hb.categories_sha256, "different categories.json"),
        (ha.review_config != hb.review_config, "different review policy"),
        (a.is_fake or b.is_fake, "a fake-provider run is involved"),
        (ha.git_dirty or hb.git_dirty, "a run from a dirty tree is involved"),
    ]
    return [f"{text}: the numbers are not directly comparable" for bad, text in checks if bad]


def render_compare(a: EvalResult, b: EvalResult) -> str:
    ma, mb = a.metrics, b.metrics
    lines = [
        f"# Compare `{a.header.run_id}` → `{b.header.run_id}`",
        "",
        f"- A: `{a.header.model}`, {'+'.join(a.header.prompt_versions)}, {sample_line(a)}",
        f"- B: `{b.header.model}`, {'+'.join(b.header.prompt_versions)}, {sample_line(b)}",
        "",
    ]
    lines += [f"> **Warning:** {w}" for w in compare_warnings(a, b)]
    lines += ["", "| Stage | Metric | A | B | Δ (B − A) |", "|---|---|---|---|---|"]
    for stage in ("raw", "posted"):
        sa, sb = ma.stages[stage], mb.stages[stage]
        for label, x, y in (
            ("precision", sa.precision, sb.precision),
            ("recall", sa.recall, sb.recall),
            ("F1", sa.f1, sb.f1),
        ):
            lines.append(f"| {stage} | {label} | {pct(x)} | {pct(y)} | {_delta(x, y)} |")
        lines.append(
            f"| {stage} | FP per clean case | {num(sa.fp_per_clean)} | {num(sb.fp_per_clean)} | "
            f"{_delta(sa.fp_per_clean, sb.fp_per_clean, 1)} |"
        )
    ua, ub = ma.usage, mb.usage
    lines += [
        f"| – | invalid-output rate | {pct(ua.invalid_output_rate)} | "
        f"{pct(ub.invalid_output_rate)} | "
        f"{_delta(ua.invalid_output_rate, ub.invalid_output_rate)} |",
        f"| – | cost per case | {usd(ua.cost_per_case_usd)} | {usd(ub.cost_per_case_usd)} | |",
        f"| – | wall ms per case | {num(ua.wall_ms_per_case)} | {num(ub.wall_ms_per_case)} | "
        f"{_delta(ua.wall_ms_per_case, ub.wall_ms_per_case, 1)} |",
        "",
        "## Per category recall (posted)",
        "",
        "| Category | A | B | Δ |",
        "|---|---|---|---|",
    ]
    cats_a = {c.category: c for c in ma.per_category}
    cats_b = {c.category: c for c in mb.per_category}
    for name in sorted(set(cats_a) | set(cats_b)):
        ra = cats_a[name].recall if name in cats_a else None
        rb = cats_b[name].recall if name in cats_b else None
        if ra is None and rb is None:
            continue
        lines.append(f"| `{name}` | {pct(ra)} | {pct(rb)} | {_delta(ra, rb)} |")
    lines += _case_changes(ma, mb)
    return "\n".join(lines) + "\n"


def _counts(row: CaseRow | None) -> str:
    return "–" if row is None else f"{row.tp} / {row.fp} / {row.fn}"


def _case_changes(ma: Metrics, mb: Metrics) -> list[str]:
    rows_a = {c.case: c for c in ma.per_case}
    rows_b = {c.case: c for c in mb.per_case}
    changed = []
    for name in sorted(set(rows_a) | set(rows_b)):
        a, b = rows_a.get(name), rows_b.get(name)
        if _counts(a) != _counts(b):
            changed.append(f"| `{name}` | {_counts(a)} | {_counts(b)} |")
    if not changed:
        return ["", "No case changed its TP / FP / FN counts."]
    return [
        "",
        "## Cases that changed (posted, TP / FP / FN summed over repeats)",
        "",
        "| Case | A | B |",
        "|---|---|---|",
        *changed,
    ]
