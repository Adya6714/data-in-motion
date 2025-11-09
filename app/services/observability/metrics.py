from prometheus_client import Counter, Gauge, Summary


placement_evaluations_total = Counter(
    "dim_placement_evaluations_total",
    "Number of placement evaluations executed",
    ["result_tier"],
)
placement_evaluation_duration = Summary(
    "dim_placement_evaluation_duration_seconds",
    "Latency of placement evaluation loop",
)
placement_heat_gauge = Gauge(
    "dim_file_heat_score",
    "Latest heat score per object",
    ["key"],
)


simulate_events_total = Counter(
    "dim_simulated_access_events_total",
    "Synthetic access events generated via API",
)

migration_jobs_total = Counter(
    "dim_migration_jobs_total",
    "Migration task outcomes",
    ["result"],
)
migration_queue_gauge = Gauge(
    "dim_migration_tasks",
    "Count of migration tasks per status",
    ["status"],
)
