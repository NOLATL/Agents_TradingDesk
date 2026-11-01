# Monitoring plan: the AI-bubble unwind — "when do I de-risk?"

**Provenance: HUMAN (Jared's personal portfolio-monitoring frame, drafted
with Claude Code 2026-11-01).** This is a reference for the human. It is
not desk evidence: the four LLM roles never read it (EXP-001 freeze), and
nothing in it feeds a desk calculation or order. Companion to
`theme-ai-capex-bubble.md` (the Aug-2026 timing-gap packet). That packet
holds the thesis, numbers and dated forecasts. This file holds the
**monitoring system**: what to watch, where to get it, what counts as a
break, and how signals turn into pre-committed actions.

---

## 0. The premise, made precise

"AI is real tech, but the buildout is a bubble" means one specific thing:
**cash obligations land years before the cash revenue that's meant to pay
for them.** The obligations are capex, leases, debt service and short-lived
GPU depreciation. Until the revenue arrives, the gap gets bridged with
outside money. Calling that money "free" is loose, since policy rates aren't
zero. What has been abundant is *cheap risk capital*:

| Funding source | Why it's been abundant | What ends it |
|---|---|---|
| Hyperscaler operating cash flow | Ad/cloud monopolies | Capex hits ~100% of OCF, so the marginal dollar must come from outside |
| Equity (high multiples, record raises, IPOs) | Narrative + index concentration | Multiple compression; a weak IPO (OpenAI S-1 filed June 2026) |
| Private credit / insurance balance sheets | Annuity inflows chasing yield | Losses, redemption gates, regulator pressure |
| ABS/CMBS + off-balance-sheet SPVs ("shadow borrowing") | Investment-grade tenant leases | Spread widening, rating actions, tenant walk-aways |
| Vendor / circular financing (NVDA→labs→clouds→NVDA) | Supplier cash piles | Supplier stock falls, so it stops funding customers |
| Sovereign / strategic (Gulf SWFs, SoftBank) | Oil revenue, ARM collateral | Oil price, SoftBank margin-loan stress |

**The thesis breaks in your favor (no bust)** if monetization grows into the
capex before outside funding reprices. **It breaks against the market** if
outside funding reprices first. The whole plan watches that race.

---

## 1. The chain of events: what has to break, in order

Booms like this don't need demand to *collapse*. Supplier revenue depends on
the *growth rate* of capex, not its level. So capex only has to **stop
accelerating** for the most valuable stocks in the index to de-rate. Cisco
peaked in March 2000 while its revenue kept growing into 2001. **The market
tops before the fundamentals confirm.** That's why the plan weights market
and credit signals as heavily as fundamentals.

```
 [A] FUNDING REGIME ──► [B] CAPITAL INTO AI ──► [C] HYPERSCALER CAPEX ──► [D] FINANCING PLUMBING
  rates, term premium     lab rounds, IPOs,       guides, capex/OCF,        private credit, ABS,
  credit spreads,         SWF/SoftBank,           debt+equity raises,       SPVs, BDCs, insurers
  liquidity               vendor financing        off-B/S leases
        ▲                                                │
        │                                                ▼
 [I] MARKET STRUCTURE ◄── [H] ACCOUNTING ◄── [G] MONETIZATION ◄── [F] COMPUTE PRICING ◄── [E] PHYSICAL BUILD
  concentration,           useful lives,       lab revenue/margin,   GPU rental rates,       power queues,
  leverage, breadth,       RPO quality,        cloud AI growth,      token prices,           permits, leases,
  reaction to news         round-tripping      enterprise ROI        secondary GPU prices    cancellations
```

It's a **loop**, not a line. Weak monetization [G] raises the cost of
capital in [B]/[D], which forces capex cuts in [C], which cut supplier
revenue, which compresses multiples in [I], which shuts the equity window in
[B]. Any link can start it. The five plausible ignition paths, roughly from
most to least likely:

1. **Monetization disappointment (G→B).** Lab revenue or cloud AI growth
   slows while capex keeps rising. Lab fundraising gets harder, a round is
   flat/down, or the IPO trades poorly. Then the circular financing
   unwinds.
2. **Funding shock from outside AI (A→D).** Inflation or term premium
   re-accelerates, a private-credit loss event happens in an unrelated
   sector, or a Japan carry unwind hits. Spreads widen for everyone, and the
   leveraged periphery (Oracle, CoreWeave, neoclouds, SPVs) can't refinance.
3. **A periphery credit event (D→C).** A neocloud, data-center developer or
   SPV defaults or draws emergency financing. Oracle takes a second
   downgrade into junk. That forces repricing of every similar structure.
4. **Efficiency shock (F→C).** A DeepSeek-style step change in
   compute-per-unit-of-value. Overcapacity shows up as falling *reserved*
   GPU rates, not just spot rates.
5. **Accounting reckoning (H→I).** Useful lives get shortened, an
   impairment hits, or a restatement / SEC comment-letter wave lands.
   Earnings get rebased lower, and multiples follow.

**Physical constraints (E) cut both ways.** Permit and power delays postpone
revenue and **widen** the timing gap: more debt is carried for longer before
cash arrives. Lease *cancellations* and falling pre-leasing are the
**demand-side** warning. Read them as opposites.

---

## 2. What to evaluate, link by link (indicators → sources → break signal)

Thresholds are **relative** to your own baseline. First task (§5): record a
baseline reading for each line, starting from the Q3-2026 earnings that just
printed.

### [A] Funding regime ("the free-money era")
| Indicator | Source | Break signal |
|---|---|---|
| HY and IG OAS | FRED `BAMLH0A0HYM2`, `BAMLC0A0CM` | HY ≥100bp off its 6-mo low; **widening while S&P makes highs** (divergence) |
| 10y real yield, 10y nominal, term premium | FRED `DFII10`, `DGS10`; NY Fed ACM term premium | Real 10y breaks to new cycle high; term premium spike on supply/deficit news |
| Financial conditions | FRED `NFCI` (Chicago Fed) | Turns from loosening to tightening for 4+ weeks |
| Fed path / balance sheet | FOMC statements, SEP dots; FRED `WALCL`, `RRPONTSYD` | Market-implied cuts priced out; hikes back on the table (inflation re-accelerating) |
| Bank credit standards | Fed SLOOS (quarterly) | Net tightening to C&I / CRE |
| Systemic-risk read | Fed Financial Stability Report (semiannual), IMF GFSR, BIS Quarterly Review, OFR | Explicit AI/private-credit escalation language (all three flagged it in 2026) |
| Japan / carry | BoJ decisions, USDJPY | Sharp yen rally + JGB yield jump (Aug-2024 pattern) |

### [B] Capital flowing into AI
| Indicator | Source | Break signal |
|---|---|---|
| Frontier-lab rounds (size, valuation, cadence) | The Information, Bloomberg, Reuters; lab announcements | Round delayed, downsized, flat/down; lead investor drops out |
| OpenAI (and any lab) IPO | EDGAR S-1/A amendments, pricing, first-month trading | IPO pulled, priced below range, or trades below issue |
| Vendor financing | NVDA 10-Q (investments in customers, guarantees), lab/cloud press | Suppliers *stop* funding customers, or "commitments" get re-labeled as LOIs (already happened with NVDA–OpenAI $100B) |
| SoftBank / Gulf capacity | SoftBank results, ARM-backed margin loan disclosures, oil price | SoftBank asset sales to meet commitments; Brent sustained drop |
| AI IPO / SPAC froth | Renaissance Capital IPO data | First-day pops shrinking, then deals pulled |

### [C] Hyperscaler capex and balance sheets
| Indicator | Source | Break signal |
|---|---|---|
| Capex guide (MSFT, GOOGL, AMZN, META, ORCL) | Earnings calls/10-Qs (late Jan/Apr/Jul/Oct) | **Any top-4 guide cut** = red. *Next-year growth guided below this year's* = yellow (2nd derivative) |
| Capex / operating cash flow; FCF | 10-Q cash-flow statements | >100% at more names; FCF negative beyond ORCL |
| Debt + equity raised for AI | 8-Ks, bond issuance (Bloomberg/FT), EDGAR 424B | Accelerating raises at worse terms; even the strongest names issuing equity (Alphabet's $84.75B raise, June 2026, was the first tell) |
| Off-balance-sheet lease commitments | 10-K lease footnote ("leases not yet commenced"); Moody's | Keeps compounding faster than revenue (Moody's: $1.2T, $820B not yet commenced, July 2026) |
| Ratings | S&P/Moody's/Fitch press releases | **Oracle to BB+/Ba1** (packet forecast #2); outlook-negative at any mega-cap |

### [D] Financing plumbing (private credit, ABS, insurers)
| Indicator | Source | Break signal |
|---|---|---|
| Data-center ABS/CMBS new-issue spreads | KBRA/Fitch/Moody's presale reports, Barclays/Green Street (via press) | ≥150bp wider than mid-2026 for a quarter (packet forecast #4) |
| Neocloud debt (CoreWeave DDTLs, bonds) | CRWV 10-Q/8-K, bond yields (FINRA TRACE) | New-facility coupons stepping up; tranche downgrades; emergency draws |
| Oracle credit | ORCL CDS / bond spreads (press, TRACE) | Spread widening faster than the IG index |
| Private-credit defaults | Fitch U.S. Private Credit Default Rate, Proskauer Private Credit Default Index, KBRA DLD | Rising for 2+ consecutive readings |
| BDC health | BDC 10-Qs (non-accruals, PIK share); BDC price/NAV discounts | Non-accruals ≥5% anywhere with DC/neocloud exposure (forecast #7); discounts widening sector-wide |
| **Non-traded BDC / interval-fund redemptions** | EDGAR tender-offer filings (`SC TO-I`) for the big retail private-credit funds | **Redemption requests exceeding the ~5% quarterly cap → gating.** One of the clearest "money is leaving" tells |
| Alt-manager equity as proxy | OWL, APO, ARES, BX, KKR prices | Underperforming financials during a period of AI-equity strength |
| Bank exposure to NDFIs | Fed H.8, bank 10-Qs | Banks pulling back lending to non-bank lenders |

### [E] Physical build (power, permits, construction)
| Indicator | Source | Break signal |
|---|---|---|
| Data-center construction spending | Census Value of Construction Put in Place (data-center line, monthly) | YoY growth rolls over |
| Vacancy, pre-leasing, absorption | CBRE NA Data Center Trends (semiannual), JLL, Cushman & Wakefield, datacenterHawk | Pre-leasing share falls; vacancy rises from record lows |
| **Lease cancellations / walk-aways** | Sell-side channel checks via press (TD Cowen's early-2025 MSFT notes were the template), developer 8-Ks | Hyperscaler lets LOIs lapse or walks from sites = demand-side red |
| Interconnection queues & large-load pipelines | PJM capacity auction results, ERCOT large-load interconnection reports, LBNL "Queued Up"; utility earnings calls (AEP, Dominion, Southern, Entergy) | Utilities *trimming* large-load pipelines or developers withdrawing requests |
| Local permits / opposition / moratoria | County zoning agendas, Data Center Watch (local-opposition tracker), state PUC large-load tariff dockets | Spreading moratoria = timing gap widens (delay, not demand loss) |
| Long-lead equipment | GE Vernova turbine backlog, transformer lead times, Vertiv/Eaton/Schneider order backlogs | Orders/backlog decelerate (book-to-bill < 1) |
| Electricity-price backlash | EIA retail price data, state elections, PUC rate cases | Politicians campaign on data-center power costs; tariff riders shift cost to DCs |

### [F] Compute pricing (overcapacity shows up here first)
| Indicator | Source | Break signal |
|---|---|---|
| GPU rental rates, **reserved** (not just spot) | Silicon Data H100/B200 rental index, SemiAnalysis, neocloud pricing pages | Reserved 1-yr rates fall ≥20% from peak (in 2026 they *rose*; that's the steelman to watch fade) |
| Secondary GPU prices | Broker listings, press | Accelerating decline for current-gen parts |
| Token prices vs. usage | Lab price lists, OpenRouter public usage stats, Epoch AI | Prices collapse *without* matching usage growth |
| Supplier high-frequency data | **TSMC monthly revenue (~10th of each month)**; Korea customs exports, first 10/20 days (semis); Taiwan export orders; SK Hynix/Micron HBM commentary | 3-month avg YoY decelerating 3 months running |
| NVDA working capital | NVDA 10-Q: receivables days, inventory, purchase obligations | DSO creeping up + inventory build = channel stuffing / customers paying slower |

### [G] Monetization (the thing that has to arrive)
| Indicator | Source | Break signal |
|---|---|---|
| Lab revenue, gross margin, burn | S-1 (OpenAI), The Information, lab statements | Revenue growth slows while burn rises; gross margin stalls (OpenAI ~33% vs 46% target) |
| Cloud growth vs capex | Azure / Google Cloud / AWS growth rates; MSFT "AI run-rate" disclosures | Cloud growth decelerates 2 quarters while capex accelerates |
| RPO conversion | MSFT/ORCL 10-Q RPO and "% recognized in 12 months" | RPO growth decelerates (MSFT <20% YoY = forecast #9); single-customer concentration rises |
| Enterprise adoption | **Census Business Trends & Outlook Survey (AI-use rate, biweekly)**; Ramp AI Index; Menlo/a16z enterprise surveys; MIT NANDA successors | Adoption plateaus; pilots-to-production stays ~5% |
| Software seat economics | Earnings of CRM, NOW, ADBE, WDAY (AI SKUs, seat counts, pricing) | AI add-ons cannibalize seats rather than expanding revenue |
| Productivity | BLS productivity & costs (quarterly) | No acceleration years into deployment |
| Labor channel (a separate bust path) | Challenger layoffs citing AI, JOLTS | AI-driven layoffs big enough to dent consumption |

### [H] Accounting quality ("the trickery")
| What to look for | Where | Red flag |
|---|---|---|
| Server/GPU useful-life changes | 10-K PP&E / depreciation footnotes (MSFT, GOOGL, META, AMZN, ORCL, CRWV) | Lives *extended*, or held at 5-6 yrs against a ~2-yr GPU cycle (Burry: ~$176B understated D&A 2026-28; that's an opinion, treat it as one) |
| Depreciation / capex ratio | Cash-flow statements | D&A lagging far behind capex = future earnings drag being deferred |
| Off-B/S structures | Lease footnotes, VIE/equity-method disclosures (e.g., Meta–Blue Owl Hyperion 80/20 JV, residual-value guarantees) | New SPVs, guarantees, or residual-value commitments |
| Round-tripping / circular revenue | Related-party notes; NVDA investments in customers; MSFT–OpenAI revenue share; CRWV customer concentration | Revenue from entities the seller funds; "AI revenue" definitions quietly changing |
| RPO / backlog quality | 10-Q RPO notes | Backlog from non-investment-grade counterparties (OpenAI), long duration, cancellable terms |
| Non-GAAP drift | Earnings releases vs 10-Q | Growing adjustments (SBC, "restructuring," capitalized software) |
| Regulator / auditor tells | **EDGAR `CORRESP`/`UPLOAD` (SEC comment letters)**, 8-K Item 4.01 (auditor change), **Item 4.02 (non-reliance/restatement)**, PCAOB inspection reports | Comment letters on useful lives/RPO/revenue recognition; any 4.02 at an AI name = red |
| Forensic / short research | Burry, Chanos commentary, short-seller reports, Damodaran (NYU) | Treat as hypotheses to verify in filings, never as fact |

### [I] Market structure (where the damage happens)
| Indicator | Source | Break signal |
|---|---|---|
| Concentration | Top-10 weight of S&P 500 (S&P/press); RSP vs SPY (equal- vs cap-weight) | Leadership narrows further, then leaders break first |
| Reaction to good news | NVDA/hyperscaler earnings-day moves | **Beat-and-raise → stock falls.** Classic late-cycle tell (AVGO fell ~15% on an *unraised* >$100B guide in 2026) |
| Leverage | FINRA margin statistics (monthly); leveraged single-stock ETF AUM; options volume (OCC) | Margin debt YoY at extremes, then rolling over |
| Valuation | Forward P/E, Shiller CAPE, equity risk premium (Damodaran monthly) | ERP near zero / negative vs real yields |
| Insider behavior | Form 4 filings (EDGAR, openinsider) | Clustered selling by AI-company insiders beyond 10b5-1 routine |
| Trend | S&P 500 / SOX vs 200-day MA | Used only as *confirmation* (§3), never alone |

---

## 3. Turning signals into decisions (pre-commit now, while calm)

Market tops can't be timed precisely. The goal is to **de-risk in stages on
evidence, with rules written before the stress hits**, so the decision
isn't made in a panic or talked out of during a rally. Going all-in or
all-out on one signal is the usual mistake in both directions. Being early
costs real returns: the packet's base case is a **2-3 year slow deflation,
not a 2000-style pop** (forecast #8, 55%).

**Scoring.** Each of the nine links [A]–[I] gets Green / Yellow / Red at
each monthly review. Reds count only when they're in *different* links. Two
reds inside [D] are one signal, not two.

| Stage | Trigger (your rule, edit the actions) | Action template |
|---|---|---|
| **0 — Watch** | ≤1 red link | Hold; keep the review cadence |
| **1 — Trim** | 2 red links, **or** any single "hard" red (below) | De-risk tranche 1 (you set the %): cut AI-concentrated exposure first |
| **2 — Defend** | 3+ red links, **or** credit [A/D] **and** capex [C] both red | Tranche 2 |
| **3 — Out** | Stage 2 **plus** market confirmation: S&P and SOX below 200-day MA, and HY OAS widening | Final tranche to your floor allocation |
| **Re-entry** | Reds clear for 2 consecutive reviews, or a ≥30% drawdown with spreads stabilizing | Reverse in the same tranches, so you don't miss the recovery |

**Hard reds (any one triggers Stage 1 on its own):**
- A top-4 hyperscaler cuts its capex guide.
- Oracle downgraded to junk at S&P or Moody's.
- A neocloud / DC-developer / Stargate-SPV default, restructuring or emergency financing.
- Retail private-credit fund gates redemptions.
- Item 4.02 restatement or impairment wave at an AI-infrastructure name.
- OpenAI IPO pulled or broken below issue.

**Things to think through before acting** (decisions for you, not advice):
- **A cap-weighted index fund is the AI trade.** The top holdings are the AI
  complex, so "in the market" may already mean concentrated AI exposure.
  Stage 1 can be a *rotation* (equal-weight, value, international,
  short-duration bonds) rather than cash.
- Tax lots, account types (taxable vs. retirement) and your time horizon
  change what "pull out" should mean. Worth a conversation with a
  fee-only advisor before Stage 2.
- **De-escalate too.** The thesis is falsified (packet §Falsification) if
  Oracle FCF turns positive on schedule, labs approach breakeven, spreads
  stay calm through a rate cycle, and enterprise ROI inflects. If that
  happens, stand the plan down.

---

## 4. Cadence

| When | What | Time |
|---|---|---|
| **Weekly** (e.g. Sat) | Skim [A] spreads/yields, [D] credit news, [F] TSMC/Korea when released, tracker notes | 20 min |
| **Monthly** | Full scorecard: every link G/Y/R, record in a dated table at the bottom of this file | 1 hr |
| **Quarterly earnings windows** (late Jan/Apr/Jul/Oct; NVDA ~4 weeks later) | [C] capex guides, [G] cloud growth/RPO, [H] footnotes, [I] reaction to beats | 2-3 hrs |
| **Semiannual** | Fed FSR, IMF GFSR, CBRE DC report, BIS review; revisit thresholds | 1 hr |
| **Event-driven** | Any hard red → do the scorecard that day | — |

**Upcoming checkpoints:** NVDA FQ3 earnings (late Nov 2026); Dec FOMC + SEP;
TSMC Nov revenue (~Dec 10); Q4 hyperscaler earnings + 2027 capex guides
(late Jan 2027). The 2027 guides are the big one for the "second
derivative" test. Packet forecast #1 (a top-4 guide trim by 12/31/26, 45%)
resolves in this window.

---

## 5. Implementation: what the existing fleet can automate

Kept outside the desk's decision path (Prime Directive: this is the human's
dashboard, not a desk input):

1. **Story trackers (zero new code).** Drop 4 topic files into
   `Agents_News_Trackers/topics/`, all weekly, Google News queries:
   - `ai-financing-credit`: private credit defaults, BDC redemptions/gates,
     data-center ABS, CoreWeave debt, Oracle rating/CDS.
   - `ai-datacenter-buildout`: lease cancellations, PJM/ERCOT large-load,
     moratoria/permits, utility pipeline changes.
   - `ai-accounting-quality`: useful life / depreciation, SEC comment
     letters, restatements, circular financing.
   - `ai-monetization`: lab revenue/fundraising/IPO, Azure/cloud growth,
     enterprise AI ROI surveys.
   These flow into the Daily Briefing automatically (non-`no-change` only).
2. **Deterministic scorecard script (small, later).** Pull the FRED series
   in [A] plus TSMC monthly revenue and Census BTOS, compute the
   relative-threshold flags in pure Python, and write a weekly markdown
   table to the vault. Every number comes from Python, none from a model.
3. **Desk overlap.** The desk's AI-capex theme already Brier-scores the
   packet's forecasts and monitors daily via `thesis_daily_status`. Use its
   REVERSAL/WATCH alerts as one more input to [C]/[I], not as the trigger.
4. **Manual-only** (no clean free feed): ABS/DDTL spreads, Oracle CDS,
   reserved GPU rates, private-credit default indices, lease-cancellation
   channel checks. Those go in the weekly human checklist.

### Who watches what (topic → source → agent)

Legend: **Desk** = Agents_TradingDesk (existing; EDGAR/Tiingo/FRED for the
AI-capex theme universe — NVDA, MSFT, ORCL, AVGO, CRWV, ARCC, OBDC, OWL;
daily monitoring only while that thesis is ACTIVE). **Trackers** =
Agents_News_Trackers topic (*proposed*, weekly). **Scorecard** = FRED
scorecard script (*proposed*). **Global** = Agents_News_Global (existing,
incidental coverage only). **You** = manual weekly/quarterly check. All
agent output lands in the **Daily Briefing**.

| Link | Topic | Source(s) | Agent |
|---|---|---|---|
| A | Credit spreads (HY/IG), real yields, financial conditions | FRED `BAMLH0A0HYM2`, `BAMLC0A0CM`, `DFII10`, `DGS10`, `NFCI` | Scorecard |
| A | Fed path, balance sheet, inflation | FOMC/SEP; FRED `WALCL`, `RRPONTSYD` | Scorecard + Global |
| A | Systemic-risk reports | Fed FSR, IMF GFSR, BIS Quarterly, Fed SLOOS | You (semiannual/quarterly) |
| A | Japan / carry | BoJ, USDJPY | Global + You |
| B | Lab fundraising, valuations, OpenAI IPO | The Information/Bloomberg/Reuters; EDGAR S-1/A | Trackers `ai-monetization` |
| B | Vendor / circular financing | NVDA 10-Q; press | Desk (NVDA filings) + Trackers `ai-accounting-quality` |
| B | SoftBank / Gulf capacity | SoftBank results; oil price | Global + You |
| C | Hyperscaler capex guides, capex/OCF, FCF | Earnings calls, 10-Qs | Desk (MSFT, ORCL filings) + You (GOOGL/AMZN/META at earnings) |
| C | Debt/equity raises, lease commitments | 8-K, 424B, 10-K lease footnotes; Moody's | Desk (universe filings) + You |
| C | Ratings (Oracle to junk?) | S&P/Moody's/Fitch | Trackers `ai-financing-credit` |
| D | Data-center ABS/CMBS spreads, CoreWeave debt, Oracle CDS | KBRA/Fitch presales, TRACE, press | Trackers `ai-financing-credit` (news) + You (levels) |
| D | Private-credit default rates | Fitch PCDR, Proskauer index, KBRA DLD | You (monthly) |
| D | BDC non-accruals, PIK, NAV discounts | BDC 10-Qs; prices | Desk (ARCC, OBDC, OWL) |
| D | Retail private-credit redemptions / gates | EDGAR `SC TO-I` | Trackers `ai-financing-credit` + You |
| E | Data-center construction spending | Census construction put in place | Scorecard |
| E | Vacancy, pre-leasing, lease cancellations | CBRE/JLL/Cushman/datacenterHawk; sell-side via press | Trackers `ai-datacenter-buildout` + You (CBRE semiannual) |
| E | Power queues, utility large-load pipelines | PJM, ERCOT, LBNL; utility calls | Trackers `ai-datacenter-buildout` |
| E | Permits, moratoria, power-price backlash | County agendas, Data Center Watch, PUC dockets, EIA | Trackers `ai-datacenter-buildout` |
| E | Turbines / transformers / electrical backlog | GE Vernova, Vertiv, Eaton earnings | You (earnings) |
| F | GPU rental rates (reserved), secondary prices | Silicon Data index, SemiAnalysis | You (weekly) |
| F | TSMC monthly revenue; Korea/Taiwan chip exports | TSMC IR (~10th), Korea customs | Scorecard (TSMC) + You |
| F | NVDA receivables / inventory | NVDA 10-Q | Desk (NVDA filings) |
| G | Lab revenue, margin, burn | S-1, press | Trackers `ai-monetization` |
| G | Cloud growth vs capex; RPO conversion | MSFT/ORCL/GOOGL/AMZN earnings, 10-Qs | Desk (MSFT, ORCL) + You |
| G | Enterprise AI adoption / ROI | Census BTOS, Ramp AI Index, surveys | Scorecard (BTOS) + Trackers `ai-monetization` |
| G | Productivity, AI layoffs | BLS, Challenger | Global + You (quarterly) |
| H | Useful lives, D&A vs capex, off-B/S SPVs, related-party revenue | 10-K/10-Q footnotes | Desk (universe filings) + You (10-K season) |
| H | SEC comment letters, restatements, auditor changes | EDGAR `CORRESP`, 8-K Items 4.01/4.02 | Trackers `ai-accounting-quality` + You |
| H | Short-seller / forensic claims | Burry, Chanos, short reports | Trackers `ai-accounting-quality` |
| I | Prices, earnings reactions, momentum | Tiingo | Desk (universe + momentum scan) |
| I | Concentration, RSP vs SPY, trend vs 200-day | Prices | Scorecard |
| I | Margin debt, ERP, insider selling | FINRA, Damodaran, Form 4 | You (monthly) |
| All | Stage scoring / de-risk decision | This file | **You only** |

### Baseline (fill at first monthly review, Nov 2026)

| Link | Key reading (date, value, source) | G/Y/R |
|---|---|---|
| A Funding | | |
| B Capital into AI | | |
| C Hyperscaler capex | | |
| D Plumbing | | |
| E Physical build | | |
| F Compute pricing | | |
| G Monetization | | |
| H Accounting | | |
| I Market structure | | |
| **Stage** | | |
