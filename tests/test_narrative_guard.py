"""Pure unit tests for the narrative no-new-claims guard — no DB, no LLM."""

from types import SimpleNamespace

from reporting import narrative_guard as ng


def make_ctx(**over):
    base = dict(
        theme_title="Grid constraint",
        theme_description="T&D equipment crunch",
        verdict="ACTIVATE",
        causal_chain="Data center expansion drives grid modernization.",
        valuation_check="Insufficient data for P/E.",
        do_nothing_case="Backlog growth may be acquisitions.",
        challenges=["PWR backlog partially from acquisitions"],
        basket=[{"symbol": "PWR", "rank": 1,
                 "justification": "exposure_score=1 backlog $48.47B",
                 "supporting_assertion_ids": [305, 299]},
                {"symbol": "AVAV", "rank": 2, "justification": "score 0.79",
                 "supporting_assertion_ids": [1480]}],
        claims=[{"assertion_id": 299, "symbol": "PWR",
                 "claim": "Backlog was $48.47 billion as of March 31, 2026,"
                          " a 10.2% increase.",
                 "quote": "backlog were $26.24 billion and $48.47 billion",
                 "figure_name": "backlog_total", "figure_value": 48.47,
                 "figure_unit": "USD_billions"},
                {"assertion_id": 1478, "symbol": "AVAV",
                 "claim": "Funded backlog was $1,183.0 million.",
                 "quote": "funded backlog was approximately $1,183.0 million",
                 "figure_name": "funded_backlog", "figure_value": 1183.0,
                 "figure_unit": "USD_millions"}],
        metrics=[{"assertion_id": 305, "symbol": "PWR",
                  "metric": "exposure_score", "value": 1.0},
                 {"assertion_id": 1480, "symbol": "AVAV",
                  "metric": "exposure_score", "value": 0.79}],
        macro=[{"series_id": "IPUTIL", "n_obs": 12, "latest_date": "2026-05-01",
                "latest": 111.1, "min": 106.4, "max": 114.8,
                "assertion_ids": [324, 335]}],
        forecasts=[{"statement": "PWR backlog will reach $55 billion",
                    "resolution_criterion": "Q4 2026 backlog report",
                    "horizon_end": "2026-12-31",
                    "p_prior": 0.85, "p_final": 0.60}],
        milestones=[{"description": "capex stays elevated",
                     "falsification_criterion": "two guidance cuts"}],
        exit_criteria={"TIME_STOP": "2027-12-31"},
    )
    base.update(over)
    return SimpleNamespace(**base)


ALLOW = ng.build_allowlist(make_ctx())
KNOWN = {"PWR", "AVAV", "NOC", "ETN", "GEV"}


def check(text, section="thesis_narrative", **kw):
    kw.setdefault("allow", ALLOW)
    kw.setdefault("known_symbols", KNOWN)
    return ng.check_text(section, text, **kw)


def kinds(violations):
    return {v.kind for v in violations}


# ---- numeric acceptance ---------------------------------------------------- #

def test_exact_restatement_passes():
    assert not check("PWR's backlog stands at $48.47 billion. [^A299]")


def test_rounding_to_fewer_digits_passes():
    # 1183 USD_millions cited as $1.2bn
    assert not check("AVAV's funded backlog is about $1.2bn. [^A1478]")


def test_unknown_number_fails():
    v = check("The company guided to $2.71 billion next year. [^A299]")
    assert kinds(v) == {"UNKNOWN_NUMBER"}


def test_adding_precision_fails():
    # exposure_score is 0.79 (2 sig digits); 0.786 adds precision
    v = check("AVAV scores 0.786 on exposure. [^A1480]")
    assert kinds(v) == {"UNKNOWN_NUMBER"}


def test_percent_form_of_probability_passes():
    assert not check("The desk puts 85% on the backlog forecast,"
                     " revised to 60%. [^A299]")


def test_commas_and_dollar_normalization():
    assert not check("Funded backlog reached $1,183.0 million. [^A1478]")


def test_year_and_small_int_exemptions():
    assert not check("By 2027 the two segments across 12 months"
                     " should converge. [^A299]")


def test_duration_int_exempt():
    assert not check("Backlog covers roughly 24 months of work. [^A299]")


# ---- footnotes ------------------------------------------------------------- #

def test_unknown_footnote_id_fails():
    v = check("Backlog is $48.47 billion. [^A999999]")
    assert "UNKNOWN_FOOTNOTE" in kinds(v)


def test_missing_footnote_fails_with_suggestions():
    v = check("A fine paragraph with no citations at all.",
              suggest_ids=[299, 305])
    assert "MISSING_FOOTNOTE" in kinds(v)
    assert "A299" in ng.format_critique(v)


def test_lead_exempt_from_marker_requirement():
    assert not check("Verdict first, thesis second.", section="lead",
                     require_marker=False)


# ---- symbols --------------------------------------------------------------- #

def test_foreign_known_ticker_fails():
    v = check("NOC shows a similar pattern. [^A299]")
    assert "FOREIGN_SYMBOL" in kinds(v)


def test_non_ticker_caps_token_passes():
    assert not check("The AI and MRO demand cycle is real. [^A299]")


# ---- length ---------------------------------------------------------------- #

def test_under_length_fires_and_names_uncited_ids():
    v = check("Too short. [^A299]", target_key="company",
              suggest_ids=[299, 1478])
    under = [x for x in v if x.kind == "UNDER_LENGTH"]
    assert under and "A1478" in under[0].detail
    assert "A299" not in under[0].detail  # already cited


def test_length_floor_satisfied():
    text = ("PWR anchors the basket. " * 80) + "[^A299]"
    assert not check(text, target_key="company", suggest_ids=[299])


def test_body_word_count_excludes_markers():
    assert ng.body_word_count("one two [^A299] three") == 3


# ---- marker normalization -------------------------------------------------- #

def test_normalize_marker_variants():
    ids = {299, 305}
    out = ng.normalize_markers(
        "See A299 and [A305] and (A299) and [^A305].", ids)
    assert out == "See [^A299] and [^A305] and [^A299] and [^A305]."


def test_normalize_leaves_unknown_ids_alone():
    assert ng.normalize_markers("A777 stays bare.", {299}) == "A777 stays bare."


def test_normalize_compound_citation_groups():
    ids = {12, 34}
    assert (ng.normalize_markers("Backlog grew. [^A12, ^A34]", ids)
            == "Backlog grew. [^A12][^A34]")
    assert (ng.normalize_markers("Backlog grew. [A12; A34]", ids)
            == "Backlog grew. [^A12][^A34]")
    assert (ng.normalize_markers("Backlog grew. (A12, A34)", ids)
            == "Backlog grew. [^A12][^A34]")
    assert ng.normalize_markers("Backlog grew. ^A12", ids) \
        == "Backlog grew. [^A12]"


def test_prose_quoted_number_truncated_passes():
    """W30 regression: the reviewer's prose said volatility 0.737831; the
    narrator wrote 0.737 (truncation, not rounding) — must pass."""
    ctx = make_ctx(challenges=["AVAV volatility 0.737831 is elevated"])
    allow = ng.build_allowlist(ctx)
    assert not check("AVAV's volatility of 0.737 is elevated. [^A299]",
                     allow=allow)
    assert not check("Volatility near 0.74 remains high. [^A299]",
                     allow=allow)  # rounded form passes too
    v = check("Volatility of 0.736 is elevated. [^A299]", allow=allow)
    assert kinds(v) == {"UNKNOWN_NUMBER"}  # neither rounding nor truncation


def test_identifier_digits_are_not_numeric_claims():
    # a bare unknown id (or Q4-style token) must not fire UNKNOWN_NUMBER —
    # citation correctness is the footnote checks' and Sonnet's job
    v = check("As noted in A999999 during Q4 the story holds. [^A299]")
    assert "UNKNOWN_NUMBER" not in kinds(v)
