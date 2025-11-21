# NetApp Data-in-Motion: Intelligent Cloud Storage Solution

**Data-in-Motion** is an intelligent data management solution designed to dynamically analyze, tier, and move data across hybrid and multi-cloud environments. It addresses the challenges of managing large volumes of distributed data by optimizing placement, ensuring resilience, and providing real-time insights.

## 🚀 Quick Start

### Prerequisites
- Docker and Docker Compose

### 1. Build and Run
```bash
docker compose up -d --build
```
This starts Redpanda (Kafka), MinIO (AWS/Azure/GCP mocks), the API, Dashboard, and Observability stack (Prometheus/Grafana).

### 2. Initialize System
```bash
./setup.sh
```
This script builds containers, seeds data, trains models, and starts the dashboard.

### 3. Access Interfaces
- **Dashboard**: [http://localhost:8050](http://localhost:8050)
- **Grafana**: [http://localhost:3000](http://localhost:3000) (admin/admin)
- **API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🏗️ System Architecture

### Components
1.  **API Service (FastAPI)**: The central brain. Manages file metadata, serves ML predictions, and orchestrates migration tasks.
2.  **Migrator Service**: A background worker that processes the migration queue.
3.  **Stream Processor (Kafka Consumer)**: Consumes access events from Redpanda and updates the SQLite state store.
4.  **ML Engine**: Periodically retrains models on historical access logs to predict "hotness".
5.  **Dashboard**: Streamlit-based interface for monitoring and control.

### Data Flow
1.  **Ingest**: Applications generate access logs → Kafka Topic.
2.  **Process**: Consumer reads logs → Updates `file_meta` in DB.
3.  **Analyze**: Optimizer (Cron) queries DB → Calls ML Model → Determines optimal tier.
4.  **Act**: If current tier != optimal tier → Create `MigrationTask`.
5.  **Move**: Migrator picks up task → Checks source integrity → Copies file → Verifies → Updates DB.

---

## 🧠 Intelligent Features & Edge Case Handling

### 1. ML-Driven Data Placement (Unique Approach)
Unlike traditional "watermark" tiering, we use a **Predictive & Mathematical** approach:
- **Predictive**: A **Random Forest** model analyzes access patterns (frequency, recency, time-of-day) to predict *future* file "hotness" (`p_hot`).
- **Mathematical Optimization**: We use **Mixed-Integer Linear Programming (MILP)** to solve for the optimal placement that minimizes Cost while satisfying Latency SLAs.
    - **Hot Data**: Automatically routed to Low Latency regions (e.g., AWS).
    - **Cold Data**: Automatically retired to Low Cost regions (e.g., Azure/GCP).

### 2. Resilience & Throttling (Edge Case: API Limits)
Cloud providers often rate-limit requests. Our system handles `429 Too Many Requests` and `503 Service Unavailable` errors using **Exponential Backoff**.
- **Mechanism**: If a request fails, the worker sleeps for 1s, 2s, 4s... up to a max retry limit before marking the task as failed.
- **Verification**: You can observe "Retrying in Xs..." logs during high-load simulations.

### 3. Growing File Detection (Edge Case: Partial Uploads)
To prevent data corruption by migrating a file that is still being written to:
- **Mechanism**: The migrator checks the `LastModified` timestamp. If the file was modified < 5 seconds ago, the migration is **skipped** with `reason: file_growing`.
- **Benefit**: Ensures atomicity and prevents partial file transfers.

### 4. Chaos Engineering (Edge Case: Network Failures)
We include built-in chaos controls to simulate real-world network issues.
- **Latency Injection**: Can inject artificial latency (e.g., 2000ms) to verify that the system doesn't time out but instead waits patiently.
- **Failure Simulation**: Can force endpoints to return errors to test the retry logic.

### 5. Data Consistency
- **Atomic Copy**: The system performs a full copy to the destination *before* updating the metadata pointer. If the copy fails, the pointer remains at the source, ensuring no data loss.

---

## 🛠️ Troubleshooting

- **"Docker paused"**: Unpause Docker Desktop.
- **"Container not found"**: Run `docker compose down` then `./setup.sh` again.
- **"No data"**: Run the simulation step manually:
    ```bash
    docker compose exec api python -m app.services.stream.simulate --events 100
    ```

## 📚 Detailed Guides
- **[Running Guide](RUNNING_GUIDE.md)**: Detailed step-by-step instructions for verification and assessment.
