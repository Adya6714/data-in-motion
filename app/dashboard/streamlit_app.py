import os
import json
import requests
import pandas as pd
import streamlit as st
import plotly.express as px
import time
from typing import Any, Dict, List

API = os.getenv("API_URL", "http://localhost:8000")
TIMEOUT = float(os.getenv("DASH_TIMEOUT", "8"))
AUTO_REFRESH_MS = int(os.getenv("DASH_AUTO_REFRESH_MS", "5000"))

st.set_page_config(
    page_title="Data Autopilot",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🚀 Data Autopilot — Multi-Cloud Tiering Control")


@st.cache_data(ttl=5.0)
def fetch_json(path: str) -> Any:
    resp = requests.get(f"{API}{path}", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_endpoints() -> List[Dict[str, Any]]:
    return fetch_json("/endpoints")


def fetch_security_policy() -> Dict[str, Any]:
    return fetch_json("/policy/security")


def fetch_alerts(include_ack: bool = False) -> List[Dict[str, Any]]:
    flag = "true" if include_ack else "false"
    return fetch_json(f"/alerts?include_ack={flag}")


def post(path: str, **kwargs) -> Dict[str, Any]:
    resp = requests.post(f"{API}{path}", timeout=TIMEOUT, **kwargs)
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return {"status": "ok"}


def delete(path: str, **kwargs) -> Dict[str, Any]:
    resp = requests.delete(f"{API}{path}", timeout=TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp.json()


def security_controls():
    try:
        policy = fetch_security_policy()
        current = bool(policy.get("enforce", False))
    except Exception as exc:
        st.error(f"Unable to load security policy: {exc}")
        return
    new_val = st.toggle(
        "Enforce encrypted destinations",
        value=current,
        help="When enabled, only destinations flagged as encrypted are eligible for placement or migrations.",
    )
    if new_val != current:
        try:
            post("/policy/security", json={"enforce": new_val})
            fetch_json.clear()
            st.success("Security policy updated")
            st.experimental_rerun()
        except Exception as exc:
            st.error(f"Failed to update policy: {exc}")

def chaos_controls():
    st.subheader("Chaos Controls")
    try:
        status = fetch_json("/chaos/status")
    except Exception as exc:
        st.error(f"Unable to load chaos status: {exc}")
        return
    failed = status.get("failed_endpoints", [])
    st.caption(f"Failed endpoints: {', '.join(failed) if failed else 'none'}")
    cols = st.columns(2)
    with cols[0]:
        ep = st.text_input("Fail endpoint", key="chaos_fail_name")
        if st.button("Fail", key="chaos_fail_btn", use_container_width=True):
            if not ep:
                st.warning("Enter endpoint name (e.g., aws)")
            else:
                try:
                    post(f"/chaos/fail/{ep}")
                    fetch_json.clear()
                    st.success(f"Endpoint {ep} failed")
                    st.experimental_rerun()
                except Exception as exc:
                    st.error(f"Chaos fail failed: {exc}")
    with cols[1]:
        rep = st.text_input("Recover endpoint", key="chaos_recover_name")
        if st.button("Recover", key="chaos_recover_btn", use_container_width=True):
            if not rep:
                st.warning("Enter endpoint name")
            else:
                try:
                    post(f"/chaos/recover/{rep}")
                    fetch_json.clear()
                    st.success(f"Endpoint {rep} recovered")
                    st.experimental_rerun()
                except Exception as exc:
                    st.error(f"Chaos recover failed: {exc}")
    if st.button("Clear chaos", use_container_width=True):
        try:
            post("/chaos/clear")
            fetch_json.clear()
            st.success("Chaos cleared")
            st.experimental_rerun()
        except Exception as exc:
            st.error(f"Failed to clear chaos: {exc}")

def render_inventory(df: pd.DataFrame) -> str:
    tier_colors = {"hot": "#ff6b6b", "warm": "#ffa94d", "cold": "#74c0fc"}
    styled = df.copy()
    styled["replicas"] = styled["replicas"].apply(lambda xs: ", ".join(xs) if isinstance(xs, list) else xs or "")
    styled = styled.rename(
        columns={
            "key": "Key",
            "tier": "Tier",
            "primary": "Primary",
            "replicas": "Replicas",
            "access_1h": "Access (1h)",
            "access_24h": "Access (24h)",
        }
    )
    st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Tier": st.column_config.Column(
                width="small",
                help="Current storage tier",
            )
        },
    )
    st.caption("Select a dataset in the picker below for deep-dive insights.")
    tier_counts = df["tier"].value_counts().reset_index()
    tier_counts.columns = ["Tier", "Count"]
    chart = px.bar(
        tier_counts,
        x="Tier",
        y="Count",
        color="Tier",
        color_discrete_map=tier_colors,
        title="Tier Distribution",
    )
    chart.update_layout(showlegend=False, height=300, margin=dict(t=40, b=10))
    st.plotly_chart(chart, use_container_width=True)


def render_tasks(tasks: List[Dict[str, Any]]) -> None:
    with st.expander("Migration Tasks", expanded=False):

        if not tasks:
            st.info("No migration tasks at the moment.")
            return
        df = pd.DataFrame(tasks)
        st.dataframe(df, use_container_width=True, hide_index=True)


def detail_panel(key: str) -> None:
    try:
        data = fetch_json(f"/debug/placement/{key}")
    except Exception as exc:
        st.error(f"Could not load details: {exc}")
        return

    db = data.get("db_before", {})
    heat = data.get("heat", {})
    cols = st.columns(4)
    cols[0].metric("Tier", (db.get("tier") or "n/a").upper())
    cols[1].metric("Heat", f"{heat.get('score', 0):.1f}")
    cols[2].metric("Primary", db.get("primary") or "-")
    cols[3].metric("Replicas", ", ".join(db.get("replicas", [])) or "—")

    tabs = st.tabs(["Insights", "Placement Explain", "Raw JSON"])
    with tabs[0]:
        st.write("**Current Snapshot**")
        st.json(
            {
                "Access (1h)": db.get("access_1h"),
                "Access (24h)": db.get("access_24h"),
                "Last Access": db.get("last_access_ts"),
                "Heat Thresholds": {
                    "Boosted": heat.get("boosted"),
                    "Warm": heat.get("warm_threshold"),
                    "Hot": heat.get("hot_threshold"),
                },
            },
            expanded=False,
        )
    with tabs[1]:
        try:
            placement = fetch_json(f"/explain/{key}")
            st.json(placement, expanded=False)
        except Exception as exc:
            st.error(f"Explain failed: {exc}")
    with tabs[2]:
        st.json(data, expanded=False)


def action_buttons(selected_key: str | None):
    col1, col2, col3, col4, col5 = st.columns(5)

    if col1.button("Re-optimize All", use_container_width=True):
        with st.spinner("Re-optimizing…"):
            res = post("/optimize/all")
        fetch_json.clear()
        st.success(f"Re-optimized {res.get('updated', 0)}/{res.get('total', 0)} files")
        st.experimental_rerun()

    if col2.button("Run Migrator Tick", use_container_width=True):
        with st.spinner("Running migrator…"):
            post("/migrator/tick")
        fetch_json.clear()
        st.success("Migrator tick executed")
        st.experimental_rerun()

    if col3.button("Clear Failed Tasks", use_container_width=True):
        with st.spinner("Clearing failed tasks…"):
            delete("/tasks", params={"status": "failed"})
        fetch_json.clear()
        st.success("Failed tasks cleared")
        st.experimental_rerun()

    disable_burst = selected_key is None
    if col4.button("Burst 100", disabled=disable_burst, use_container_width=True):
        if selected_key:
            with st.spinner(f"Bursting {selected_key}…"):
                post("/simulate", params={"key": selected_key, "events": 100})
            fetch_json.clear()
            st.success(f"Burst queued for {selected_key}")
            st.experimental_rerun()

    if col5.button("Chaos Spike (500)", disabled=disable_burst, use_container_width=True):
        if selected_key:
            with st.spinner(f"Chaos spike on {selected_key}…"):
                post("/simulate", params={"key": selected_key, "events": 500})
            fetch_json.clear()
            st.success(f"Chaos spike queued for {selected_key}")
            st.experimental_rerun()


def migrator_fast_forward():
    if st.button("Drain Migrator (5 ticks)", use_container_width=True):
        with st.spinner("Draining migrator queue…"):
            for _ in range(5):
                post("/migrator/tick")
        fetch_json.clear()
        st.success("Migrator drained")
        st.experimental_rerun()


def advanced_migration_section(endpoints: List[Dict[str, Any]]):
    st.subheader("Advanced Migration Tools (rclone / s5cmd simulators)")
    endpoint_names = [e["name"] for e in endpoints]
    cols = st.columns(2)
    with cols[0]:
        with st.form("rclone_form"):
            src = st.selectbox("Source", endpoint_names, key="rclone_src")
            dst = st.selectbox("Destination", endpoint_names, key="rclone_dst")
            prefix = st.text_input("Prefix (optional)", key="rclone_prefix")
            submitted = st.form_submit_button("Run rclone sync", use_container_width=True)
            if submitted:
                with st.spinner("Running rclone sync…"):
                    res = post("/tools/rclone", json={"src": src, "dst": dst, "prefix": prefix or None})
                st.success(f"Copied {res.get('copied',0)} objects ({res.get('bytes',0)} bytes)")


def render_alerts():
    st.subheader("Active Alerts")
    try:
        data = fetch_alerts()
    except Exception as exc:
        st.error(f"Unable to load alerts: {exc}")
        return
    if not data:
        st.success("No active alerts 🎉")
        return
    df = pd.DataFrame(data)
    st.dataframe(df[["id","severity","type","message","created_at"]], use_container_width=True, hide_index=True)
    cols = st.columns(2)
    if cols[0].button("Acknowledge All", use_container_width=True):
        try:
            for row in data:
                post(f"/alerts/{row['id']}/ack")
            fetch_json.clear()
            st.success("All alerts acknowledged")
            st.experimental_rerun()
        except Exception as exc:
            st.error(f"Ack failed: {exc}")
    if cols[1].button("Clear Alerts", use_container_width=True):
        try:
            post("/alerts/clear")
            fetch_json.clear()
            st.success("Alerts cleared")
            st.experimental_rerun()
        except Exception as exc:
            st.error(f"Clear failed: {exc}")


def main():
    try:
        files = fetch_json("/files")
        tasks = fetch_json("/tasks")
        endpoints = fetch_endpoints()
    except Exception as exc:
        st.error(f"API unavailable: {exc}")
        st.stop()

    df = pd.DataFrame(files)
    if df.empty:
        st.warning("No files found. Run bootstrap or simulator.")
        return

    with st.sidebar:
        st.header("Policies & Sites")
        security_controls()
        chaos_controls()
        st.markdown("**Endpoints**")
        for ep in endpoints or []:
            lock = "🔒" if ep.get("encrypted", True) else "🔓"
            st.caption(f"{lock} {ep['name']} ({ep.get('latency_ms', '?')} ms / ${ep.get('cost_per_gb', '?')}/GB)")
        
        # Initialize session state for pause_refresh if not exists
        if "pause_refresh" not in st.session_state:
            st.session_state.pause_refresh = False
        
        pause_refresh = st.checkbox(
            "Pause auto-refresh",
            value=st.session_state.pause_refresh,
            key="pause_refresh_toggle",
            help="Check to pause automatic dashboard refresh while presenting."
        )
        
        # Update session state when checkbox changes
        st.session_state.pause_refresh = pause_refresh

    st.caption(f"{len(df)} datasets • {sum(1 for x in tasks if x['status']=='queued')} queued migrations")
    render_inventory(df)

    st.subheader("Inventory Detail")
    selected_key = st.selectbox("Dataset", options=df["key"].tolist(), index=0)
    action_buttons(selected_key)

    st.divider()
    render_alerts()
    st.divider()
    if selected_key:
        detail_panel(selected_key)

    migrator_fast_forward()
    render_tasks(tasks)
    advanced_migration_section(endpoints or [])
    
    # Auto-refresh logic at the end (only if not paused)
    # Use Streamlit's rerun with a delay to preserve session state
    if not st.session_state.get("pause_refresh", False):
        # Small delay then rerun - this preserves session state unlike meta refresh
        time.sleep(AUTO_REFRESH_MS / 1000.0)
        st.rerun()


if __name__ == "__main__":
    main()