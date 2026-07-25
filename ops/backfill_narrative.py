"""Backfill deep-dive narrative for past dives and re-render their reports.

Rehydrates each REVIEWED/REJECTED thesis of the ISO week from Postgres,
adapts the DiveBundle into the narrator's NarrativeContext, runs
reporter.narrate (guard-validated, Sonnet-audited), and rewrites the report
files in place (repo + vault). Never touches cycle_log or documents; a
--force rerun appends a newer report_narrative assertion (assertions stay
immutable) and the renderer picks the newest.

    uv run python -m ops.backfill_narrative 2026-W30 [--thesis ID] [--force] [--no-audit]
"""

import argparse
from datetime import date, datetime, timezone

from agents.roles import reporter
from agents.state import NarrativeContext
from data import repository as repo
from data.db import connect
from reporting import deepdive, packet
from schemas.models import Thesis


def context_from_bundle(bundle: deepdive.DiveBundle,
                        thesis: Thesis) -> NarrativeContext:
    """DiveBundle (rehydrated assertions) → NarrativeContext. Quarantined
    price metrics ride along as citable reviewer-domain evidence (see
    reporter.context_from_run) — never as ranking justification."""
    audit = bundle.audit or {}
    series = []
    for sid, obs in deepdive._macro_series(bundle.macro).items():
        vals = [a.value.get("value") for a in obs if a.value.get("value") is not None]
        latest = obs[-1]
        series.append({
            "series_id": sid, "n_obs": len(obs),
            "assertion_ids": [a.id for a in obs],
            "latest": latest.value.get("value"),
            "latest_date": latest.value.get("date"),
            "min": min(vals) if vals else None,
            "max": max(vals) if vals else None,
        })
    return NarrativeContext(
        theme_title=bundle.theme.title if bundle.theme else None,
        theme_description=bundle.theme.description if bundle.theme else None,
        verdict=audit.get("verdict"),
        causal_chain=thesis.causal_chain,
        valuation_check=thesis.valuation_check,
        do_nothing_case=thesis.do_nothing_case,
        challenges=list(audit.get("challenges", [])),
        price_narrative_check=audit.get("price_narrative_check"),
        basket=bundle.basket,
        claims=[{
            "assertion_id": a.id, "symbol": a.value.get("symbol"),
            "claim": a.value.get("claim"),
            "quote": " ".join((a.citation_span or "").split())[:200],
            "figure_name": a.value.get("figure_name"),
            "figure_value": a.value.get("figure_value"),
            "figure_unit": a.value.get("figure_unit"),
        } for a in bundle.claims],
        metrics=[{
            "assertion_id": a.id, "symbol": a.value.get("symbol"),
            "metric": a.value.get("metric"), "value": a.value.get("value"),
        } for a in bundle.metrics],
        quarantined_metrics=[{
            "assertion_id": a.id, "symbol": a.value.get("symbol"),
            "metric": a.value.get("metric"), "value": a.value.get("value"),
        } for a in bundle.quarantined],
        macro=series,
        forecasts=[{
            "statement": f.statement,
            "p_prior": (bundle.prior_forecasts[i].probability
                        if i < len(bundle.prior_forecasts) else f.probability),
            "p_final": f.probability,
            "horizon_end": str(f.horizon_end),
            "resolution_criterion": f.resolution_criterion,
        } for i, f in enumerate(bundle.forecasts)],
        milestones=[{
            "description": m.description,
            "falsification_criterion": m.falsification_criterion,
        } for m in bundle.milestones],
        exit_criteria={k: str(v) for k, v in (thesis.exit_criteria or {}).items()},
    )


def backfill(week_key: str, thesis_id: int | None = None,
             force: bool = False, sonnet_audit: bool = True) -> int:
    year, week = (int(p) for p in week_key.replace("W", "").split("-"))
    start = date.fromisocalendar(year, week, 1)
    end = date.fromisocalendar(year, week, 7)
    n = 0
    with connect() as conn:
        where = ("status IN ('REVIEWED', 'REJECTED') AND created_at >= %s"
                 " AND created_at < %s + interval '1 day'")
        params: tuple = (start, end)
        if thesis_id is not None:
            where += " AND id = %s"
            params += (thesis_id,)
        for thesis in repo.fetch(conn, Thesis, where + " ORDER BY id", params):
            if deepdive.has_narrative(conn, thesis.id) and not force:
                print(f"thesis #{thesis.id} ({thesis.strategy_tag}) already"
                      " has a narrative — skipping (use --force to regenerate)")
                continue
            print(f"thesis #{thesis.id} ({thesis.strategy_tag}):"
                  " rehydrating …")
            bundle = deepdive.rehydrate(conn, thesis)
            ctx = context_from_bundle(bundle, thesis)
            print(f"  {len(ctx.claims)} claims, {len(ctx.metrics)} metrics,"
                  f" {len(ctx.macro)} macro series,"
                  f" {len(ctx.forecasts)} forecasts — narrating …")
            narrative, notes = reporter.narrate(
                conn, ctx, thesis, thesis.experiment_id,
                datetime.now(timezone.utc), sonnet_audit=sonnet_audit)
            for note in notes:
                print(f"  ⚠ {note}")
            if narrative is None:
                print("  narrative failed — report keeps deterministic layout")
            paths = deepdive.write(
                conn, thesis, week_key,
                packet.latest_doc_id(conn, thesis.strategy_tag))
            for p in paths:
                print(f"  wrote {p}")
            n += 1
    return n


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("week_key", help="ISO week, e.g. 2026-W30")
    parser.add_argument("--thesis", type=int, default=None)
    parser.add_argument("--force", action="store_true",
                        help="regenerate even if a narrative already exists")
    parser.add_argument("--no-audit", action="store_true",
                        help="skip the Sonnet audit gate")
    args = parser.parse_args()
    count = backfill(args.week_key, thesis_id=args.thesis, force=args.force,
                     sonnet_audit=not args.no_audit)
    print(f"backfilled {count} dive(s) for {args.week_key}")
