"""Estimate a lot's True Value (TV) from its Sample, using the formula the market publishes.

The formula and its numbers come from GET /v1/calendar -> "rules", so nothing is hard-coded here.
Includes Sample Noise Expectation smoothing (USE_SAMPLE_NOISE_MARGIN=1 by default for Stage 2-3)
where sample measurements have measurement noise relative to the true lot quality.
"""
import os


def grade(rules: dict, defect_pct: float) -> tuple[str, float]:
    for step in rules["defect_steps"]:
        if defect_pct <= step["max_defect_pct"]:
            return step["grade"], step["step"]
    last = rules["defect_steps"][-1]
    return last["grade"], last["step"]


def moisture_factor(rules: dict, m: float) -> float:
    mo = rules["moisture"]
    if m <= mo["ref"]:
        return 1.0
    if m <= mo["hard_limit"]:
        return (1 - m / 100) / (1 - mo["ref"] / 100) - mo["penalty_per_pp"] * (m - mo["ref"])
    return mo["hard_factor"]


def screen_factor(rules: dict, screen: int) -> float:
    factors = {int(k): v for k, v in rules["screen_factors"].items()}
    codes = sorted(factors)
    return factors[min(max(int(screen), codes[0]), codes[-1])]


def true_value(rules: dict, sample: dict) -> float:
    """TV = P0 * G(d) * M(m) * S(s), baht per kg."""
    d, m = sample["defect_pct"], sample["moisture_pct"]
    g = (1 - d / 100) * grade(rules, d)[1]
    return rules["p0"] * g * moisture_factor(rules, m) * screen_factor(rules, sample["screen"])


def expected_true_value_with_noise(
    rules: dict,
    sample: dict,
    defect_sigma: float = 0.30,
    moisture_sigma: float = 0.25,
) -> float:
    """
    Smooths out sharp grade-step & moisture cliffs when samples have noise (Stage 2-3).
    Uses 5-point discrete Gaussian weights across defect & moisture uncertainty.
    Toggleable via USE_SAMPLE_NOISE_MARGIN env var (default 1 = noise-smoothed, 0 = exact sample).
    """
    pts_weights = [(-1.5, 0.08), (-0.75, 0.22), (0.0, 0.40), (0.75, 0.22), (1.5, 0.08)]
    d0 = sample["defect_pct"]
    m0 = sample["moisture_pct"]
    s0 = sample["screen"]
    sf = screen_factor(rules, s0)

    ev = 0.0
    for zd, wd in pts_weights:
        d = max(0.0, min(100.0, d0 + zd * defect_sigma))
        g = (1 - d / 100) * grade(rules, d)[1]
        for zm, wm in pts_weights:
            m = max(5.0, min(30.0, m0 + zm * moisture_sigma))
            ev += (wd * wm) * (rules["p0"] * g * moisture_factor(rules, m) * sf)
    return ev


def estimate_lots(rules: dict, lots: list[dict]) -> dict[str, float]:
    use_noise = os.environ.get("USE_SAMPLE_NOISE_MARGIN", "0") == "1"
    defect_sigma = float(os.environ.get("NOISE_DEFECT_SIGMA", "0.30"))
    moisture_sigma = float(os.environ.get("NOISE_MOISTURE_SIGMA", "0.25"))
    out = {}
    for lot in lots:
        if use_noise:
            val = expected_true_value_with_noise(rules, lot["sample"], defect_sigma, moisture_sigma)
        else:
            val = true_value(rules, lot["sample"])
        out[lot["lot_id"]] = round(val, 2)
    return out


def appraisal_items(rules: dict, lots: list[dict], tv: dict[str, float]) -> list[dict]:
    return [
        {
            "lot_id": lot["lot_id"],
            "grade": grade(rules, lot["sample"]["defect_pct"])[0],
            "est_value_per_kg": tv[lot["lot_id"]],
        }
        for lot in lots
    ]
