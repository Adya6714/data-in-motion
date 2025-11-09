from fastapi import APIRouter
from app.ml import serve_tiers, serve_forecast

r = APIRouter(prefix="/ml")

@r.post("/load")
def load_models(tier_model:str="/app/models/tier.bin", forecast_model:str="/app/models/forecast.bin"):
    ok1 = serve_tiers.load(tier_model)
    ok2 = serve_forecast.load(forecast_model)
    return {"tier_loaded": ok1, "forecast_loaded": ok2}


