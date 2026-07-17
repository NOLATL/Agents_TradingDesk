# THEME (STARTER): The Grid Constraint — AI datacenter power demand vs. grid expansion capacity

*Status: Stage 0 draft (experiment input). Source inspiration: Agents_Economics
hypotheses `grid-equipment-crunch`, `grid-power-rerate`, `btm-bridge-power`.
Every factual claim below must be re-derived with SOURCE_FACT provenance by
Stage 1 evidence tooling before it can support any decision. Probabilities are
draft priors; the Research Analyst re-estimates them from the blinded evidence
packet at thesis creation. The source notes reference price action — none of
that is carried into this document's basket approach (§7 blinding).*

- **strategy_tag:** `grid-constraint`
- **Contamination assessment:** Forward-unresolved. Whether the equipment/labor
  crunch persists through 2027–2028 is genuinely undecided; the model's training
  data knows the 2024–25 narrative, so forecasts are deliberately anchored to
  post-cutoff resolution dates and measurable quantities.

## 1. Causal chain

AI training/inference capex → hyperscaler datacenter construction → interconnection
requests and firm power demand growth → grid expansion requirement (generation,
transmission, distribution) → demand for large power transformers, HV switchgear,
cable, and skilled T&D labor → multi-year equipment backlogs and utility capex
growth → revenue and backlog growth at electrical-equipment makers and T&D
contractors. Workaround branch: behind-the-meter generation (fuel cells, on-site
gas, hydro) routes *around the grid* but still consumes transformers, switchgear,
and electricians — the equipment/labor link is the workaround-resistant segment of
the chain, which is why this theme centers on it rather than on scarcity value of
existing generation.

## 2. Forecasts (draft — 8 resolvable statements)

| # | Statement | Prior | Horizon | Resolution criterion |
|---|---|---|---|---|
| 1 | Mean US large-power-transformer lead time remains ≥120 weeks in the latest industry/DOE survey published before horizon | 0.70 | 2027-12-31 | Most recent published lead-time survey (DOE/NEMA/Wood Mackenzie) as of horizon date |
| 2 | Aggregate next-FY capex guidance of MSFT+GOOGL+AMZN+META, as stated in filings/calls, is ≥ their current-FY aggregate guidance | 0.75 | 2027-06-30 | Sum of guided capex in the four issuers' most recent filings before horizon |
| 3 | EIA-reported US total electricity consumption for calendar 2026 grows ≥1.5% YoY | 0.65 | 2027-03-31 | EIA Electric Power Monthly annual figure, first vintage published |
| 4 | The next PJM base residual auction clears at or above the prior auction's clearing price | 0.60 | 2027-12-31 | PJM published auction results |
| 5 | Aggregate reported backlog of ≥3 S&P 500 electrical-equipment issuers (identified in Stage 1 by segment revenue) grows YoY in their FY2026 annual filings | 0.70 | 2027-06-30 | Backlog figures in 10-K filings, as extracted with citations |
| 6 | Aggregate 2027 capex guidance of the 10 largest S&P 500 utilities (by rate base, from filings) exceeds their 2026 guidance | 0.70 | 2027-06-30 | Capex guidance in filings/investor decks, extracted with citations |
| 7 | Ratepayer-protection or data-center cost-allocation measures are enacted (not proposed) in ≥3 US states | 0.45 | 2028-06-30 | State legislative/PUC records; counts enacted statutes or final PUC orders |
| 8 | Announced new US large-transformer manufacturing capacity reaching commercial production before horizon (the supply response that ends the crunch) | 0.30 | 2028-06-30 | Company announcements/filings confirming first commercial shipment from a new US plant |

Forecasts 7 and 8 are deliberately adversarial to the theme — they score the
risks, not the hope.

## 3. Exposure basket — approach (ranked names require Stage 1 blinded tooling)

Candidate pool: S&P 500 members whose filings show measurable exposure to grid
equipment, T&D services, or electrical infrastructure inputs. Ranking criteria
(exclusively measured-exposure metrics, per §7):
- % of revenue from electrical equipment / grid segments (10-K segment reporting)
- Reported backlog and book-to-bill in grid-related segments
- Segment operating profit share from grid/electrical businesses
- Management emphasis: frequency and forward-commitment language on datacenter/grid
  demand in transcripts (measured, not vibes — counted mentions with citations)
- Capex committed to grid-related capacity expansion

Explicitly barred from ranking: any historical price performance, momentum, or
analyst price targets. The source hypothesis's ticker list is NOT carried over;
Stage 1 re-derives the basket from filings alone.

## 4. Valuation check — plan

Right theme, wrong price is the expected failure mode here: this is a consensus
theme and the sector re-rated hard in 2024–25. Stage 1 valuation module computes,
per candidate: EV/EBITDA and P/E vs. the name's own 5-year distribution and vs.
sector peers (deterministic percentile, COMPUTED provenance). Rule of thumb to
formalize: a candidate in the top decile of its own 5-year multiple range needs
either accelerating backlog growth in filings or is down-weighted; the theme is
investable only if the basket's median valuation percentile is below a threshold
set in config before any position is proposed.

## 5. Exit criteria (all four, defined at creation)

- **PRICE_STOP** (trade dies, thesis lives, forecasts keep scoring): a position
  closes on an adverse move beyond the config stop threshold from entry, or on
  idiosyncratic single-name news unrelated to the causal chain (e.g. an
  accounting issue at one contractor).
- **CAUSAL_BREAK** (thesis dies, basket liquidates, forecasts resolve per
  criterion): a published lead-time survey shows large-transformer lead times
  ≤60 weeks (normalization); OR ≥2 of the four hyperscalers cut current-FY capex
  guidance by ≥20% in filings; OR aggregate equipment-maker backlog declines YoY
  in two consecutive quarterly filing cycles.
- **VALUATION_EXIT** (profit taken, thesis lives, forecasts keep scoring): the
  basket's deterministic valuation percentile exceeds the config ceiling while
  milestones remain confirmed — the thesis is right and priced in.
- **TIME_STOP** (horizon hit): 2028-06-30; unresolved forecasts marked
  UNRESOLVED, positions closed, thesis closed.

## 6. Do-nothing case (to be completed by the Independent Reviewer)

Seed for the reviewer: this is the most consensus theme in the candidate set.
The market has had two years to price the crunch; multiples across the equipment
complex embed multi-year persistence. The do-nothing case is that entry at
current valuations buys the thesis at its point of maximum agreement, where even
a correct causal chain produces flat-to-negative returns as lead times normalize
on schedule. "Evidence insufficient at these prices, take no position" is a
valid, tracked, rewarded output.
