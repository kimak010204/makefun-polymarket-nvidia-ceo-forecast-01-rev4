#!/usr/bin/env python3
"""Forecast Polymarket's next NVIDIA CEO prices (make.fun bounty
polymarket-nvidia-ceo-forecast-01) -- revision 4.

Reads each case in the input directory (one case per file <id>.in) and writes
one forecast file <id>.out per case to the output directory.

Revision 4 keeps revision 3's median exactly (last price + sum-reversion
drift + asymmetric up-spike give-back, with the regime gate) and recalibrates
three width constants. Revision 3's widths were tuned one parameter at a
time from revision 2, and its inner-quantile scale (0.7) was inherited from
revision 2 without ever being tuned upward. A fresh grid on the same three
validation splits shows the inner scale is the remaining lever:

  - Inner quantiles (25%/75%) 0.7 -> 0.85: the middle quantiles dominate
    the loss (q50 0.322, q75 0.337 in the revision-2 decomposition), and
    practice improves monotonically as the inner scale rises (0.75 ->
    0.256569, 0.80 -> 0.256196, 0.85 -> 0.255943, 0.90 -> 0.255808),
    while the fresh-split cost stays small (+0.19% at 0.80, +0.31% at
    0.85). 0.85 is the chosen point: beyond it the fresh cost grows
    faster than the practice gain.
  - Below-median offsets 1.15 -> 1.20: downside slightly wider. On its
    own it improves practice (0.256881) and chaotic (2.662477); combined
    with the inner move it keeps the fresh cost of that move near zero.
  - Longshots (price < 0.03) 0.7 -> 0.65: slightly narrower again. On its
    own it improves fresh (0.204900); in the combination it offsets the
    fresh cost of the inner widening, so the final point costs fresh
    only +0.11% while gaining practice -0.41%.

Combined point (this program): practice 0.255974 (revision 3: 0.257016,
-0.41%), fresh 0.205597 (revision 3: 0.205365, +0.11%), chaotic 2.665082
(revision 3: 2.666946, -0.07%). All three splits are scored with the same
official pinball loss and the same protocol as revisions 2 and 3, so the
comparison is apples-to-apples. The gain is small (the same order as the
~±0.001 practice noise between neighbouring points), and the median error
is unchanged from revision 3 (0.7144) — this revision does not close the
median gap to the leaders (0.60-0.68). It is a width recalibration only,
chosen because it improves practice clearly while keeping fresh (the split
closest in time and regime to the final window) essentially flat.

Method (median unchanged from revision 3; three width constants changed):
  - Median: last price + drift, where drift is
      -0.8 * (1 - exp(-h/60)) * p0 * clip(S - 1, +/-0.25)
      -0.5 * (1 - exp(-h/120)) * max(p0 - price 60 min ago, 0)
    S = sum of the last prices of all markets in the case. Total drift
    clipped to +/-0.10 in price units.
  - Width: for each horizon h, the empirical quantiles of past h-step
    price changes over the last 8 hours (480 minutes), recentered so
    their median is 0, blended 85/15 with the house baseline's normal
    quantiles (sigma 0.0025 per sqrt-minute), then blended 55/45 with a
    pooled cross-market width: h-step changes of ALL markets in the
    case, normalised by sqrt(p * (1 - p)) and rescaled by the market's
    own sqrt(p0 * (1 - p0)).
  - Conditional vol: all widths are scaled by (recent 2h 10-minute
    realized vol / lookback 10-minute realized vol) ** 0.5, clipped to
    [0.7, 1.6].
  - Width scale: base 0.7, rising with the board's sum deviation,
    min(1.6, 0.7 + 1.5 * |S - 1|); inner quantiles (25%/75%) x0.85,
    outer quantiles (5%/95%) x1.0; longshots (p0 < 0.03) x0.65 extra;
    below-median offsets x1.20, above-median offsets x0.8.
  - Regime gate: if |S - 1| > 0.30 the board is not in an arbitrage
    regime (e.g. market launch, when every candidate sat near 0.45 and
    the sum was ~2.4) and the program falls back to the revision-1
    model exactly (persistence median, un-narrowed widths). This keeps
    the worst case no worse than the shipped model.
  - Short history (<60 prices): fall back to the house baseline widths
    (the median drift still applies; the 60-minute move is 0 there).
  - No network, no randomness, standard library only, deterministic:
    two runs on the same case write identical bytes.

Local backtest (official pinball loss, actuals fetched from Polymarket),
measured by running THIS program on the six practice cases:
  house baseline 0.363589 | revision 1 (shipped) 0.285119
  | revision 2 0.259477 | revision 3 0.257016 | this program 0.255974
  Per case, this program vs revision 3 (verified by scoring this exact
  file): p00 0.3954 vs 0.4017, p01 0.2727 vs 0.2731, p02 0.3642 vs
  0.3652, p03 0.2991 vs 0.2980 (slightly worse), p04 0.1625 vs 0.1624
  (flat), p05 0.0988 vs 0.0994. On the 4 fresh Nov 12-13 checkpoints
  it is essentially flat vs revision 3 (0.205597 vs 0.205365, +0.11%,
  within the ~1% guard), and on the 11 chaotic Nov 10-11 checkpoints
  it is slightly better (2.665082 vs 2.666946). 90% coverage on
  practice is 0.887 (revision 3: 0.893). Median error is unchanged
  from revision 3 (0.7144). Two runs on the same cases write
  byte-identical output;
  ~0.22 s per case (limit 8 s); output files 192-260 KB (limit 1 MiB).

Usage (local): mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
In the grader: python3 /bot/main.py reads /input, writes /output.
"""

import bisect
import json
import math
import os
from pathlib import Path

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.6448536269514722, -0.6744897501960817, 0.0,
     0.6744897501960817, 1.6448536269514722]

# --- tuned parameters (see BUILD_LOG.md for the validation) ---
LOOKBACK_MIN = 480       # empirical h-step changes from the last 8 hours
W_EMP = 0.85             # weight of empirical deltas vs baseline normal deltas
BASE_SIGMA = 0.0025      # house baseline spread per sqrt-minute
GAMMA = 0.5              # conditional-vol exponent
VOL_CLIP = (0.7, 1.6)    # conditional-vol scaling limits
POOL_W = 0.45            # weight of pooled cross-market widths (rev2: 0.30)
GRID_H = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 180, 240]
MIN_DIFFS = 30           # minimum overlapping samples for a grid horizon
A_SUM = 0.8              # sum-reversion strength
TAU_SUM = 60.0           # sum-reversion timescale (minutes)
B_UP = 0.5               # up-spike give-back strength (applied as -B_UP)
TAU_UP = 120.0           # up-spike give-back timescale (minutes)
SDEV_CLIP = 0.25         # clip on (S - 1) used by the sum drift
DRIFT_CLIP = 0.10        # clip on total median drift (price units)
GATE_SDEV = 0.30         # |S - 1| above this -> fall back to revision 1
W_BASE = 0.7             # base width multiplier in calm regime
W_SLOPE = 1.5            # width growth per unit |S - 1|
W_MAX = 1.6              # cap on the width multiplier
INNER_SCALE = 0.85       # extra scale for the 25%/75% quantiles (rev3: 0.7)
OUTER_SCALE = 1.0        # extra scale for the 5%/95% quantiles (rev2: 1.2)
W_LONG = 0.65            # extra width scale for longshots (p0 < 0.03) (rev3: 0.7)
DOWN_SCALE = 1.20        # extra scale for below-median offsets (q05, q25) (rev3: 1.15)
UP_SCALE = 0.8           # extra scale for above-median offsets (q75, q95)


def _clamp6(x):
    """x clamped to [0, 1] and rounded to 6 decimals (0, never -0)."""
    v = min(1.0, max(0.0, x))
    return math.floor(v * 1e6 + 0.5) / 1e6 + 0.0


def _empirical_quantiles(vals, levels):
    """Linear-interpolation empirical quantiles of a list of numbers."""
    s = sorted(vals)
    n = len(s)
    out = []
    for lv in levels:
        if n == 1:
            out.append(s[0])
            continue
        pos = lv * (n - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        out.append(s[lo] * (1.0 - frac) + s[hi] * frac)
    return out


def _interp_grid(qgrid, h):
    """Interpolate recentered quantile deltas at horizon h (in sqrt(h))."""
    if not qgrid:
        return [0.0] * 5
    hs = sorted(qgrid)
    if h <= hs[0]:
        return qgrid[hs[0]]
    if h >= hs[-1]:
        return qgrid[hs[-1]]
    j = bisect.bisect_left(hs, h)
    h0, h1 = hs[j - 1], hs[j]
    w = (math.sqrt(h) - math.sqrt(h0)) / (math.sqrt(h1) - math.sqrt(h0))
    d0, d1 = qgrid[h0], qgrid[h1]
    return [d0[k] * (1.0 - w) + d1[k] * w for k in range(5)]


def _baseline_rows(x, horizon):
    rows = []
    for h in range(1, horizon + 1):
        s = BASE_SIGMA * math.sqrt(h)
        rows.append([_clamp6(x + z * s) for z in Z])
    return rows


def _rms_diffs(step, seg):
    ds = [seg[i] - seg[i - step] for i in range(step, len(seg))]
    if not ds:
        return 0.0
    return math.sqrt(sum(d * d for d in ds) / len(ds))


def _cond_vol_factor(prices):
    """Recent (2h) vs lookback 10-minute realized-vol ratio, ^ GAMMA, clipped."""
    n = len(prices)
    if n < 240:
        return 1.0
    s_rec = _rms_diffs(10, prices[-121:])
    s_lb = _rms_diffs(10, prices[max(0, n - LOOKBACK_MIN):])
    if s_lb <= 0.0 or s_rec <= 0.0:
        return 1.0
    f = (s_rec / s_lb) ** GAMMA
    return min(VOL_CLIP[1], max(VOL_CLIP[0], f))


def _market_offsets(prices, horizon):
    """Per-market width offsets (recentered deltas) for h = 1..horizon.

    Blend of the market's own empirical h-step quantiles and the house
    baseline normal quantiles, scaled by the conditional-vol factor.
    Short history (<60 prices) falls back to the baseline deltas.
    """
    x = prices[-1]
    n = len(prices)
    if n < 60:
        return [[z * BASE_SIGMA * math.sqrt(h) for z in Z]
                for h in range(1, horizon + 1)]
    seg = prices[max(0, n - LOOKBACK_MIN):]
    m = len(seg)
    qgrid = {}
    for h in GRID_H:
        if m - h < MIN_DIFFS:
            continue
        diffs = [seg[i] - seg[i - h] for i in range(h, m)]
        qs = _empirical_quantiles(diffs, QUANTILES)
        med = qs[2]
        qgrid[h] = [v - med for v in qs]
    factor = _cond_vol_factor(prices)
    rows = []
    for h in range(1, horizon + 1):
        d = _interp_grid(qgrid, h)
        base = [z * BASE_SIGMA * math.sqrt(h) for z in Z]
        rows.append([(W_EMP * d[k] + (1.0 - W_EMP) * base[k]) * factor
                     for k in range(5)])
    return rows


def _pooled_grid(markets, horizon):
    """Cross-market width grid: quantiles of h-step changes of ALL markets
    in the case, each change normalised by sqrt(p * (1 - p)) at the step's
    start. Returns {h: [5 recentered normalised quantiles]} (median 0)."""
    grid = {}
    for h in GRID_H:
        if h > horizon:
            continue
        vals = []
        for prices in markets:
            seg = prices[max(0, len(prices) - LOOKBACK_MIN):]
            for i in range(h, len(seg)):
                p_start = seg[i - h]
                s = math.sqrt(max(p_start * (1.0 - p_start), 1e-6))
                vals.append((seg[i] - seg[i - h]) / s)
        if vals:
            qs = _empirical_quantiles(vals, QUANTILES)
            med = qs[2]
            grid[h] = [v - med for v in qs]
    return grid


def _forecast_case(markets, horizon):
    """markets: list of price lists (one per market in the case).
    Returns {index: rows} with rows = horizon x 5 quantile lists."""
    n_markets = len(markets)
    lasts = [p[-1] for p in markets]
    total = sum(lasts)
    sdev = total - 1.0
    calm = abs(sdev) <= GATE_SDEV
    if calm:
        width_mult = min(W_MAX, W_BASE + W_SLOPE * abs(sdev))
        sdev_used = max(-SDEV_CLIP, min(SDEV_CLIP, sdev))
        pooled = _pooled_grid(markets, horizon)
    else:
        # Regime gate: board is not in an arbitrage regime; revision 1.
        width_mult = 1.0
        sdev_used = 0.0
        pooled = {}
    out = {}
    for idx, prices in enumerate(markets):
        prices = [p for p in prices if math.isfinite(p)]
        if not prices:
            out[idx] = [[0.5] * 5 for _ in range(horizon)]
            continue
        x = prices[-1]
        n = len(prices)
        d60 = x - prices[-61] if n > 60 else 0.0
        offs = _market_offsets(prices, horizon)
        rows = []
        for h in range(1, horizon + 1):
            if calm:
                g_sum = 1.0 - math.exp(-h / TAU_SUM)
                g_up = 1.0 - math.exp(-h / TAU_UP)
                drift = (-A_SUM * g_sum * x * sdev_used
                         - B_UP * g_up * max(d60, 0.0))
                drift = max(-DRIFT_CLIP, min(DRIFT_CLIP, drift))
            else:
                drift = 0.0
            med = x + drift
            off = list(offs[h - 1])
            if calm and pooled:
                pq = _interp_grid(pooled, h)
                scale = math.sqrt(max(x * (1.0 - x), 1e-6))
                poff = [pq[k] * scale for k in range(5)]
                off = [(1.0 - POOL_W) * off[k] + POOL_W * poff[k]
                       for k in range(5)]
            band = W_LONG if x < 0.03 else 1.0
            row = []
            for k in range(5):
                if calm:
                    s = width_mult * band * (INNER_SCALE if k in (1, 3)
                                      else OUTER_SCALE if k in (0, 4)
                                      else 1.0)
                    s *= DOWN_SCALE if k in (0, 1) else (UP_SCALE if k in (3, 4) else 1.0)
                else:
                    s = 1.0
                row.append(_clamp6(med + off[k] * s))
            for k in range(1, 5):
                if row[k] < row[k - 1]:
                    row[k] = row[k - 1]
            rows.append(row)
        out[idx] = rows
    return out


def main():
    in_dir = Path(INPUT_DIR)
    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs = sorted(p for p in in_dir.iterdir()
                    if p.is_file() and p.name.endswith(".in"))
    for path in inputs:
        case_id = path.name[:-3]
        with open(path, "r", encoding="utf-8") as f:
            case = json.load(f)
        horizon = int(case.get("horizon_minutes", 240))
        markets = [m["prices"] for m in case["markets"]]
        fc = _forecast_case(markets, horizon)
        forecasts = [{"id": m["id"], "q": fc[i]}
                     for i, m in enumerate(case["markets"])]
        out_path = out_dir / (case_id + ".out")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"forecasts": forecasts},
                               separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
