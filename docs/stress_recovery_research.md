# Stress-recovery methods for Singularity-Filtered Stress (spec §38)

The Milestone 7 implementation (`devtools/stress_recovery.py`) uses the
**confidence-weighted inverse-distance neighbourhood estimate** the master spec
prescribes as the starting point (§37):

```
w_j = (1 - C_j/100)^2 / (d_j + 0.25 h_local)^2
sigma_recovered = Σ w_j sigma_j / Σ w_j
```

over *locally converged* donor nodes (confidence < threshold), same body/material,
within ~2.5 local element layers, after median±3·MAD outlier rejection; "Not
Recoverable" if fewer than 6 valid donors remain.

Before this is called production-ready it must be compared against the standard
recovery schemes below. This note is the research log; the comparison itself is
future work (a `devtools/recovery_bench.py` on analytic fields with a known
smooth part).

## Candidate methods

### 1. Weighted neighbourhood IDW (current)
- **Idea**: local weighted average of donor stresses; weights favour high
  confidence and proximity.
- **Accuracy**: 0th-order (reproduces a constant exactly; O(h) bias on a
  gradient — it cannot represent a linear variation across the patch).
- **Robustness**: high — MAD rejection + the `1-C/100` factor make it hard for a
  bad donor or a half-singular neighbour to dominate. Degenerates gracefully to
  "Not Recoverable".
- **Cost**: one KD-tree query + a few dozen flops per flagged node. Negligible.
- **Complexity**: ~80 lines, no linear algebra.
- **Use**: the safe default and the fallback for every other method.

### 2. Moving Least Squares (MLS)
- **Idea**: fit a low-order polynomial (linear or quadratic) to the donor
  stresses in a weighted least-squares sense, evaluate the polynomial at the
  flagged point.
- **Accuracy**: 1st/2nd-order — represents gradients/curvature, so it extrapolates
  a smooth field into the singular zone far better than IDW.
- **Robustness**: moderate. Needs enough well-spread donors for the normal
  matrix to be conditioned; a poor donor distribution (donors only on one side
  of a re-entrant corner) gives wild extrapolation. Must guard with a condition
  number check and fall back to IDW.
- **Cost**: a 4×4 (linear) or 10×10 (quadratic) weighted normal solve per node.
  Still cheap (thousands of nodes).
- **Complexity**: moderate; needs a basis, a weight function, and a
  fallback/conditioning guard.
- **Verdict**: the most promising upgrade. Prototype linear MLS first.

### 3. Superconvergent Patch Recovery (SPR, Zienkiewicz–Zhu)
- **Idea**: for each patch of elements around a node, least-squares fit a
  polynomial to the stresses **sampled at the element superconvergent
  (Gauss/Barlow) points**, then evaluate at the node. The recovered field σ* is
  smoother and (for smooth problems) superconvergent.
- **Accuracy**: excellent for regular fields; this is the reference method for
  discretisation-error estimation (`||σ* - σ_h||`).
- **Robustness**: it is *designed for smooth regions*. Directly over a
  singularity it is not valid — but that is exactly our use: we only ever
  recover *from converged donor patches*, which is the regime SPR is built for.
- **Cost**: needs Gauss-point stresses (DPF can give ElementalNodal / Gauss
  data) and per-node patch assembly. Heavier than MLS; still tractable.
- **Complexity**: high — patch construction, element-type-dependent sampling.
- **Verdict**: the "correct" method for the discretisation-error side (M5 S29
  currently uses a ZZ-lite proxy); worth adopting there. For the filtered-stress
  contour, MLS on nodal donors is likely enough and far simpler.

### 4. Polynomial patch recovery (plain, unweighted)
- SPR without the superconvergent sampling / weighting — least-squares polynomial
  through nodal donor values. Between IDW and MLS in accuracy; MLS (weighted)
  strictly dominates it. Not worth implementing separately.

### 5. Zienkiewicz–Zhu error estimator
- Not a recovery method for the *value* but for the *error*: `η² = ∫(σ* - σ_h)²`.
  Relevant to Milestone 5 (S29) and to deciding whether more refinement is
  needed, not to the filtered-stress contour. `secondary_metrics.zz_lite_*`
  is the current stand-in; replacing σ* with an SPR field would make it a
  proper ZZ estimator.

## Plan

1. Keep IDW as the shipped default and the universal fallback.
2. Add **linear MLS** as an opt-in (`method="mls1"`) with a condition-number
   guard → IDW fallback; benchmark on:
   - analytic linear stress field (MLS must be ~exact, IDW biased),
   - plate-with-hole hoop stress near (but not at) the hole (known smooth),
   - L-bracket: recover from converged donors, check the recovered value is
     stable as the study is refined.
3. Evaluate SPR specifically for the S29 discretisation-error estimator.
4. Record accuracy / robustness / cost numbers here and pick the default.
