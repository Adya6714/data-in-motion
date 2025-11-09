from dataclasses import dataclass
from typing import List, Dict, Optional
import pulp as pl

@dataclass
class Site:
    name: str
    p95_ms: int
    cost_gb: float
    provider: str
    region: str = ""
    encrypted: bool = True

def solve_placement(
    sites: List[Site],
    rf: int,
    sla_ms: int,
    avoid_provider_clash: bool = True,
    site_scores: Optional[List[float]] = None,
    score_weight: float = 0.1,
):
    n = len(sites)
    if n == 0:
        return [], {"reason": "no_sites", "sla_ms": sla_ms, "rf": rf, "sites": []}
    
    x = pl.LpVariable.dicts('x', range(n), lowBound=0, upBound=1, cat='Binary')
    m = pl.LpProblem("placement", pl.LpMinimize)
    
    # Minimize storage cost and soft-penalize exceeding SLA latency
    objective = pl.lpSum([x[i] * sites[i].cost_gb for i in range(n)]) + \
                0.001 * pl.lpSum([x[i] * max(0, sites[i].p95_ms - sla_ms) for i in range(n)])
    if site_scores is not None:
        # Incorporate site preference scores (higher is better)
        objective += (-score_weight) * pl.lpSum([x[i] * float(site_scores[i]) for i in range(n)])
    m.setObjective(objective)  

    # Exactly choose RF (or all sites if fewer available)
    target = min(rf, n)
    m += pl.lpSum([x[i] for i in range(n)]) == target
    under_sla = [i for i in range(n) if sites[i].p95_ms <= sla_ms]
    if under_sla:
        m += pl.lpSum([x[i] for i in under_sla]) >= 1

    # Provider diversity: avoid multiple replicas on the same provider
    if avoid_provider_clash and target >= 2:
        prov = {}
        for i, s in enumerate(sites):
            prov.setdefault(s.provider, []).append(i)
        for _p, idxs in prov.items():
            if len(idxs) >= 2:
                m += pl.lpSum([x[i] for i in idxs]) <= 1

    m.solve(pl.PULP_CBC_CMD(msg=False))
    chosen = [sites[i].name for i in range(n) if pl.value(x[i]) > 0.5]

    obj_val = pl.value(m.objective)
    explain = {
        "objective": float(obj_val) if obj_val is not None else None,
        "chosen": chosen,
        "sla_ms": sla_ms,
        "rf": rf,
        "sites": [{ "name": s.name, "p95_ms": s.p95_ms, "cost_gb": s.cost_gb, "provider": s.provider, "region": (s.region or s.name) } for s in sites]
    }
    return chosen, explain


