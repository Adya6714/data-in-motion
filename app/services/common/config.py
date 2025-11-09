import os, json
from pydantic import BaseModel
from typing import List, Optional

class Endpoint(BaseModel):
    name: str
    url: str
    access_key: str
    secret_key: str
    bucket: str
    latency_ms: float
    cost_per_gb: float
    encrypted: bool = True

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP","localhost:9092")
DB_URL = os.getenv("DB_URL","sqlite:///./state.db")
SLA_LATENCY_MS = float(os.getenv("SLA_LATENCY_MS","80"))
REPLICATION_FACTOR = int(os.getenv("REPLICATION_FACTOR","2"))
ENCRYPTION_ENABLED = os.getenv("ENCRYPTION_ENABLED","false").lower()=="true"
S3_ENDPOINTS: List[Endpoint] = [Endpoint(**e) for e in json.loads(os.getenv("S3_ENDPOINTS","[]") or "[]")]
API_HOST = os.getenv("API_HOST","127.0.0.1"); API_PORT = int(os.getenv("API_PORT","8000"))
DASH_HOST = os.getenv("DASH_HOST","127.0.0.1"); DASH_PORT = int(os.getenv("DASH_PORT","8050"))
FAST_DEMO = os.getenv("FAST_DEMO","false").lower()=="true"
HOT_DEMOTE_RECENCY_S = int(os.getenv("HOT_DEMOTE_RECENCY_S", "300" if FAST_DEMO else "900"))
HOT_DEMOTE_P_THRESHOLD = float(os.getenv("HOT_DEMOTE_P_THRESHOLD", "0.35"))
WARM_DEMOTE_RECENCY_S = int(os.getenv("WARM_DEMOTE_RECENCY_S", "3600" if FAST_DEMO else "21600"))
HEAT_TAU_SEC = float(os.getenv("HEAT_TAU_SEC", "600" if FAST_DEMO else "1800"))
HEAT_WARM_THRESHOLD = float(os.getenv("HEAT_WARM_THRESHOLD", "20" if FAST_DEMO else "25"))
HEAT_HOT_THRESHOLD = float(os.getenv("HEAT_HOT_THRESHOLD", "60" if FAST_DEMO else "70"))
HEAT_PRED_BOOST = float(os.getenv("HEAT_PRED_BOOST", "35"))
HEAT_DAILY_WEIGHT = float(os.getenv("HEAT_DAILY_WEIGHT", "0.05" if FAST_DEMO else "0.2"))
DEFAULT_PRIMARY = os.getenv("DEFAULT_PRIMARY", S3_ENDPOINTS[0].name if S3_ENDPOINTS else "aws")
