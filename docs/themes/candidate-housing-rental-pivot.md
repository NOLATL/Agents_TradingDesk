# THEME (CANDIDATE 2): Housing Glut → Build-to-Rent Pivot

*Status: Stage 0 draft (experiment input). Source inspiration: Agents_Economics
concept + deep-dive `housing-glut-rental-pivot`. Same caveats as the starter
theme. Selected deliberately as a non-power theme: it diversifies the candidate
set away from the AI-power cluster, and the source research found significant
DISCONFIRMING evidence — drafting it anyway, with honest priors, is exactly the
kind of hypothesis the genealogy table exists to track.*

- **strategy_tag:** `housing-rental-pivot`
- **Contamination assessment:** Forward-unresolved. Whether capital structurally
  rotates from for-sale construction to rental operation at scale resolves over
  2026–2028; the disconfirming BTR-starts data (2025–early 2026) is inside the
  model's likely knowledge window and must be re-derived from sources, not
  assumed.

## 1. Causal chain

Mortgage rates ~6.5% + record new-home inventory → for-sale affordability gap →
builders defend volume with double-digit incentive spend → retail construction
margins compress toward the point where bulk disposition to rental operators
competes with retail sale → capital reallocates toward rental product (SFR
REIT acquisitions, purpose-built rental) → rental operators with construction or
bulk-acquisition capability grow NOI-producing stock at discounted basis →
for-sale-only builders' margins stay structurally lower while rental-exposed
names' income share grows. Known counter-current (from the source research):
BTR *single-family* starts were shrinking through early 2026 while multifamily
rental grew — the chain may be resolving toward apartments, not SFR, which
forecast 5 tests directly.

## 2. Forecasts (draft — 7 resolvable statements)

| # | Statement | Prior | Horizon | Resolution criterion |
|---|---|---|---|---|
| 1 | New single-family home months-of-supply (Census/HUD) remains ≥7.0 at horizon | 0.60 | 2027-06-30 | Census new-home inventory report, latest before horizon |
| 2 | 30-year fixed mortgage rate (Freddie Mac PMMS) does not sustain <6.0% for any 8 consecutive weeks before horizon | 0.65 | 2027-12-31 | PMMS weekly series |
| 3 | The two largest S&P 500 builders' reported incentive spend remains ≥10% of average sales price in their latest quarterly disclosure before horizon | 0.60 | 2027-06-30 | Incentive %/ASP figures in filings/calls, extracted with citations |
| 4 | The largest builder's rental/bulk segment revenue grows ≥15% YoY in its FY2027 annual filing | 0.45 | 2028-03-31 | Segment reporting in the 10-K |
| 5 | Census-reported single-family built-for-rent starts for calendar 2027 exceed calendar 2026 (the pivot actually shows up in SFR, not just apartments) | 0.40 | 2028-06-30 | Census/NAHB SFBFR quarterly series, full-year sum |
| 6 | S&P 500 SFR REITs' aggregate net acquisitions (acquisitions minus dispositions, units) for calendar 2027 exceed calendar 2026 | 0.50 | 2028-06-30 | REIT quarterly supplements, extracted with citations |
| 7 | Existing-home sales (NAR annual) for 2027 remain below 4.5M SAAR annual average (the frozen-market condition persists) | 0.55 | 2028-03-31 | NAR published annual figure, first vintage |

Priors are deliberately near coin-flip — the source research is genuinely mixed,
and this theme's main value to the experiment may be calibration data plus a
high-quality abstention.

## 3. Exposure basket — approach

Candidate pool: S&P 500 homebuilders, residential REITs, and building-products
names. Ranking criteria (measured only):
- % of revenue / pre-tax income from rental operations or bulk disposition
  (segment reporting)
- Owned/controlled lot pipeline structure (option share — capital flexibility to
  pivot)
- For REITs: units owned, net acquisition rate, development pipeline share of
  new supply
- Management emphasis on BTR/bulk strategy in transcripts (counted, cited)
- Incentive spend trajectory as a *negative* exposure metric for for-sale-only
  names

Barred: price history, momentum, price targets. Long/short expression is out of
scope (long-only desk): the thesis must be expressible as long rental-exposed
names, or not at all.

## 4. Valuation check — plan

Builders at trough multiples may already price margin compression; SFR REITs may
already price rental scarcity. Stage 1 computes P/B and P/E percentiles for
builders vs. own history, and implied cap rate / AFFO multiple percentiles for
REITs vs. own history and multifamily peers. The asymmetry to verify: is the
market pricing the pivot as temporary (builders cheap, REITs normal) or
structural (builders cheap, REITs rich)? Only the former supports a position.

## 5. Exit criteria

- **PRICE_STOP:** adverse move beyond config stop, or idiosyncratic single-name
  event (e.g. a REIT governance issue). Trade closes; forecasts keep scoring.
- **CAUSAL_BREAK:** PMMS sustains <6.0% for 8+ consecutive weeks AND
  existing-home sales print two consecutive quarters annualizing >5.0M (the
  frozen market thaws — the affordability premise dies); OR new-home
  months-of-supply falls below 5.0 for two consecutive monthly reports.
  Basket liquidates; thesis invalidated.
- **VALUATION_EXIT:** rental-exposed basket reaches config valuation ceiling
  while supply/incentive milestones remain confirmed. Close at profit; keep
  scoring.
- **TIME_STOP:** 2028-06-30.

## 6. Do-nothing case (seed for the Independent Reviewer)

The strongest current evidence (BTR starts down 19% in 2025, accelerating
declines into 2026; SFR rent growth ~1%; operators repositioning development
arms as fee builders) says the pivot is NOT happening in single-family scale —
capital is choosing apartments. The reviewer should treat "the thesis's own
sources disconfirm it" as the leading candidate conclusion; a tracked abstention
with forecasts still scoring may be this theme's best outcome for the
experiment.
