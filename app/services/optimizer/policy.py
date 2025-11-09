from dataclasses import dataclass

@dataclass
class Costs:
    prewarm_cost: float = 0.001   
    miss_cost: float = 0.01      
    max_hot_ratio: float = 0.2

def decide_tier(base_rule_tier: str, p_hot: float, hot_rank_ctx) -> str:
    if base_rule_tier == "hot": return "hot"
    if p_hot >= 0.7 and hot_rank_ctx.get("under_cap", True):
        return "hot"
    return base_rule_tier

def should_prewarm(p_hot_soon: float, size_gb: float, costs: Costs) -> bool:
    ev_benefit = p_hot_soon * costs.miss_cost * size_gb
    return ev_benefit > costs.prewarm_cost * size_gb


