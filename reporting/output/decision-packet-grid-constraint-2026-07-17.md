# Decision Packet — grid-constraint: ETN, GEV, HUBB, PWR, NVT

*Generated 2026-07-17T13:26:40.143434+00:00 · thesis v2 (REJECTED) · experiment 9 · strategy_tag `grid-constraint`*

> **No orders are proposed in Stage 1.** This packet is the evidence-to-thesis
> vertical slice output; sizing and orders arrive with the Stage 2 simulator.

## Executive summary (LLM-written, JUDGMENT — adds no claims)

The thesis linking AI-driven data center expansion to grid modernization demand was rejected. The basket identified companies with high exposure scores, but the review highlighted insufficient evidence of pure data center focus, unverified forecasts ignoring seasonality, and unquantified data center-specific backlog. GEV's backlog includes non-data-center projects, and PWR's forecast lacks data center-specific validation. The verdict concluded evidence was insufficient for action.

## Causal chain

Data center expansion (AI-driven) increases electricity demand, straining grid infrastructure and creating demand for grid modernization solutions, which is captured by companies with high exposure to data center power and grid infrastructure segments.

## Exposure basket (ranked by measured exposure only — invariant 5)

| Rank | Symbol | Justification | Supporting assertions |
|---|---|---|---|
| 1 | GEV | GEV has the highest exposure_score (26.55) supported by high revenue_share (77.66%) and backlog_ratio (7.87) in electrification (A18, A19), strong electrification segment revenue ($2,959M) and backlog ($42,440M) (A10, A11), and robust EBITDA (A14, A15). | A9, A10, A11, A14, A15, A18, A19 |
| 2 | PWR | PWR has exposure_score (1.0) supported by high backlog ($48.47B) (A38), data center demand drivers (A29, A30, A36, A37), and infrastructure solutions for data centers (A28, A31). | A28, A29, A30, A31, A36, A37, A38 |
| 3 | NVT | NVT has exposure_score (0.75) supported by data center solutions (A44, A45, A47, A48) and segment revenue mix (72% Systems Protection for data centers) (A46). | A44, A45, A46, A47, A48 |
| 4 | ETN | ETN has exposure_score (0.34) supported by data center power enclosures (A4). | A4 |
| 5 | HUBB | HUBB has insufficient evidence for grid-constraint theme; exposure_score (0.15) is low and source facts (A21, A22, A23, A24) are general utility solutions without explicit data center grid constraint linkage. | A21, A22, A23, A24 |

### Measured exposure metrics

| Assertion | Symbol | Metric | Value | Function (version) |
|---|---|---|---|---|
| A5 | ETN | emphasis_total | 34 | `analytics.exposure.emphasis_count` (9d9e797) |
| A6 | ETN | exposure_score | 0.34 | `analytics.exposure.exposure_score` (9d9e797) |
| A17 | GEV | emphasis_total | 304 | `analytics.exposure.emphasis_count` (9d9e797) |
| A18 | GEV | revenue_share | 77.66404199475065616797900262 | `analytics.exposure.revenue_share` (9d9e797) |
| A19 | GEV | backlog_ratio | 7.874015748031496062992125984 | `analytics.exposure.backlog_ratio` (9d9e797) |
| A20 | GEV | exposure_score | 26.55468066491688538932633421 | `analytics.exposure.exposure_score` (9d9e797) |
| A26 | HUBB | emphasis_total | 15 | `analytics.exposure.emphasis_count` (9d9e797) |
| A27 | HUBB | exposure_score | 0.15 | `analytics.exposure.exposure_score` (9d9e797) |
| A41 | PWR | emphasis_total | 130 | `analytics.exposure.emphasis_count` (9d9e797) |
| A42 | PWR | exposure_score | 1 | `analytics.exposure.exposure_score` (9d9e797) |
| A49 | NVT | emphasis_total | 75 | `analytics.exposure.emphasis_count` (9d9e797) |
| A50 | NVT | exposure_score | 0.75 | `analytics.exposure.exposure_score` (9d9e797) |

## Valuation check

Insufficient data to assess valuation (no earnings, P/E, or price metrics provided).

## Forecasts (the measurement instrument)

| # | Statement | P | Horizon | Resolution criterion |
|---|---|---|---|---|
| 1 | GEV's electrification segment revenue for fiscal year 2026 will exceed $12.0 billion. | 0.85 | 2026-12-31 | GEV's 10-K for fiscal year 2026 (SEC filing) reporting electrification segment revenue > $12.0 billion. |
| 2 | PWR's total backlog as of December 31, 2026 will exceed $55.0 billion. | 0.60 | 2026-12-31 | PWR's 10-Q for Q4 2026 (SEC filing) reporting total backlog > $55.0 billion. |
| 3 | NVT's backlog as of December 31, 2026 will exceed $3.0 billion. | 0.80 | 2026-12-31 | NVT's 10-Q for Q4 2026 (SEC filing) reporting backlog > $3.0 billion. |
| 4 | PWR's data center-related revenue (from Quanta segment) will represent at least 40% of total revenue in fiscal year 2026. | 0.70 | 2026-12-31 | PWR's 10-K for fiscal year 2026 reporting data center-related revenue as a percentage of total revenue >= 40%. |
| 5 | NVT's Systems Protection segment revenue (for data centers) will exceed $1.0 billion in fiscal year 2026. | 0.75 | 2026-12-31 | NVT's 10-K for fiscal year 2026 reporting Systems Protection segment revenue > $1.0 billion. |
| 6 | HUBB's Utility Solutions segment backlog will exceed $1.0 billion as of December 31, 2026. | 0.40 | 2026-12-31 | HUBB's 10-Q for Q4 2026 (SEC filing) reporting Utility Solutions segment backlog > $1.0 billion. |

## Exit criteria (all four types, defined at creation)

- **PRICE_STOP**: Stock price falls below 80% of current price (2026-07-17) by 2027-01-01
- **CAUSAL_BREAK**: Data center demand growth rate (Gartner) falls below 5% YoY for two consecutive quarters by 2027-01-01
- **VALUATION_EXIT**: Insufficient data: no earnings or P/E provided in evidence
- **TIME_STOP**: 2027-01-01

## Independent review

**Verdict: REJECT**

### Challenges
- GEV's electrification segment backlog ($42.44B) is not exclusively data center-focused; evidence (A9) states 'demand for equipment to electrify data centers' but does not isolate data center-specific backlog, overestimating exposure purity (e.g., backlog likely includes commercial/industrial projects).
- GEV's 2026 revenue forecast ($12B) assumes linear Q1-to-full-year growth from $2.959B (A10), ignoring typical infrastructure seasonality (Q1 often weak due to weather), with no historical data provided to validate this assumption.
- PWR's backlog forecast ($55B) relies on total backlog growth ($48.47B as of Mar 2026, A38), but data center-specific backlog (A30, A31) is unquantified in evidence, making the forecast unverifiable against the thesis's causal chain.

### Is price driving the narrative? (reviewer-only quarantined metrics)

No price-derived metrics available; thesis not reverse-engineered from price action

### Do-nothing case

Evidence fails to confirm exposure purity (e.g., GEV's electrification segment includes non-data-center infrastructure), forecasts lack seasonality validation, and valuation is unassessable. 'Evidence insufficient' is the strongest case for inaction.

## Provenance appendix

Every assertion id used above resolves below. SOURCE_FACT → verbatim citation;
COMPUTED → named function + code version; JUDGMENT → labeled model opinion.

| Assertion | Type | Resolution |
|---|---|---|
| A4 | SOURCE_FACT | doc 13: “pre-integrated modular power enclosures for data center, industrial, utility and communications customers” |
| A5 | COMPUTED | `analytics.exposure.emphasis_count` @ 9d9e797 |
| A6 | COMPUTED | `analytics.exposure.exposure_score` @ 9d9e797 |
| A7 | SOURCE_FACT | doc 23: “total revenues were $38.1 billion” |
| A8 | SOURCE_FACT | doc 23: “Equipment revenues $ 20,934” |
| A9 | SOURCE_FACT | doc 23: “to electrify industries including data centers playing a key role in the development of artificial intelligence (AI)” |
| A10 | SOURCE_FACT | doc 20: “$ 2,959 $ 1,840” |
| A11 | SOURCE_FACT | doc 20: “$ 42,440 $ 34,242 $ 25,034” |
| A12 | SOURCE_FACT | doc 20: “$5.1 billion ( 5% )” |
| A13 | SOURCE_FACT | doc 20: “$0.3 billion ( 1% )” |
| A14 | SOURCE_FACT | doc 20: “$ 528 $ 205” |
| A15 | SOURCE_FACT | doc 20: “$ 811 $ 517” |
| A16 | SOURCE_FACT | doc 20: “$ (382) $ (146)” |
| A17 | COMPUTED | `analytics.exposure.emphasis_count` @ 9d9e797 |
| A18 | COMPUTED | `analytics.exposure.revenue_share` @ 9d9e797 |
| A19 | COMPUTED | `analytics.exposure.backlog_ratio` @ 9d9e797 |
| A20 | COMPUTED | `analytics.exposure.exposure_score` @ 9d9e797 |
| A21 | SOURCE_FACT | doc 31: “Utility Solutions $ 948.9 $ 857.1” |
| A22 | SOURCE_FACT | doc 31: “Electrical Solutions 567.8 508.1” |
| A23 | SOURCE_FACT | doc 31: “Operating Income: Utility Solutions $ 175.1 $ 150.8” |
| A24 | SOURCE_FACT | doc 31: “18.5 %” |
| A25 | SOURCE_FACT | doc 31: “backlog intangible assets are classified in Level 3 of the fair value hierarchy” |
| A26 | COMPUTED | `analytics.exposure.emphasis_count` @ 9d9e797 |
| A27 | COMPUTED | `analytics.exposure.exposure_score` @ 9d9e797 |
| A28 | SOURCE_FACT | doc 38: “Our Underground and Infrastructure segment provides comprehensive infrastructure solutions to customers involved in the transportation, distribution, storage, d” |
| A29 | SOURCE_FACT | doc 38: “increased demand for data center and other technology infrastructure and advanced manufacturing facilities” |
| A30 | SOURCE_FACT | doc 38: “the investment of capital into the build out of data centers by technology customers in order to, among other things, expand cloud-based services and develop ar” |
| A31 | SOURCE_FACT | doc 38: “Quanta is well positioned to provide turnkey infrastructure solutions for these facilities, such as critical-path low-voltage electrical infrastructure solution” |
| A32 | SOURCE_FACT | doc 38: “In particular, we believe there are opportunities to provide fiber and other services in and around data centers, and to interconnect data centers.” |
| A33 | SOURCE_FACT | doc 38: “Amounts represent capital committed for the expansion of certain manufacturing facilities and expansion of our equipment fleet.” |
| A34 | SOURCE_FACT | doc 38: “Backlog $ 25,866,287 $ 43,976,408” |
| A35 | SOURCE_FACT | doc 34: “utilities are continuing to invest significant capital in their electric power delivery systems through multi-year grid modernization and reliability programs, ” |
| A36 | SOURCE_FACT | doc 34: “increasing demand for electricity associated with, among other things, data centers and other technology-related dynamics” |
| A37 | SOURCE_FACT | doc 34: “strong demand for these services by data center, manufacturing, semiconductor and other large load facilities” |
| A38 | SOURCE_FACT | doc 34: “$48.47 billion” |
| A39 | SOURCE_FACT | doc 34: “The cost-effectiveness of solar, wind energy and battery storage, combined with a meaningful increase in current and forecasted electricity demand is continuing” |
| A40 | SOURCE_FACT | doc 34: “high demand for new and expanded transmission, substation and distribution infrastructure needed to reliably transport power.” |
| A41 | COMPUTED | `analytics.exposure.emphasis_count` @ 9d9e797 |
| A42 | COMPUTED | `analytics.exposure.exposure_score` @ 9d9e797 |
| A43 | SOURCE_FACT | doc 44: “The dollar amount of backlog as of December 31, 2025 was $2.3 billion.” |
| A44 | SOURCE_FACT | doc 44: “Our Systems Protection business provides innovative solutions to help protect electronics, systems and data in mission critical applications, including data cen” |
| A45 | SOURCE_FACT | doc 44: “The Electrical Products Group is a leading provider of infrastructure solutions, designed to help ensure safe and reliable electrical operations primarily in th” |
| A46 | SOURCE_FACT | doc 42: “Systems Protection and Electrical Connections, which represented approximately 72% and 28% of total revenues during the first three months of 2026, respectively” |
| A47 | SOURCE_FACT | doc 42: “including power utilities and data centers.” |
| A48 | SOURCE_FACT | doc 42: “including data centers” |
| A49 | COMPUTED | `analytics.exposure.emphasis_count` @ 9d9e797 |
| A50 | COMPUTED | `analytics.exposure.exposure_score` @ 9d9e797 |

---
*Model: `qwen3:30b-a3b@ad815644918f` · prompt 0945ea042f63… · all runs logged to `agent_runs`.*