"""Primary singularity classifier (Milestone 4).

External automation layer -> CPython 3 + numpy/scipy.  Pure: no Ansys, no DPF.
Validated against synthetic series (tests/unit/test_convergence_classifier.py)
BEFORE it is ever pointed at real solver data (spec S42).

Given a stress-vs-characteristic-element-size series at one location (or for a
region's hotspot), decide whether the finite-element stress approaches a finite
limit as h -> 0 (spec S5):

  A. mathematical singularity  -- stress diverges with refinement
  B. ordinary concentration    -- stress high but -> finite limit
  C. not-yet-converged         -- trend unclear, no divergence evidence

Method (spec S26 + S27):
  * increment analysis: Delta_sigma_i = |sigma_{i+1} - sigma_i|; for a convergent
    field sigma = sigma_inf + C h^p the increments shrink ~ geometrically
    (ratio < 1); for sigma ~ h^-lambda they grow (ratio > 1).  Also fit
    log(Delta_sigma) = a + b log(h)  ->  lambda_est ~ -b - 1.
  * two competing model fits, compared on normalised residual + plausibility:
      finite   :  sigma(h) = sigma_inf + C h^p        (p > 0)
      divergent:  sigma(h) = B + A h^-lambda           (lambda > 0)
  * monotonicity + sufficient mesh-size change gates.

Output: ConvergenceVerdict with a classification, a divergence exponent, an
extrapolated limit (flagged when the divergent model wins), and a 0..1
`singularity_evidence` scalar that feeds the 55 % "mesh divergence behaviour"
term of the Milestone 6 Confidence score -- it is NOT the final confidence.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np

try:
    from scipy.optimize import curve_fit
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _HAVE_SCIPY = False

# thresholds -- preliminary, to be calibrated on benchmarks (spec S33)
MIN_LEVELS_FIT = 4
MIN_LEVELS_ANY = 3
MIN_RANGE_RATIO = 1.4          # h_coarsest / h_finest must exceed this
P_MIN, P_MAX = 0.1, 6.0
LAMBDA_MIN, LAMBDA_MAX = 0.02, 2.5
REL_RESID_GOOD = 0.02         # normalised residual below this == a good fit


@dataclasses.dataclass
class ModelFit:
    name: str
    params: dict
    rms: float                 # RMS residual in stress units
    rel_resid: float           # rms / (peak-to-peak of sigma, or |mean|)
    r2: float
    ok: bool
    note: str = ""


@dataclasses.dataclass
class ConvergenceVerdict:
    classification: str        # convergent | singular | insufficient_data | uncertain
    singularity_evidence: float   # 0..1  (input to the Confidence score)
    divergence_exponent: float | None      # lambda estimate (None if convergent/insufficient)
    extrapolated_limit: float | None       # sigma_inf (finite model); None if not meaningful
    limit_is_trustworthy: bool
    finite_fit: ModelFit | None
    divergent_fit: ModelFit | None
    evidence: dict

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return d


# --------------------------------------------------------------------------- #
# model functions                                                             #
# --------------------------------------------------------------------------- #
def _finite_model(h, sigma_inf, C, p):
    return sigma_inf + C * np.power(h, p)


def _divergent_model(h, B, A, lam):
    return B + A * np.power(h, -lam)


def _r2(y, yhat):
    y = np.asarray(y, float)
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else (1.0 if ss_res == 0 else 0.0)


def _scale(sigma):
    sigma = np.asarray(sigma, float)
    ptp = float(np.ptp(sigma))
    return ptp if ptp > 1e-12 else max(abs(float(np.mean(sigma))), 1e-12)


# --------------------------------------------------------------------------- #
# fitting                                                                     #
# --------------------------------------------------------------------------- #
def fit_finite_limit(h, sigma, weights=None) -> ModelFit:
    h = np.asarray(h, float)
    s = np.asarray(sigma, float)
    scale = _scale(s)
    if not _HAVE_SCIPY or h.size < 3:
        return ModelFit("finite", {}, math.nan, math.inf, 0.0, False, "no scipy / too few points")

    # guesses: sigma_inf ~ finest value; slope of log|s - sigma_inf0| vs log h -> p
    order = np.argsort(-h)                      # coarse -> fine
    h_s, s_s = h[order], s[order]
    sinf0 = s_s[-1] + (s_s[-1] - s_s[-2]) * 0.5
    resid0 = np.abs(s_s - sinf0)
    with np.errstate(divide="ignore", invalid="ignore"):
        good = resid0 > 0
        p0 = 2.0
        if good.sum() >= 2:
            b = np.polyfit(np.log(h_s[good]), np.log(resid0[good]), 1)[0]
            p0 = float(np.clip(b, P_MIN, P_MAX))
    C0 = (s_s[0] - sinf0) / max(h_s[0] ** p0, 1e-12)

    sigma_err = None
    if weights is not None:
        w = np.asarray(weights, float)[order]
        sigma_err = 1.0 / np.clip(w, 1e-6, None)

    best = None
    for pg in (p0, 1.0, 2.0, 0.5, 3.0):
        try:
            popt, _ = curve_fit(
                _finite_model, h_s, s_s,
                p0=[sinf0, C0 if np.isfinite(C0) else 1.0, pg],
                sigma=sigma_err, absolute_sigma=False,
                bounds=([-np.inf, -np.inf, P_MIN], [np.inf, np.inf, P_MAX]),
                maxfev=20000,
            )
            yhat = _finite_model(h_s, *popt)
            rms = float(np.sqrt(np.mean((s_s - yhat) ** 2)))
            if best is None or rms < best[1]:
                best = (popt, rms, yhat)
        except Exception:
            continue

    if best is None:
        return ModelFit("finite", {}, math.nan, math.inf, 0.0, False, "curve_fit failed")
    popt, rms, yhat = best
    return ModelFit(
        "finite",
        {"sigma_inf": float(popt[0]), "C": float(popt[1]), "p": float(popt[2])},
        rms, rms / scale, _r2(s_s, yhat),
        ok=bool(P_MIN <= popt[2] <= P_MAX),
        note="p out of range" if not (P_MIN <= popt[2] <= P_MAX) else "",
    )


def fit_divergent(h, sigma, weights=None) -> ModelFit:
    h = np.asarray(h, float)
    s = np.asarray(sigma, float)
    scale = _scale(s)
    if not _HAVE_SCIPY or h.size < 3:
        return ModelFit("divergent", {}, math.nan, math.inf, 0.0, False, "no scipy / too few points")

    order = np.argsort(-h)
    h_s, s_s = h[order], s[order]
    B0 = s_s[0] - (s_s[1] - s_s[0])            # a bit below the coarsest value
    resid0 = np.abs(s_s - B0)
    lam0 = 0.5
    with np.errstate(divide="ignore", invalid="ignore"):
        good = resid0 > 0
        if good.sum() >= 2:
            b = np.polyfit(np.log(h_s[good]), np.log(resid0[good]), 1)[0]
            lam0 = float(np.clip(-b, LAMBDA_MIN, LAMBDA_MAX))
    A0 = (s_s[-1] - B0) / max(h_s[-1] ** (-lam0), 1e-12)

    sigma_err = None
    if weights is not None:
        w = np.asarray(weights, float)[order]
        sigma_err = 1.0 / np.clip(w, 1e-6, None)

    best = None
    for lg in (lam0, 0.5, 0.3, 1.0, 0.1):
        try:
            popt, _ = curve_fit(
                _divergent_model, h_s, s_s,
                p0=[B0, A0 if np.isfinite(A0) else 1.0, lg],
                sigma=sigma_err,
                bounds=([-np.inf, -np.inf, LAMBDA_MIN], [np.inf, np.inf, LAMBDA_MAX]),
                maxfev=20000,
            )
            yhat = _divergent_model(h_s, *popt)
            rms = float(np.sqrt(np.mean((s_s - yhat) ** 2)))
            if best is None or rms < best[1]:
                best = (popt, rms, yhat)
        except Exception:
            continue

    if best is None:
        return ModelFit("divergent", {}, math.nan, math.inf, 0.0, False, "curve_fit failed")
    popt, rms, yhat = best
    lam = float(popt[2])
    return ModelFit(
        "divergent",
        {"B": float(popt[0]), "A": float(popt[1]), "lambda": lam},
        rms, rms / scale, _r2(s_s, yhat),
        ok=bool(LAMBDA_MIN < lam < LAMBDA_MAX and abs(popt[1]) > 1e-9),
        note="lambda at bound" if not (LAMBDA_MIN < lam < LAMBDA_MAX) else "",
    )


def increment_analysis(h, sigma) -> dict:
    h = np.asarray(h, float)
    s = np.asarray(sigma, float)
    order = np.argsort(-h)                      # coarse -> fine
    h_s, s_s = h[order], s[order]
    d = np.abs(np.diff(s_s))
    out = {
        "increments": d.tolist(),
        "monotonic_fraction": float(np.mean(np.sign(np.diff(s_s)) == np.sign(np.diff(s_s))[0]))
        if d.size else 0.0,
        "abs_monotone_increasing": bool(np.all(np.diff(np.abs(s_s)) >= -1e-9 * _scale(s))),
    }
    if d.size >= 2:
        with np.errstate(divide="ignore", invalid="ignore"):
            ratios = d[1:] / d[:-1]
        ratios = ratios[np.isfinite(ratios)]
        out["increment_ratios"] = ratios.tolist()
        out["mean_increment_ratio"] = float(np.mean(ratios)) if ratios.size else math.nan
        out["last_increment_ratio"] = float(ratios[-1]) if ratios.size else math.nan
        out["growing_fraction"] = float(np.mean(ratios > 1.0)) if ratios.size else 0.0
        # log-log slope of increment vs midpoint h
        hmid = np.sqrt(h_s[:-1] * h_s[1:])
        gg = d > 0
        if gg.sum() >= 2:
            b = float(np.polyfit(np.log(hmid[gg]), np.log(d[gg]), 1)[0])
            out["loglog_increment_slope"] = b
            out["lambda_from_increments"] = max(-b - 1.0, 0.0)   # d ~ h^-(lambda+1)
    return out


# --------------------------------------------------------------------------- #
# classification                                                              #
# --------------------------------------------------------------------------- #
def classify_series(h, sigma, weights=None) -> ConvergenceVerdict:
    h = np.asarray(h, float).reshape(-1)
    s = np.asarray(sigma, float).reshape(-1)
    n = h.size
    finite_mask = np.isfinite(h) & np.isfinite(s)
    h, s = h[finite_mask], s[finite_mask]
    if weights is not None:
        weights = np.asarray(weights, float)[finite_mask]
    n = h.size

    range_ratio = float(np.max(h) / np.min(h)) if n >= 2 and np.min(h) > 0 else 1.0
    ev = {"n_levels": int(n), "mesh_range_ratio": range_ratio}

    if n < MIN_LEVELS_ANY or range_ratio < MIN_RANGE_RATIO:
        ev["reason"] = "need >= {0} levels and h-range ratio >= {1}".format(
            MIN_LEVELS_ANY, MIN_RANGE_RATIO)
        return ConvergenceVerdict("insufficient_data", 0.0, None, None, False, None, None, ev)

    inc = increment_analysis(h, s)
    ev.update(inc)

    fin = fit_finite_limit(h, s, weights) if n >= MIN_LEVELS_FIT else None
    div = fit_divergent(h, s, weights) if n >= MIN_LEVELS_FIT else None

    # ---- evidence for divergence, 0..1 ----------------------------------
    # Model fits (regularised over all points) carry the most weight; raw
    # increment ratios are noise-sensitive so they only nudge.
    e = 0.30                      # neutral prior -> "uncertain" band
    mir = inc.get("mean_increment_ratio", math.nan)
    lir = inc.get("last_increment_ratio", math.nan)

    # 1) increments growing vs shrinking
    if np.isfinite(mir):
        if mir > 1.03:
            e += 0.18 * min((mir - 1.0) / 0.5, 1.0)
        elif mir < 0.85:
            e -= 0.22 * min((0.85 - mir) / 0.4, 1.0)
    if np.isfinite(lir) and lir > 1.0:
        e += 0.07 * min((lir - 1.0) / 0.5, 1.0)

    # 2) stress flattening?  relative last increment vs stress scale
    if inc["increments"]:
        rel_last = inc["increments"][-1] / _scale(s)
        ev["relative_last_increment"] = float(rel_last)
        if rel_last < 0.008:
            e -= 0.18

    # 3) two-model comparison -- the primary discriminator (spec S27)
    if fin is not None and div is not None:
        ev["finite_rel_resid"] = fin.rel_resid
        ev["divergent_rel_resid"] = div.rel_resid
        if np.isfinite(fin.rel_resid) and np.isfinite(div.rel_resid):
            adv = (fin.rel_resid - div.rel_resid) / max(fin.rel_resid + div.rel_resid, 1e-9)
            ev["model_advantage_divergent"] = float(adv)
            e += 0.45 * float(np.clip(adv, -1.0, 1.0))
            # decisive-fit bonuses
            if div.ok and div.rel_resid < 0.05 and div.rel_resid < 0.6 * fin.rel_resid:
                e += 0.30
            if fin.ok and fin.rel_resid < REL_RESID_GOOD and fin.rel_resid < 0.6 * div.rel_resid:
                e -= 0.30
            # both models fit comparably well AND finite is plausible -> lean convergent
            if fin.ok and fin.rel_resid < 0.03 and abs(adv) < 0.15 and fin.params.get("p", 0) > 0.2:
                e -= 0.12
        if fin.ok:
            ev["finite_p"] = fin.params.get("p")
        if div.ok:
            ev["divergent_lambda"] = div.params.get("lambda")

    # 4) monotone increase toward refinement is necessary (not sufficient)
    if not inc.get("abs_monotone_increasing", False):
        e -= 0.18

    e = float(np.clip(e, 0.0, 1.0))

    # ---- classification from evidence -----------------------------------
    lam_est = None
    if div is not None and div.ok:
        lam_est = div.params["lambda"]
    elif "lambda_from_increments" in inc and e > 0.5:
        lam_est = inc["lambda_from_increments"]

    sigma_inf = None
    trust_limit = False
    if fin is not None and fin.ok:
        sigma_inf = fin.params["sigma_inf"]
        trust_limit = fin.rel_resid < 0.03 and e < 0.5

    if e >= 0.70:
        cls = "singular"
    elif e <= 0.30:
        cls = "convergent"
    else:
        cls = "uncertain"

    if cls != "singular":
        lam_est = None
    if cls == "singular":
        sigma_inf = None
        trust_limit = False

    return ConvergenceVerdict(
        classification=cls,
        singularity_evidence=e,
        divergence_exponent=lam_est,
        extrapolated_limit=sigma_inf,
        limit_is_trustworthy=trust_limit,
        finite_fit=fin,
        divergent_fit=div,
        evidence=ev,
    )


def _ordered_h(series):
    import numpy as _np
    h = _np.array([s if s is not None else _np.nan for s in series.sizes_m], float)
    if _np.isfinite(h).all():
        order = _np.argsort(-h)
        return h[order], order
    order = _np.arange(series.n_levels)[::-1]      # assume index order = fine->coarse
    return _np.arange(series.n_levels, dtype=float)[::-1] + 1.0, order


def classify_hotspot_neighborhood(series, radius_frac: float = 0.04) -> ConvergenceVerdict:
    """Classify the trend of the MAX stress within a small neighbourhood of the
    finest-mesh hotspot -- localises the analysis to where a singular field
    actually lives, instead of being diluted by the bulk (spec S30)."""
    import numpy as _np

    v = series.valid
    idx = _np.where(v)[0]
    mat = series.matrix[v]
    coords = series.ref_coords[v]
    if idx.size == 0:
        raise ValueError("no points valid at every level")

    peak_i = int(_np.argmax(mat[:, series.ref_index]))
    lo, hi = coords.min(axis=0), coords.max(axis=0)
    diag = float(_np.linalg.norm(hi - lo))
    d = _np.linalg.norm(coords - coords[peak_i], axis=1)
    near = d <= max(radius_frac * diag, 1e-9)
    if near.sum() < 3:
        near = d <= _np.partition(d, 10)[10]      # fall back to the 10 closest

    h_ord, order = _ordered_h(series)
    peak_series = mat[near][:, order].max(axis=0)
    return classify_series(h_ord, peak_series)


def field_divergence(h, mat) -> dict:
    """Vectorised, cheap per-point divergence read for a whole field (K points x
    L levels).  No curve_fit -- increment-ratio + log-log increment slope only.
    Use this for the evidence/lambda contour; use classify_series() for the few
    hotspots that need the full two-model comparison.

    ``h`` : (L,) characteristic sizes, ANY order.
    ``mat``: (K, L) stress at K points across the L levels (matching h order).
    Returns evidence (K,) in 0..1, lambda_est (K,) (nan where not singular),
    classification (K,) str array, and the ordered peak-to-peak scale.
    """
    h = np.asarray(h, float).reshape(-1)
    mat = np.asarray(mat, float)
    if mat.ndim == 1:
        mat = mat[None, :]
    order = np.argsort(-h)                       # coarse -> fine
    h_s = h[order]
    s = mat[:, order]                            # (K, L)
    K, L = s.shape

    scale = np.maximum(np.ptp(s, axis=1), np.maximum(np.abs(np.mean(s, axis=1)), 1e-12))
    d = np.abs(np.diff(s, axis=1))               # (K, L-1)
    ok = np.isfinite(s).all(axis=1)

    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = d[:, 1:] / d[:, :-1]           # (K, L-2)
    mean_ratio = np.nanmean(np.where(np.isfinite(ratios), ratios, np.nan), axis=1)
    rel_last = d[:, -1] / scale

    # log-log slope of increment vs midpoint h, per point (closed form)
    hmid = np.sqrt(h_s[:-1] * h_s[1:])
    lx = np.log(hmid)
    x = lx - lx.mean()
    denom = float(np.sum(x * x)) or 1.0
    with np.errstate(divide="ignore", invalid="ignore"):
        ly = np.log(np.where(d > 0, d, np.nan))
    ly_c = ly - np.nanmean(ly, axis=1, keepdims=True)
    slope = np.nansum(ly_c * x, axis=1) / denom          # b ; d ~ h^b
    lam_from_inc = np.clip(-slope - 1.0, 0.0, LAMBDA_MAX)

    e = np.full(K, 0.30)
    e += 0.30 * np.clip((mean_ratio - 1.0) / 0.5, -1.0, 1.0)
    e += np.where(rel_last > 0.03, 0.12 * np.clip(rel_last / 0.15, 0, 1), 0.0)
    e -= np.where(rel_last < 0.01, 0.15, 0.0)
    e += np.where(lam_from_inc > 0.15, 0.18, 0.0)
    e -= np.where((mean_ratio < 0.85) & np.isfinite(mean_ratio), 0.22, 0.0)
    e = np.clip(e, 0.0, 1.0)
    e[~ok] = np.nan

    cls = np.where(e >= 0.70, "singular",
                   np.where(e <= 0.30, "convergent", "uncertain")).astype(object)
    cls[~ok] = "insufficient_data"
    lam = np.where(e >= 0.70, lam_from_inc, np.nan)

    return {"evidence": e, "lambda_est": lam, "classification": cls,
            "mean_ratio": mean_ratio, "rel_last_increment": rel_last}


def classify_hotspot(series, top_percentile: float = 99.0) -> ConvergenceVerdict:
    """Classify the peak-stress trend of a cross_mesh.CrossMeshSeries.

    Uses, per level, the high-percentile stress over the points that are valid at
    every level -- a stable proxy for "the hotspot" that is not hostage to a
    single noisy node.
    """
    import numpy as _np

    v = series.valid
    mat = series.matrix[v]                       # (Mv, L)
    if mat.shape[0] == 0:
        raise ValueError("no points valid at every level")
    # reference point = argmax at the finest level; track that neighbourhood
    finest = series.ref_index
    peak_series = _np.percentile(mat, top_percentile, axis=0)
    h = _np.array([s if s is not None else _np.nan for s in series.sizes_m], float)
    # order columns coarse->fine by size (fallback: by index)
    if _np.isfinite(h).all():
        order = _np.argsort(-h)
    else:
        order = _np.arange(len(peak_series))
    return classify_series(_np.where(_np.isfinite(h[order]), h[order],
                                     _np.arange(len(order))[::-1] + 1.0),
                           peak_series[order])
