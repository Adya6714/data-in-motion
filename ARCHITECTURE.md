# System Architecture & Codebase Tour

This document provides a technical deep dive into the **Data-in-Motion** architecture and maps key features to their implementation in the codebase.

---

## 1. High-Level Architecture

The system follows a **Microservices Event-Driven Architecture**:

```mermaid
graph TD
    User[User/App] -->|Access File| S3[S3 Interface]
    S3 -->|Log Event| Kafka[Redpanda (Kafka)]
    Kafka -->|Consume| Consumer[Consumer Service]
    Consumer -->|Write| DB[(SQLite DB)]
    
    subgraph "Optimization Engine"
        Cron[Optimizer Cron] -->|Read Stats| DB
        Cron -->|1. Predict| ML[ML Model (Random Forest)]
        Cron -->|2. Optimize| MILP[MILP Solver]
        Cron -->|Create Task| DB
    end
    
    subgraph "Execution Plane"
        Migrator[Migrator Service] -->|Poll Task| DB
        Migrator -->|Copy Data| Cloud[Cloud Storage]
    end
```

### Components
1.  **API Service**: FastAPI-based gateway. Handles metadata, serves the dashboard API, and exposes metrics.
2.  **Consumer**: Listens to the `file_access` Kafka topic. Aggregates raw events into 1h/24h windows.
3.  **Optimizer**: A background process that runs the "Brain". It combines ML predictions with MILP optimization to generate migration tasks.
4.  **Migrator**: A resilient worker that executes data movement. Handles retries, checksum verification, and atomic metadata updates.

---

## 2. Codebase Tour: Where the Magic Happens 🪄

Use this map to verify the implementation of our core innovations.

### A. Predictive AI (Random Forest)
**Goal**: Predict future file popularity (`p_hot`) to pre-warm data.

*   **File**: [`app/ml/serve_tiers.py`](app/ml/serve_tiers.py)
*   **Key Function**: `predict_proba(feat: dict)`
*   **Logic**:
    1.  Loads the trained Scikit-Learn model (`tier.bin`).
    2.  Featurizes inputs: `access_1h`, `access_24h`, `recency`, `hour_of_day`.
    3.  Returns a probability score (0.0 to 1.0).

### B. Mathematical Optimization (MILP)
**Goal**: Solve for the mathematically optimal data placement (Min Cost + Latency Penalty).

*   **File**: [`app/services/optimizer/placement_milp.py`](app/services/optimizer/placement_milp.py)
*   **Key Function**: `solve_placement(...)`
*   **Logic**:
    1.  Defines a **Pulp** linear programming problem.
    2.  **Objective**: `Minimize(StorageCost + 0.001 * LatencyPenalty)`.
    3.  **Constraints**:
        *   `Sum(Replicas) == RF` (Replication Factor).
        *   `Latency <= SLA` (Soft constraint with penalty).
        *   `Provider Diversity` (Avoid single provider if RF>1).

### C. Resilience & Exponential Backoff
**Goal**: Handle cloud provider rate limits (`429`, `503`) gracefully.

*   **File**: [`app/services/migrator/service.py`](app/services/migrator/service.py)
*   **Key Function**: `_ensure_and_copy_once` (Lines 101-119)
*   **Logic**:
    *   Implements a retry loop with `time.sleep(backoff)`.
    *   Catches `ClientError` and checks for "Throttling", "TooManyRequests", "503".
    *   Doubles the sleep time after each failure (1s -> 2s -> 4s).

### D. Chaos Engineering
**Goal**: Verify system stability under failure conditions.

*   **File**: [`app/services/common/s3_client.py`](app/services/common/s3_client.py)
*   **Key Function**: `client_for(name)`
*   **Logic**:
    *   Checks `chaos.get_failed_endpoints()`.
    *   Raises `RuntimeError` if the requested endpoint is in the failure list.
    *   This forces the Migrator to enter its retry/failure workflow.

### E. Dynamic Heat Decay
**Goal**: Automatically retire cold data.

*   **File**: [`app/services/optimizer/model.py`](app/services/optimizer/model.py)
*   **Logic**:
    *   The `access_1h` and `access_24h` counters in the DB naturally decrease over time as the sliding window moves.
    *   As these inputs drop, the ML model's `p_hot` score drops.
    *   The MILP solver then calculates that "Low Cost" storage is now the optimal placement, triggering a migration to Azure/GCP.
