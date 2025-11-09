from dataclasses import dataclass

@dataclass
class Weights:
    w_hot: float = 1.0
    w_lat: float = 0.6
    w_cost: float = 0.4
    w_aff: float = 0.2
    w_egress: float = 0.3

def normalize_latency(p95_ms: int, sla_ms: int) -> float:
    return max(0.0, 1.0 - p95_ms / max(1, sla_ms * 2))

def normalize_cost(cost_gb: float) -> float:
    return 1.0 / (1.0 + cost_gb)

def _rel(x: float, lo: float, hi: float, invert: bool = False) -> float:
    if hi <= lo:
        return 1.0
    z = (x - lo) / (hi - lo)
    z = max(0.0, min(1.0, z))
    return 1.0 - z if invert else z

def score_location(feat: dict, site, weights: Weights) -> float:
    hot = float(feat.get("p_hot", 0.0))

    lat_min = feat.get("lat_min")
    lat_max = feat.get("lat_max")
    if lat_min is not None and lat_max is not None:
        lat = _rel(float(getattr(site, "p95_ms")), float(lat_min), float(lat_max), invert=True)
    else:
        lat = normalize_latency(getattr(site, "p95_ms"), int(feat["sla_ms"]))

    cost_min = feat.get("cost_min")
    cost_max = feat.get("cost_max")
    if cost_min is not None and cost_max is not None:
        cst = _rel(float(getattr(site, "cost_gb")), float(cost_min), float(cost_max), invert=True)
    else:
        cst = normalize_cost(getattr(site, "cost_gb"))

    aff = 1.0 if getattr(site, "region", getattr(site, "name", None)) in feat.get("affinity_regions", []) else 0.0
    egr = float(feat.get("egress_penalty", {}).get(getattr(site, "name"), 0.0))

    return (
        weights.w_hot * hot
        + weights.w_lat * lat
        + weights.w_cost * cst
        + weights.w_aff * aff
        - weights.w_egress * egr
    )


