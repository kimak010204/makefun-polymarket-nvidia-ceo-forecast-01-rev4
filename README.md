# Polymarket NVIDIA CEO price forecaster — revision 4

Entry for make.fun bounty `polymarket-nvidia-ceo-forecast-01`: forecast the
Yes price of every trading market of Polymarket's "Next CEO of NVIDIA?"
event at every minute of the 4 hours after each checkpoint, as five
quantiles (5%, 25%, 50%, 75%, 95%), scored by mean pinball loss.

## Why revision 4

Revision 3 scored **0.257016** on the six practice cases (dev rank #424,
still outside the top 100, whose bar is ≈0.24). Its median was already at
its limit with the signals available, and its inner-quantile scale (0.7)
was inherited from revision 2 without ever being tuned upward. A fresh
grid on the same three validation splits found the remaining lever in the
widths (details in `main.py`):

- **Inner quantiles 0.7 → 0.85.** The middle quantiles dominate the loss
  (q50 0.322, q75 0.337 in the revision-2 decomposition). Practice
  improves monotonically as the inner scale rises (0.75 → 0.256569,
  0.80 → 0.256196, 0.85 → 0.255943, 0.90 → 0.255808) while the
  fresh-split cost stays small; 0.85 is the chosen point.
- **Below-median offsets 1.15 → 1.20.** Downside slightly wider; on its
  own it improves practice and chaotic, and in the combination it keeps
  the fresh cost of the inner move near zero.
- **Longshots 0.7 → 0.65.** Slightly narrower again; on its own it
  improves fresh, offsetting the fresh cost of the inner widening.

The median is unchanged from revision 3 (sum reversion + asymmetric
up-spike give-back + regime gate); median error is unchanged too (0.7144).
This revision is a width recalibration, not a median improvement.

## Method (one paragraph)

`main.py` (Python 3, standard library only, no network, deterministic)
reads each case in `/input` (`$MAKEFUN_INPUT_DIR` locally) and writes one
`<id>.out` per case to `/output` (`$MAKEFUN_OUTPUT_DIR` locally). Median =
last price + clipped drift (sum reversion + up-spike give-back). Width =
empirical h-step quantiles (last 8 h, recentered), blended 85/15 with the
house baseline normal quantiles and 55/45 with pooled cross-market
quantiles, scaled by conditional vol (recent 2 h vs lookback 10-min vol,
^0.5, clip [0.7, 1.6]) and by a board-dependent multiplier (0.7 base,
+1.5·|S−1|, cap 1.6; inner quantiles ×0.85, outer ×1.0; longshots ×0.65;
below-median ×1.20, above-median ×0.8). Short history (<60 prices) falls
back to the house baseline widths; |S−1| > 0.30 falls back to revision 1.

## Validation (local backtest, official pinball loss)

Actual prices fetched from Polymarket's public price history
(`clob.polymarket.com/prices-history`, fidelity=1). The harness
reproduces the house baseline's published practice loss exactly
(0.363589).

| model | practice (6 cases) | fresh Nov 12–13 (4 cases) | chaotic Nov 10–11 (11 cases) |
|---|---|---|---|
| house baseline | 0.363589 | — | — |
| revision 1 (shipped) | 0.285119 | 0.2463 | 2.7161 |
| revision 2 | 0.259477 | 0.2195 | 2.6853 |
| revision 3 | 0.257016 | 0.205365 | 2.666946 |
| **this program (rev 4)** | **0.255974** | **0.205597** | **2.665082** |

Per practice case, rev 4 vs rev 3: p00 0.395 vs 0.402, p01 0.273 vs 0.273,
p02 0.364 vs 0.365, p03 0.299 vs 0.298 (slightly worse), p04 0.163 vs 0.162
(flat), p05 0.099 vs 0.099. Fresh is essentially flat (+0.11%, within the
~1% guard set before tuning); chaotic is slightly better. 90% coverage on
practice is 0.887 (rev 3: 0.893). Median error unchanged at 0.7144, still
above the leaders' 0.60–0.68: the median gap is NOT closed by this
revision. The gain is small (same order as the ~±0.001 practice noise
between neighbouring points) — the honest reason to prefer rev 4 over
rev 3 is that it improves practice clearly while keeping fresh flat.

Runtime: ~0.22 s per case locally (limit 8 s). Two runs on the same
cases write byte-identical output. Output files 193–263 KB (limit 1 MiB).

## Run locally

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

## License

MIT — see `LICENSE`.
