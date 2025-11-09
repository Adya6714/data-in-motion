import dash
from dash import dcc, html, dash_table, Input, Output, State, callback
import requests, os
API=os.getenv("API_URL","http://localhost:8000")
app = dash.Dash(__name__); app.title="Data-in-Motion Dashboard"
app.layout = html.Div([
    html.H2("Data Distribution & Activity"),
    html.Div(id="status"),
    html.Button("Run Migrator Tick", id="tick", n_clicks=0),
    html.Button("Burst Selected (100)", id="burst", n_clicks=0, style={"marginLeft":"8px"}),
    html.Button("Explain Selected", id="explain_btn", n_clicks=0, style={"marginLeft":"8px"}),
    html.Button("Re-optimize All", id="reopt_all", n_clicks=0, style={"marginLeft":"8px"}),
    dcc.Interval(id="refresh", interval=2000, n_intervals=0),
    dash_table.DataTable(id="files", row_selectable="single", columns=[
        {"name":"Key","id":"key"},{"name":"Tier","id":"tier"},{"name":"Primary","id":"primary"},
        {"name":"Replicas","id":"replicas"},{"name":"Access(1h)","id":"access_1h"},{"name":"Access(24h)","id":"access_24h"}], page_size=10),
    dash_table.DataTable(id="tasks", columns=[
        {"name":"ID","id":"id"},{"name":"Key","id":"key"},{"name":"Src","id":"src"},
        {"name":"Dst","id":"dst"},{"name":"Status","id":"status"},{"name":"Error","id":"error"}], page_size=10),
    html.Pre(id="explain", style={"background":"#f7f7f7","padding":"8px","whiteSpace":"pre-wrap"})
])
@callback(Output("files","data"), Output("tasks","data"), Output("status","children"),
          Input("refresh","n_intervals"), Input("tick","n_clicks"))
def refresh(_, __):
    f = requests.get(API+"/files").json(); t = requests.get(API+"/tasks").json()
    return f, t, f"{len(f)} files · {len([x for x in t if x['status']=='queued'])} queued migrations"
server = app.server

@callback(Output("explain","children"),
          Input("burst","n_clicks"), Input("explain_btn","n_clicks"), Input("reopt_all","n_clicks"),
          State("files","data"), State("files","selected_rows"))
def on_actions(burst_clicks, explain_clicks, reopt_clicks, data, selected_rows):
    ctx = dash.callback_context
    if not ctx.triggered:
        return ""
    trig = ctx.triggered[0]["prop_id"].split(".")[0]
    if trig=="reopt_all" and reopt_clicks:
        try:
            r = requests.post(API+"/optimize/all")
            res = r.json()
            return f"Re-optimized {res.get('updated', 0)}/{res.get('total', 0)} files"
        except Exception as e:
            return str(e)
    if trig=="burst" and burst_clicks:
        if not data or not selected_rows:
            return "Select a file first"
        key = data[selected_rows[0]]["key"]
        try:
            requests.post(API+"/simulate", params={"key": key, "events": 100})
        except Exception as e:
            return str(e)
        return f"Burst queued for {key}"
    if trig=="explain_btn" and explain_clicks:
        if not data or not selected_rows:
            return "Select a file first"
        key = data[selected_rows[0]]["key"]
        try:
            ex = requests.get(API+f"/explain/{key}").json()
            import json as _j
            return _j.dumps(ex, indent=2)
        except Exception as e:
            return str(e)
    return ""
