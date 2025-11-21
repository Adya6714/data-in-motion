# Running & Assessment Guide: NetApp Data-in-Motion

This guide details how to start the platform and verify the new intelligent features.

## 1. Prerequisites
*   **Docker Desktop**: Must be installed and **RUNNING**.
    *   *Note*: Your Docker seems to be paused. Please unpause it from the Dashboard or Menu bar.

## 2. Quick Start
Run the automated setup script. This will build containers, generate data, train models, and start the dashboard.

```bash
./setup.sh
```

*Wait for the script to complete (approx. 2-3 minutes).*

## 3. Assessing Features

### A. Intelligent Dashboard
**Goal**: Visualize the system in action.
1.  Open [http://localhost:8050](http://localhost:8050).
2.  **Heatmap**: Observe files changing color (Blue=Cold, Red=Hot) as traffic is simulated.
3.  **Migration Queue**: Watch the "Migrations" tab to see files moving between tiers.

### B. ML-Driven Data Placement
**Goal**: Verify that "Hot" data goes to Low Latency, and "Cold" data goes to Low Cost.
1.  **Trigger Traffic**: In the dashboard sidebar, select a file and click **"Burst 100 Hits"**.
2.  **Observe**:
    *   The file's "Heat Score" will rise > 0.7.
    *   The system should schedule a migration to the **AWS** (or whichever is configured as lowest latency) tier.
3.  **Check Logs**:
    ```bash
    docker compose logs -f api | grep "recommend_placement"
    ```
    *You should see logs indicating "Reason: hot_data_low_latency".*

### C. Resilience & Throttling
**Goal**: Verify the system handles "Too Many Requests" errors gracefully.
1.  **Simulate Throttling** (requires code modification or mock, but you can verify the logic):
    *   The `service.py` now has a retry loop.
    *   If you want to force it, you can temporarily modify `app/services/migrator/service.py` to raise a `ClientError("429")` inside the loop and watch the logs for "Retrying in 1s...".

### D. Chaos Engineering (Latency Injection)
**Goal**: Simulate a slow network and verify the migrator waits.
1.  **Inject Latency**:
    ```bash
    docker compose exec api python -c "from app.services.policy import chaos; chaos.set_latency(2000); print('Latency: 2000ms')"
    ```
2.  **Trigger Migration**: Burst a file in the dashboard to force a move.
3.  **Observe Speed**: The migration task will take significantly longer (at least 2 seconds).
4.  **Reset**:
    ```bash
    docker compose exec api python -c "from app.services.policy import chaos; chaos.set_latency(0); print('Latency: 0ms')"
    ```

### E. Growing File Detection (Integrity)
**Goal**: Ensure we don't migrate files that are currently being written to.
1.  **Test**:
    *   The system now checks `LastModified`.
    *   If you were to continually write to a file (e.g., `touch` it every second), the migrator would skip it with `reason: file_growing`.

## 4. Troubleshooting
*   **"Docker paused"**: Unpause Docker Desktop.
*   **"Container not found"**: Run `docker compose down` then `./setup.sh` again.
*   **"No data"**: Run the simulation step manually:
    ```bash
    docker compose exec api python -m app.services.stream.simulate --events 100
    ```

## 5. Advanced Verification & Observability

This section covers manual verification of advanced features using both the Local Dashboard and Grafana.

### A. Accessing Observability Tools
*   **Grafana**: [http://localhost:3000](http://localhost:3000) (Credentials: `admin` / `admin`)
*   **Prometheus**: [http://localhost:9090](http://localhost:9090)

### B. Verifying Heat Decay
**Goal**: Confirm that files "cool down" when not accessed.
1.  **Action**: Stop all traffic to a specific "Hot" (Red) file.
2.  **Local Dashboard**: Watch the file's color turn from Red -> Orange -> Blue over time (approx. 1-2 minutes depending on decay settings).
3.  **Grafana**:
    *   Go to **"Data-in-Motion Metrics"** dashboard.
    *   Look for the **"File Heat Score"** panel.
    *   Verify the line for that file trends downward.

### Machine Learning (Random Forest)
We use a robust, interpretable model to predict the probability of a file being accessed in the next window.
1.  **Action**: Burst a "Cold" file (Blue) to make it "Hot".
2.  **Local Dashboard**:
    *   Go to **"Migrations"** tab.
    *   Verify a new task appears: `Moving <file> from <LowCost> to <LowLatency>`.
3.  **Grafana**:
    *   Look for the **"ML Model Predictions"** panel.
    *   Observe the prediction for the burst file change from low to high probability.
    *   Verify the **"Model Feature Importance"** panel to see which features (e.g., `access_count`, `last_accessed`) are driving the prediction.

### C. Verifying Tier Migration
**Goal**: Confirm files move to the correct tier based on heat.
1.  **Action**: Burst a "Cold" file (Blue) to make it "Hot".
2.  **Local Dashboard**:
    *   Go to **"Migrations"** tab.
    *   Verify a new task appears: `Moving <file> from <LowCost> to <LowLatency>`.
3.  **Grafana**:
    *   Look for **"Tier Distribution"** or **"Active Migrations"** panel.
    *   You should see a spike in "Pending Migrations" and a shift in the storage counts.

### D. Verifying Chaos Controls (Latency Injection)
**Goal**: Verify the system adapts to network slowness.
1.  **Inject Latency**:
    ```bash
    docker compose exec api python -c "from app.services.policy import chaos; chaos.set_latency(2000); print('Latency set to 2000ms')"
    ```
2.  **Action**: Trigger a migration (Burst a file).
3.  **Local Dashboard**: Notice the migration progress bar moves much slower.
4.  **Grafana**:
    *   Look for **"Migration Duration"** or **"Network Latency"** panel.
    *   You should see a distinct spike to ~2000ms.
5.  **Reset**:
    ```bash
    docker compose exec api python -c "from app.services.policy import chaos; chaos.set_latency(0); print('Latency reset to 0ms')"
    ```

## 6. Backend Deep Dive

### Architecture Data Flow
1.  **Simulation**: `simulate.py` generates synthetic access events (JSON) and pushes them to the `file_access` Kafka topic.
2.  **Ingestion**: The `consumer` service reads these events and updates the SQLite database (`access_event` table) and increments counters in `file_meta`.
3.  **Optimization Loop**: The `api` service runs a background task (`_auto_placement_loop`) that:
    *   Calculates the "Heat Score" for each file.
    *   Runs the MILP (Mixed-Integer Linear Programming) solver to determine the optimal location.
    *   Creates a `MigrationTask` if the current location differs from the optimal one.
4.  **Execution**: The `migrator` service picks up tasks, performs the copy (using `boto3`), verifies the checksum, and updates the database.

### Understanding the Metrics (Placement Explain)
When you view the "Placement Explain" JSON in the dashboard, here is what the fields mean:

*   **`objective`**: The minimized cost function value from the MILP solver. It combines storage cost and latency penalties. Lower is better.
    *   *Formula*: `Sum(Cost) + 0.001 * Sum(Latency_Penalty) - Score_Weight * Preference`
*   **`sla_ms`**: The target latency (Service Level Agreement). Default is **80ms**.
*   **`rf`**: Replication Factor. How many copies of the data should exist. Default is **2**.
*   **`p_hot`**: Probability of Hotness (0.0 to 1.0). Calculated by the Random Forest model based on access frequency and recency.
    *   `> 0.7`: Considered "Hot" -> Needs Low Latency.
    *   `< 0.3`: Considered "Cold" -> Needs Low Cost.
*   **`scores`**: A calculated preference score for each site (AWS, Azure, GCP) based on the file's heat and the site's characteristics (Cost vs. Latency).

### Solving the Problem: A Unique Approach
Most tiering solutions use simple "Watermark" logic (e.g., "if accessed > 10 times, move to SSD"). **Data-in-Motion** takes a unique, mathematical approach:

1.  **Multi-Objective Optimization**: We don't just look at speed. We use **Linear Programming** to mathematically solve for the *perfect* balance between **Cost** and **Latency** for *every single file*.
2.  **Predictive, Not Reactive**: Instead of waiting for a file to become popular, our **Random Forest** model predicts *future* popularity based on early access patterns, allowing us to pre-warm data before the traffic spike hits.
3.  **Dynamic Heat Decay**: Files don't stay hot forever. Our system naturally decays the heat score, ensuring data automatically drifts back to cheaper storage when it becomes irrelevant, saving money without human intervention.
