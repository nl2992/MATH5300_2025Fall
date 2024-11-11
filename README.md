# Yield Curve Arbitrage Strategy in Fixed Income Markets (Group A)

A relatively systematic **DV01-neutral yield curve butterfly** relative-value strategy targeting temporary curvature dislocations (belly versus wings) in sovereign fixed income markets. The strategy is driven by **carry-and-roll-down (CRD)** signals, uses **rolling Z-score normalisation** to adapt across regimes, and incorporates an optional **IRS confirmation filter** to reduce false positives.  

This repository accompanies the final paper and the complete, reproducible **Assignment 12 finalised codebase**.

---

## Project Overview

The strategy exploits the empirical observation that sovereign yield curves exhibit persistent structure in **level, slope, and curvature**, while curvature itself is frequently distorted by temporary forces such as:
- macroeconomic announcements,
- auction supply and issuance cycles,
- hedging and ALM flows,
- risk-on / risk-off positioning.

By constructing **DV01-neutral butterflies** (for example, 2s5s10s or 5s10s30s), the strategy neutralises first-order level risk and isolates relative mispricing in the curve’s belly. Returns are expected to arise from two sources:

1. **Mean reversion in curvature**, as temporary richness or cheapness in the belly converges back towards the wings.
2. **Carry and roll-down**, where the belly’s CRD dominates that of the DV01-weighted wings over short holding horizons.

The final production-style specification (“R3 / E3”) augments this framework with **cross-instrument confirmation**, requiring agreement between government and IRS curve signals when swap data is available.

---

## Strategy Summary

### Instruments and Implementation

Two implementation sleeves are supported:

- **Curve (bond) sleeve**  
  Positions are expressed using **zero-coupon equivalents** derived from fitted sovereign yield curves. Coupon bonds are decomposed into STRIPS-like exposures and marked to market via curve-implied discount factors.

- **Futures sleeve** (where deep liquidity exists)  
  - **United States**: TU (2y), FV (5y), TY (10y), US (30y)  
  - **Germany**: Schatz (2y), Bobl (5y), Bund (10y), Buxl (30y)  
  - **United Kingdom**: short, medium, and long Gilt futures mapped to ~2/5/10/30-year equivalents  

Unused risk capacity is parked in a **cash proxy (SOFR)**, implemented as daily accrual on idle capital.

---

### Signal Construction (CRD → Z-score)

For each country and fly structure:

1. Compute **carry and roll-down** for each leg under an unchanged-curve assumption over a short horizon (default: one month).
2. Combine legs into a **fly-level CRD** using DV01-neutral, self-financing weights.
3. Apply a **5-day moving average** to reduce micro-noise.
4. Standardise the signal using a **rolling Z-score** (default: 126 trading days).
5. *(Optional)* Apply **IRS confirmation**: enter only when government and IRS Z-scores agree in sign.

---

### Trading Rules

- **Entry**: absolute Z-score ≥ 1.6  
- **Exit**: absolute Z-score ≤ 0.2  
- **Direction**: determined by the sign of the Z-score  
- **Portfolio constraint**:  
  - Maximum of **four concurrent flies**  
  - Each fly capped at **25% of total DV01 budget**  
- **Futures usage**: restricted to higher-conviction signals (|Z| ≥ entry + 1) and only where liquid contracts exist.

---

### Transaction Costs

Transaction costs are modelled conservatively using **country-level round-trip basis-point assumptions per fly**, applied on entry and exit:

- US: 0.12 bp  
- Germany: 0.12 bp  
- UK: 0.18 bp  
- Japan: 0.08 bp  
- Australia: 0.35 bp  
- Canada: 0.25 bp  
- Italy: 0.50 bp  

A modest liquidity multiplier is applied to longer-dated flies (for example, 5s10s30s).

---

## Data Requirements

### Primary Inputs (Bloomberg exports)

The code expects an Excel workbook (`Yield curve arb.xlsx`) containing:

1. **Sovereign yield curves** (daily par yields)  
   - Sheet: `Yield Signals`  
   - Tenors: 2y, 5y, 10y, 30y for each country (US, DE, UK, IT, JP, AU, CA)

2. **IRS curves** (daily, by tenor)  
   - Sheet: `IRS`  
   - Used exclusively for confirmation filtering where available

3. **Futures settlement prices** (daily)  
   - Sheet: `Futs`  
   - Generic continuous contracts (for example, `TY1 Comdty`)

The loader assumes Bloomberg’s wide export format with repeated date columns (for example, `Date`, `Date.1`, etc.). This format should be preserved unless the loader is rewritten.

### Optional Sanity Checks

- FRED US constant-maturity yields (DGS2, DGS5, DGS10, DGS30)  
- Exchange documentation (CME, Eurex, ICE) for contract metadata

---

## Key Parameters (Defaults)

| Parameter | Description | Default |
|---|---|---|
| In-sample window | Calibration period | 2004-01-01 to 2019-12-31 |
| CRD horizon | Carry/roll horizon | 1 month |
| Z-score window | Rolling normalisation | 126 trading days |
| Signal smoothing | Moving average | 5 days |
| Entry / exit Z | Band thresholds | 1.6 / 0.2 |
| Total DV01 budget | Portfolio risk budget | 100,000 |
| Max flies | Concurrent positions | 4 |

---

## Backtest Pipeline

At a high level, the notebook performs the following steps:

1. **Load and align data** across sovereign curves, IRS curves, and futures.
2. **Construct CRD-based fly signals**, including both rolling and (for comparison) fixed-normalisation baselines.
3. **Apply IRS confirmation** where swap data is available.
4. **Run in-sample backtests** for:
   - Baseline (fixed Z, US only),
   - R1 (rolling Z),
   - R2 (multi-country + futures + costs),
   - R3 (R2 + IRS filter).
5. **Run out-of-sample backtest** (2020 onward) for R3 only.
6. **Produce diagnostics**, including PnL paths, drawdowns, turnover, cost drag, and correlation analysis.

---

## Outputs

The notebook generates:

- Daily net and gross PnL series  
- Cumulative returns (capital-normalised)  
- Maximum drawdown, Sharpe, Sortino, and Calmar ratios  
- VaR and Expected Shortfall estimates  
- Trade-level statistics (hit rate, holding periods, win/loss)  
- Cost attribution and turnover  
- Correlations versus rates futures and selected asset-class proxies (where data access permits)

---

## Assumptions and Limitations

- **DV01-neutrality** removes first-order level exposure but does not eliminate residual slope, curvature, or convexity risk.
- Curve-based execution is a **theoretical proxy**; real-world coupon bonds introduce additional micro-structure effects.
- Futures-to-tenor mapping is approximate and may generate small hedging errors.
- Mean-reversion strategies are **regime-dependent**; curvature can remain rich or cheap for extended periods during policy shocks or QE-style environments.

These risks are discussed explicitly in the accompanying paper.

---

## Contributors

- **Nigel Li** – Research direction, methodology design, theoretical framework, and paper drafting  
- **Ryan Hou** – Implementation, backtesting, robustness analysis, and risk diagnostics  
- **Jesse Price** – Code review, visualisation checks, and design feedback

---

## Usage and Disclaimer

This codebase is provided for **academic and research purposes only**.  
It is not investment advice and should not be used for live trading without substantial additional validation, risk controls, and operational review.
