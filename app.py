import streamlit as st
import pandas as pd
import numpy as np
import json
import os
from datetime import date, datetime
import plotly.graph_objects as go
import plotly.express as px

# ─────────────────────────────────────────────────────────────────────────────
#  PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="EPC Project Intelligence Portal",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
#  DATA PERSISTENCE (session state + JSON)
# ─────────────────────────────────────────────────────────────────────────────
DATA_FILE = "epc_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"milestones": [], "daily_logs": []}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

if "data" not in st.session_state:
    st.session_state.data = load_data()

def get_data():
    return st.session_state.data

def persist(data):
    st.session_state.data = data
    save_data(data)

# ─────────────────────────────────────────────────────────────────────────────
#  SCORING ENGINE (unchanged logic)
# ─────────────────────────────────────────────────────────────────────────────
def build_logs_df(daily_logs, milestone_id=None):
    if not daily_logs:
        return pd.DataFrame(columns=["date", "milestone_id", "wages", "materials", "machinery", "notes", "total"])
    df = pd.DataFrame(daily_logs)
    df["total"] = df["wages"] + df["materials"] + df["machinery"]
    df["date"] = pd.to_datetime(df["date"])
    if milestone_id:
        df = df[df["milestone_id"] == milestone_id]
    return df.reset_index(drop=True)

def score_milestone(ms, all_logs):
    logs_df = build_logs_df(all_logs, ms["id"])
    deadline_days = ms["deadline_days"]
    total_budget = ms["total_cost"]
    days_logged = len(logs_df)

    if days_logged > 0:
        avg_daily = logs_df["total"].mean()
        total_spent = logs_df["total"].sum()
        wages_spent = logs_df["wages"].sum()
        mat_spent = logs_df["materials"].sum()
        mach_spent = logs_df["machinery"].sum()
    else:
        avg_daily = total_budget / max(deadline_days, 1)
        total_spent = 0.0
        wages_spent = mat_spent = mach_spent = 0.0

    projected_total = avg_daily * deadline_days

    budget_pressure = min(projected_total / max(total_budget, 1), 2.0)
    pct_time = min(days_logged / max(deadline_days, 1), 1.0)
    urgency = 0.35 if deadline_days <= 7 else 0.15 if deadline_days <= 14 else 0.0
    PoD = min(0.95, budget_pressure * 0.35 + pct_time * 0.20 + urgency)

    CoD_per_day = avg_daily

    if deadline_days <= 3:   CFTS = 1.00
    elif deadline_days <= 7:  CFTS = 0.85
    elif deadline_days <= 14: CFTS = 0.65
    elif deadline_days <= 30: CFTS = 0.40
    else:                     CFTS = 0.20

    CoD_norm = min(CoD_per_day * deadline_days / max(total_budget, 1), 1.0)
    raw_score = (PoD * 0.40 + CoD_norm * 0.35 + CFTS * 0.25) * 100
    score = int(min(100, round(raw_score)))

    pct_spent = total_spent / max(total_budget, 1)
    burn_efficiency = (pct_spent / pct_time) if pct_time > 0.05 else 1.0
    remaining_budget = max(total_budget - total_spent, 0)
    days_of_cash_left = (remaining_budget / avg_daily) if avg_daily > 0 else deadline_days

    return {
        "score": score, "PoD": round(PoD, 3), "CoD_per_day": round(CoD_per_day, 2),
        "CFTS": round(CFTS, 2), "CoD_norm": round(CoD_norm, 3),
        "avg_daily": round(avg_daily, 2), "total_spent": round(total_spent, 2),
        "wages_spent": round(wages_spent, 2), "mat_spent": round(mat_spent, 2),
        "mach_spent": round(mach_spent, 2), "projected_total": round(projected_total, 2),
        "pct_spent": round(pct_spent, 4), "pct_time": round(pct_time, 4),
        "burn_efficiency": round(burn_efficiency, 3), "days_logged": days_logged,
        "days_of_cash_left": round(days_of_cash_left, 1),
        "remaining_budget": round(remaining_budget, 2),
    }

def risk_label(score):
    if score >= 70: return "🔴 CRITICAL"
    if score >= 45: return "🟡 HIGH"
    if score >= 25: return "🔵 MEDIUM"
    return "🟢 LOW"

def risk_color(score):
    if score >= 70: return "#FF4B4B"
    if score >= 45: return "#FFA500"
    if score >= 25: return "#00C0F0"
    return "#00CC66"

def generate_suggestions(ms, metrics):
    sugg = []
    be = metrics["burn_efficiency"]
    PoD = metrics["PoD"]
    d = ms["deadline_days"]
    pc_time = metrics["pct_time"]

    if be > 1.40:
        pct_over = round((be - 1) * 100)
        sugg.append(("⚡ OVERSPEND", "error", f"Burning {pct_over}% faster than schedule. Immediate review of labour allocation and machinery hours required."))
    elif be > 1.20:
        sugg.append(("⚠ PACE RISK", "warning", f"Spend pace {round((be - 1) * 100)}% above plan. Verify that physical progress matches expenditure."))
    if be < 0.55 and pc_time > 0.25:
        sugg.append(("🐢 SLOW BURN", "info", f"Only {round(metrics['pct_spent'] * 100)}% of budget used at {round(pc_time * 100)}% of timeline. Confirm physical progress — risk of back-loaded cost surge."))
    if d <= 7 and PoD > 0.35:
        sugg.append(("🚨 DEADLINE CRITICAL", "error", f"Deadline in {d} days with {round(PoD * 100)}% delay probability. Trigger resource surge and escalate to senior PM."))

    labourers = ms.get("labourers", [])
    if labourers:
        total_labour_cost = sum(l["count"] * l["daily_rate"] * l["days"] for l in labourers)
        if total_labour_cost > ms["total_cost"] * 0.60:
            total_workers = sum(l["count"] for l in labourers)
            sugg.append(("💰 LABOUR OPTIMISE", "warning", f"Labour = {round(total_labour_cost / ms['total_cost'] * 100)}% of budget. Audit utilisation of all {total_workers} workers; redeploy idle staff to critical-path activities."))

    machines = ms.get("machines", [])
    if machines and be > 1.05:
        names = ", ".join(m["name"] for m in machines[:2])
        sugg.append(("⚙ MACHINERY SAVINGS", "info", f"Machinery costs elevated. Shift {names} to off-peak hours or consider returning idle units to reduce rental spend."))

    materials = ms.get("materials", [])
    if materials and metrics["mat_spent"] > ms["total_cost"] * 0.40:
        sugg.append(("📦 MATERIAL REVIEW", "warning", f"Material spend is high relative to total budget. Review procurement schedule — avoid over-ordering to maintain cash buffer."))

    if metrics["days_of_cash_left"] < 10:
        sugg.append(("🏦 CASH ALERT", "error", f"Only {metrics['days_of_cash_left']} days of cash runway remaining at current burn rate. Activate overdraft facility or accelerate billing trigger."))

    if metrics["projected_total"] > ms["total_cost"] * 1.10:
        ovr = round((metrics["projected_total"] / ms["total_cost"] - 1) * 100)
        sugg.append(("📊 BUDGET OVERRUN", "error", f"Projected to exceed budget by {ovr}%. Renegotiate scope or reduce resource intensity immediately."))

    if not sugg:
        sugg.append(("✅ ON TRACK", "success", "Milestone is within budget and timeline. Maintain current execution pace."))
    return sugg

# ─────────────────────────────────────────────────────────────────────────────
#  CUSTOM CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    [data-testid="stAppViewContainer"] { background-color: #0e1117; }
    .metric-card {
        background: linear-gradient(135deg, #1a1f2e, #16213e);
        border: 1px solid #2d3561;
        border-radius: 12px;
        padding: 18px 22px;
        margin-bottom: 10px;
    }
    .metric-card h3 { color: #a0aec0; font-size: 0.78rem; margin: 0; text-transform: uppercase; letter-spacing: 1px; }
    .metric-card h2 { color: #ffffff; font-size: 1.6rem; margin: 4px 0 0 0; font-weight: 700; }
    .score-badge {
        display: inline-block;
        padding: 4px 14px;
        border-radius: 20px;
        font-weight: 700;
        font-size: 0.85rem;
    }
    .risk-row { padding: 10px; border-radius: 8px; margin-bottom: 6px; }
    .section-header {
        color: #63b3ed;
        font-size: 0.9rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 2px;
        border-bottom: 1px solid #2d3561;
        padding-bottom: 6px;
        margin: 20px 0 12px 0;
    }
    .stTabs [data-baseweb="tab"] { color: #a0aec0; font-size: 0.9rem; }
    .stTabs [aria-selected="true"] { color: #63b3ed !important; }
    div[data-testid="stMetricValue"] { color: #ffffff; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
#  SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🏗️ EPC Intelligence Portal")
    st.markdown("---")
    page = st.radio(
        "Navigation",
        ["📊 Dashboard", "➕ Add Milestone", "📝 Log Daily Spend", "🔍 Milestone Detail", "📤 Export Report"],
        label_visibility="collapsed"
    )
    st.markdown("---")
    data = get_data()
    st.markdown(f"**Milestones:** {len(data['milestones'])}")
    st.markdown(f"**Logs Recorded:** {len(data['daily_logs'])}")
    st.markdown("---")
    st.markdown("""
    <div style='font-size:0.75rem; color:#718096;'>
    <b>Scoring Formula</b><br>
    PoD × 0.40<br>
    + CoD_norm × 0.35<br>
    + CFTS × 0.25<br>
    = Risk Score (0–100)
    </div>
    """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
#  PAGE: DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
if page == "📊 Dashboard":
    st.markdown("# 📊 Project Intelligence Dashboard")
    data = get_data()
    milestones = data["milestones"]
    logs = data["daily_logs"]

    if not milestones:
        st.info("No milestones yet. Use **➕ Add Milestone** in the sidebar to get started.")
        st.stop()

    # Score all milestones
    scored = []
    for ms in milestones:
        m = score_milestone(ms, logs)
        scored.append({**ms, **m})
    scored.sort(key=lambda x: x["score"], reverse=True)

    # ── Portfolio KPIs ────────────────────────────────────────────────────
    total_budget = sum(s["total_cost"] for s in scored)
    total_spent = sum(s["total_spent"] for s in scored)
    total_remain = sum(s["remaining_budget"] for s in scored)
    critical = sum(1 for s in scored if s["score"] >= 70)
    high = sum(1 for s in scored if 45 <= s["score"] < 70)
    avg_score = np.mean([s["score"] for s in scored])

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Total Budget", f"${total_budget:,.0f}")
    with c2:
        st.metric("Total Spent", f"${total_spent:,.0f}", delta=f"-${total_remain:,.0f} remaining")
    with c3:
        st.metric("Critical Milestones", critical, delta="Needs action" if critical else "All clear")
    with c4:
        st.metric("High Risk", high)
    with c5:
        st.metric("Avg Risk Score", f"{avg_score:.1f}/100")

    st.markdown("---")

    # ── Gauge Chart ───────────────────────────────────────────────────────
    col_gauge, col_bar = st.columns([1, 2])
    with col_gauge:
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number",
            value=avg_score,
            title={"text": "Portfolio Risk", "font": {"color": "#a0aec0"}},
            gauge={
                "axis": {"range": [0, 100], "tickcolor": "#a0aec0"},
                "bar": {"color": risk_color(avg_score)},
                "bgcolor": "#1a1f2e",
                "steps": [
                    {"range": [0, 25], "color": "#0d2b1a"},
                    {"range": [25, 45], "color": "#0d1f2b"},
                    {"range": [45, 70], "color": "#2b2200"},
                    {"range": [70, 100], "color": "#2b0d0d"},
                ],
                "threshold": {"line": {"color": "white", "width": 2}, "thickness": 0.75, "value": avg_score}
            },
            number={"font": {"color": "#ffffff"}}
        ))
        fig_gauge.update_layout(
            paper_bgcolor="#0e1117", font={"color": "#a0aec0"},
            height=250, margin=dict(l=20, r=20, t=40, b=20)
        )
        st.plotly_chart(fig_gauge, use_container_width=True)

    with col_bar:
        df_bar = pd.DataFrame([{
            "Milestone": s["title"][:25],
            "Score": s["score"],
            "Risk": risk_label(s["score"]),
            "Color": risk_color(s["score"])
        } for s in scored])
        fig_bar = px.bar(
            df_bar, x="Score", y="Milestone", orientation="h",
            color="Score", color_continuous_scale=["#00CC66", "#00C0F0", "#FFA500", "#FF4B4B"],
            range_color=[0, 100], text="Score"
        )
        fig_bar.update_layout(
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font={"color": "#a0aec0"}, height=250,
            margin=dict(l=10, r=10, t=10, b=10),
            coloraxis_showscale=False,
            yaxis={"autorange": "reversed"}
        )
        fig_bar.update_traces(textposition="outside")
        st.plotly_chart(fig_bar, use_container_width=True)

    # ── Risk Matrix Table ─────────────────────────────────────────────────
    st.markdown('<p class="section-header">Ranked Milestone Risk Matrix</p>', unsafe_allow_html=True)

    for s in scored:
        sc = s["score"]
        col = risk_color(sc)
        with st.container():
            r1, r2, r3, r4, r5, r6, r7, r8 = st.columns([3, 1, 1.5, 1.5, 1, 1, 1, 1.5])
            with r1: st.markdown(f"**{s['title'][:30]}**")
            with r2: st.markdown(f"⏱ {s['deadline_days']}d")
            with r3: st.markdown(f"💰 ${s['total_cost']:,.0f}")
            with r4: st.markdown(f"📉 ${s['total_spent']:,.0f} spent")
            with r5: st.markdown(f"PoD: **{round(s['PoD']*100)}%**")
            with r6: st.markdown(f"CoD: ${s['CoD_per_day']:,.0f}/d")
            with r7: st.markdown(f"CFTS: {s['CFTS']}")
            with r8:
                st.markdown(
                    f'<span class="score-badge" style="background:{col}22; color:{col}; border:1px solid {col};">'
                    f'{sc}/100 &nbsp; {risk_label(sc)}</span>',
                    unsafe_allow_html=True
                )
        st.progress(sc / 100)
        st.markdown("")

# ─────────────────────────────────────────────────────────────────────────────
#  PAGE: ADD MILESTONE
# ─────────────────────────────────────────────────────────────────────────────
elif page == "➕ Add Milestone":
    st.markdown("# ➕ Add New Milestone")

    # Initialise counters in session state so changing them triggers a re-render
    for key, default in [("n_labour", 1), ("n_mat", 1), ("n_mach", 1)]:
        if key not in st.session_state:
            st.session_state[key] = default

    # ── Basic Info ────────────────────────────────────────────────────────
    st.markdown('<p class="section-header">Basic Info</p>', unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1:
        title = st.text_input("Milestone Title", placeholder="e.g. Foundation Works", key="ms_title")
    with col2:
        deadline_days = st.number_input("Deadline (days from today)", min_value=1, value=30, key="ms_deadline")
    with col3:
        total_cost = st.number_input("Total Contract Value ($)", min_value=0.0, value=50000.0, step=1000.0, key="ms_cost")
    phases = st.number_input("Number of Phases", min_value=1, value=1, key="ms_phases")

    # ── Labourers ─────────────────────────────────────────────────────────
    st.markdown('<p class="section-header">👷 Labourers</p>', unsafe_allow_html=True)
    cnt_col, _, __ = st.columns([1, 1, 2])
    with cnt_col:
        st.number_input(
            "Number of labourer categories", min_value=0, max_value=10,
            key="n_labour"
        )
    labourers = []
    for i in range(int(st.session_state.n_labour)):
        lc1, lc2, lc3, lc4 = st.columns(4)
        with lc1: lname = st.text_input(f"Role {i+1}", value=f"Category {i+1}", key=f"ln_{i}")
        with lc2: lcount = st.number_input(f"Workers {i+1}", min_value=1, value=5, key=f"lc_{i}")
        with lc3: lrate = st.number_input(f"Daily Rate ($) {i+1}", min_value=0.0, value=120.0, key=f"lr_{i}")
        with lc4: ldays = st.number_input(f"Days Hired {i+1}", min_value=1, value=int(deadline_days), key=f"ld_{i}")
        labourers.append({"name": lname, "count": int(lcount), "daily_rate": float(lrate), "days": int(ldays)})

    # ── Materials ─────────────────────────────────────────────────────────
    st.markdown('<p class="section-header">📦 Materials</p>', unsafe_allow_html=True)
    cnt_col2, _, __ = st.columns([1, 1, 2])
    with cnt_col2:
        st.number_input(
            "Number of materials", min_value=0, max_value=15,
            key="n_mat"
        )
    materials = []
    for i in range(int(st.session_state.n_mat)):
        mc1, mc2, mc3 = st.columns(3)
        with mc1: mname = st.text_input(f"Material {i+1}", value=f"Material {i+1}", key=f"mn_{i}")
        with mc2: mquant = st.number_input(f"Quantity {i+1}", min_value=0.0, value=100.0, key=f"mq_{i}")
        with mc3: munit = st.number_input(f"Unit Cost ($) {i+1}", min_value=0.0, value=10.0, key=f"mu_{i}")
        materials.append({"name": mname, "quantity": float(mquant), "unit_cost": float(munit)})

    # ── Machinery ─────────────────────────────────────────────────────────
    st.markdown('<p class="section-header">⚙️ Machinery</p>', unsafe_allow_html=True)
    cnt_col3, _, __ = st.columns([1, 1, 2])
    with cnt_col3:
        st.number_input(
            "Number of machine types", min_value=0, max_value=10,
            key="n_mach"
        )
    machines = []
    for i in range(int(st.session_state.n_mach)):
        xc1, xc2, xc3, xc4 = st.columns(4)
        with xc1: xname = st.text_input(f"Machine {i+1}", value=f"Machine {i+1}", key=f"xn_{i}")
        with xc2: xcount = st.number_input(f"Units {i+1}", min_value=1, value=1, key=f"xc_{i}")
        with xc3: xrate = st.number_input(f"Daily Rate ($) {i+1}", min_value=0.0, value=500.0, key=f"xr_{i}")
        with xc4: xdays = st.number_input(f"Days {i+1}", min_value=1, value=int(deadline_days), key=f"xd_{i}")
        machines.append({"name": xname, "count": int(xcount), "daily_rate": float(xrate), "days": int(xdays)})

    # ── Save Button ───────────────────────────────────────────────────────
    st.markdown("")
    submitted = st.button("✅ Save Milestone", use_container_width=True, type="primary")

    if submitted and title:
        ms_id = f"MS{len(get_data()['milestones'])+1:03d}_{int(datetime.now().timestamp())}"
        milestone = {
            "id": ms_id, "title": title,
            "deadline_days": int(deadline_days), "phases": int(phases),
            "total_cost": float(total_cost), "labourers": labourers,
            "materials": materials, "machines": machines,
            "created_at": str(date.today()),
        }
        d = get_data()
        d["milestones"].append(milestone)
        persist(d)

        # Reset counters for next milestone
        st.session_state.n_labour = 1
        st.session_state.n_mat = 1
        st.session_state.n_mach = 1

        planned_labour = sum(l["count"] * l["daily_rate"] * l["days"] for l in labourers)
        planned_material = sum(m["quantity"] * m["unit_cost"] for m in materials)
        planned_machine = sum(m["count"] * m["daily_rate"] * m["days"] for m in machines)
        planned_total = planned_labour + planned_material + planned_machine
        variance = total_cost - planned_total

        st.success(f"✅ Milestone **{title}** saved! ID: `{ms_id}`")
        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("Contract Value", f"${total_cost:,.2f}")
        sc2.metric("Planned Spend", f"${planned_total:,.2f}")
        sc3.metric("Variance", f"${variance:+,.2f}")
        sc4.metric("Budget Status", "✅ OK" if variance >= 0 else "⚠️ Over")
    elif submitted:
        st.error("Please enter a milestone title.")

# ─────────────────────────────────────────────────────────────────────────────
#  PAGE: LOG DAILY SPEND
# ─────────────────────────────────────────────────────────────────────────────
elif page == "📝 Log Daily Spend":
    st.markdown("# 📝 Log Daily Spending")
    data = get_data()
    milestones = data["milestones"]

    if not milestones:
        st.info("Add a milestone first.")
        st.stop()

    options = {ms["title"]: ms for ms in milestones}
    selected_name = st.selectbox("Select Milestone", list(options.keys()))
    ms = options[selected_name]
    m = score_milestone(ms, data["daily_logs"])

    budget_per_day = ms["total_cost"] / max(ms["deadline_days"], 1)
    st.markdown(f"**Budget per day:** ${budget_per_day:,.2f} &nbsp;|&nbsp; **Days left:** {ms['deadline_days']} &nbsp;|&nbsp; **Current risk score:** {m['score']}/100 {risk_label(m['score'])}")
    st.markdown("---")

    with st.form("log_form", clear_on_submit=True):
        log_date = st.date_input("Date", value=date.today())
        lc1, lc2, lc3 = st.columns(3)
        with lc1: wages = st.number_input("Wages ($)", min_value=0.0, value=0.0, step=100.0)
        with lc2: mats = st.number_input("Materials ($)", min_value=0.0, value=0.0, step=100.0)
        with lc3: mach = st.number_input("Machinery ($)", min_value=0.0, value=0.0, step=100.0)
        notes = st.text_input("Notes (optional)")
        log_submitted = st.form_submit_button("💾 Save Log Entry", use_container_width=True)

    if log_submitted:
        total_today = wages + mats + mach
        log = {
            "id": f"LOG_{int(datetime.now().timestamp())}",
            "milestone_id": ms["id"],
            "date": str(log_date),
            "wages": wages, "materials": mats, "machinery": mach,
            "notes": notes,
        }
        data["daily_logs"].append(log)
        persist(data)

        col_status = "🔴 Over Budget" if total_today > budget_per_day else "🟢 Within Budget"
        st.success(f"Log saved! Today's spend: **${total_today:,.2f}** ({col_status})")

        new_m = score_milestone(ms, data["daily_logs"])
        nc1, nc2, nc3 = st.columns(3)
        nc1.metric("Today's Spend", f"${total_today:,.2f}", delta=f"${total_today - budget_per_day:+,.2f} vs budget")
        nc2.metric("New Risk Score", f"{new_m['score']}/100")
        nc3.metric("Cash Runway", f"{new_m['days_of_cash_left']:.1f} days")

    # Show recent logs for this milestone
    logs_df = build_logs_df(data["daily_logs"], ms["id"])
    if not logs_df.empty:
        st.markdown("---")
        st.markdown("**Recent Logs**")
        logs_df["date"] = logs_df["date"].dt.strftime("%Y-%m-%d")
        logs_df["total"] = logs_df["wages"] + logs_df["materials"] + logs_df["machinery"]
        st.dataframe(
            logs_df[["date", "wages", "materials", "machinery", "total", "notes"]].tail(10).reset_index(drop=True),
            use_container_width=True
        )

# ─────────────────────────────────────────────────────────────────────────────
#  PAGE: MILESTONE DETAIL
# ─────────────────────────────────────────────────────────────────────────────
elif page == "🔍 Milestone Detail":
    st.markdown("# 🔍 Milestone Detail")
    data = get_data()
    milestones = data["milestones"]

    if not milestones:
        st.info("No milestones yet.")
        st.stop()

    options = {ms["title"]: ms for ms in milestones}
    selected_name = st.selectbox("Select Milestone", list(options.keys()))
    ms = options[selected_name]
    m = score_milestone(ms, data["daily_logs"])
    sc = m["score"]

    st.markdown(f"### {ms['title']}")
    hc1, hc2, hc3, hc4 = st.columns(4)
    hc1.metric("Risk Score", f"{sc}/100", delta=risk_label(sc))
    hc2.metric("Deadline", f"{ms['deadline_days']} days")
    hc3.metric("PoD", f"{round(m['PoD']*100)}%")
    hc4.metric("Cash Runway", f"{m['days_of_cash_left']:.1f} days")

    tab1, tab2, tab3, tab4 = st.tabs(["📊 Score Breakdown", "💰 Budget", "📅 Daily Logs", "💡 Suggestions"])

    # ── Tab 1: Score Breakdown ────────────────────────────────────────────
    with tab1:
        st.markdown('<p class="section-header">Three-Layer Risk Score</p>', unsafe_allow_html=True)
        col_g, col_d = st.columns([1, 1])

        with col_g:
            layers = ["PoD (×0.40)", "CoD_norm (×0.35)", "CFTS (×0.25)"]
            values = [round(m["PoD"]*0.40*100,1), round(m["CoD_norm"]*0.35*100,1), round(m["CFTS"]*0.25*100,1)]
            colors = ["#FF4B4B", "#FFA500", "#00C0F0"]
            fig_pie = go.Figure(go.Pie(
                labels=layers, values=values, hole=0.55,
                marker=dict(colors=colors),
                textinfo="label+percent"
            ))
            fig_pie.update_layout(
                paper_bgcolor="#0e1117", font={"color": "#a0aec0"},
                height=280, showlegend=False,
                margin=dict(l=0, r=0, t=20, b=0),
                annotations=[{"text": f"<b>{sc}</b>", "font": {"size": 26, "color": "#fff"}, "showarrow": False}]
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_d:
            st.markdown("")
            for layer, val, norm, weight, desc in [
                ("Layer 1 — Probability of Delay", f"{round(m['PoD']*100)}%", m["PoD"], 0.40,
                 "Budget burn rate, time consumed, urgency pressure"),
                ("Layer 2 — Cost of Delay", f"${m['CoD_per_day']:,.0f}/day", m["CoD_norm"], 0.35,
                 "Daily bleeding cost if milestone slips"),
                ("Layer 3 — CF Timing Sensitivity", f"{m['CFTS']:.2f}", m["CFTS"], 0.25,
                 "Proximity of cash-flow trigger"),
            ]:
                contrib = round(norm * weight * 100)
                st.markdown(f"**{layer}** — `{val}` → **+{contrib}pts**")
                st.progress(norm)
                st.caption(desc)
                st.markdown("")

    # ── Tab 2: Budget ─────────────────────────────────────────────────────
    with tab2:
        bc1, bc2 = st.columns(2)
        with bc1:
            st.metric("Total Budget", f"${ms['total_cost']:,.2f}")
            st.metric("Total Spent", f"${m['total_spent']:,.2f}")
            st.metric("Remaining", f"${m['remaining_budget']:,.2f}")
            st.metric("Projected Total", f"${m['projected_total']:,.2f}",
                      delta="⚠ Overrun" if m["projected_total"] > ms["total_cost"] else "✅ OK")
            st.metric("Burn Efficiency", f"{m['burn_efficiency']:.2f}x",
                      delta="Overspending" if m["burn_efficiency"] > 1.2 else "Normal")
            st.metric("Avg Daily Spend", f"${m['avg_daily']:,.2f}/day")

        with bc2:
            spent_data = {
                "Category": ["Wages", "Materials", "Machinery", "Remaining"],
                "Amount": [m["wages_spent"], m["mat_spent"], m["mach_spent"], m["remaining_budget"]]
            }
            fig_donut = go.Figure(go.Pie(
                labels=spent_data["Category"],
                values=spent_data["Amount"],
                hole=0.5,
                marker=dict(colors=["#FF4B4B", "#FFA500", "#00C0F0", "#00CC66"])
            ))
            fig_donut.update_layout(
                paper_bgcolor="#0e1117", font={"color": "#a0aec0"},
                height=300, margin=dict(l=0, r=0, t=20, b=0)
            )
            st.plotly_chart(fig_donut, use_container_width=True)

        # Resources table
        if ms.get("labourers"):
            st.markdown("**👷 Labourers**")
            ldf = pd.DataFrame(ms["labourers"])
            ldf["total_cost"] = ldf["count"] * ldf["daily_rate"] * ldf["days"]
            st.dataframe(ldf, use_container_width=True)
        if ms.get("materials"):
            st.markdown("**📦 Materials**")
            mdf = pd.DataFrame(ms["materials"])
            mdf["total_cost"] = mdf["quantity"] * mdf["unit_cost"]
            st.dataframe(mdf, use_container_width=True)
        if ms.get("machines"):
            st.markdown("**⚙️ Machines**")
            xdf = pd.DataFrame(ms["machines"])
            xdf["total_cost"] = xdf["count"] * xdf["daily_rate"] * xdf["days"]
            st.dataframe(xdf, use_container_width=True)

    # ── Tab 3: Daily Logs ─────────────────────────────────────────────────
    with tab3:
        logs_df = build_logs_df(data["daily_logs"], ms["id"])
        if logs_df.empty:
            st.info("No logs yet for this milestone.")
        else:
            logs_df_s = logs_df.sort_values("date")
            logs_df_s["rolling7"] = logs_df_s["total"].rolling(7, min_periods=1).mean()
            budget_per_day = ms["total_cost"] / max(ms["deadline_days"], 1)

            fig_line = go.Figure()
            fig_line.add_trace(go.Bar(
                x=logs_df_s["date"], y=logs_df_s["total"],
                name="Daily Spend", marker_color="#63b3ed", opacity=0.6
            ))
            fig_line.add_trace(go.Scatter(
                x=logs_df_s["date"], y=logs_df_s["rolling7"],
                name="7-Day Avg", line=dict(color="#FFA500", width=2.5)
            ))
            fig_line.add_hline(y=budget_per_day, line_dash="dash",
                               line_color="#FF4B4B", annotation_text="Budget/Day")
            fig_line.update_layout(
                paper_bgcolor="#0e1117", plot_bgcolor="#1a1f2e",
                font={"color": "#a0aec0"}, height=300,
                legend=dict(bgcolor="#0e1117"),
                margin=dict(l=10, r=10, t=20, b=10)
            )
            st.plotly_chart(fig_line, use_container_width=True)

            display_df = logs_df_s.copy()
            display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d")
            st.dataframe(
                display_df[["date", "wages", "materials", "machinery", "total", "notes"]].reset_index(drop=True),
                use_container_width=True
            )

    # ── Tab 4: Suggestions ────────────────────────────────────────────────
    with tab4:
        suggs = generate_suggestions(ms, m)
        for tag, kind, text in suggs:
            if kind == "error":
                st.error(f"**{tag}** — {text}")
            elif kind == "warning":
                st.warning(f"**{tag}** — {text}")
            elif kind == "info":
                st.info(f"**{tag}** — {text}")
            else:
                st.success(f"**{tag}** — {text}")

    # Delete option
    st.markdown("---")
    with st.expander("⚠️ Danger Zone"):
        if st.button("🗑️ Delete This Milestone", type="secondary"):
            d = get_data()
            d["milestones"] = [m2 for m2 in d["milestones"] if m2["id"] != ms["id"]]
            d["daily_logs"] = [l for l in d["daily_logs"] if l["milestone_id"] != ms["id"]]
            persist(d)
            st.success("Milestone deleted.")
            st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
#  PAGE: EXPORT REPORT
# ─────────────────────────────────────────────────────────────────────────────
elif page == "📤 Export Report":
    st.markdown("# 📤 Export Risk Report")
    data = get_data()
    milestones = data["milestones"]

    if not milestones:
        st.info("No milestones to export.")
        st.stop()

    records = []
    for ms in milestones:
        m = score_milestone(ms, data["daily_logs"])
        suggs = generate_suggestions(ms, m)
        records.append({
            "Milestone ID": ms["id"], "Title": ms["title"],
            "Deadline (days)": ms["deadline_days"],
            "Total Budget ($)": ms["total_cost"],
            "Total Spent ($)": m["total_spent"],
            "Remaining Budget ($)": m["remaining_budget"],
            "Projected Total ($)": m["projected_total"],
            "Avg Daily Spend ($)": m["avg_daily"],
            "PoD (0-1)": m["PoD"], "CoD per Day ($)": m["CoD_per_day"],
            "CFTS (0-1)": m["CFTS"], "Risk Score (0-100)": m["score"],
            "Risk Level": risk_label(m["score"]),
            "Burn Efficiency": m["burn_efficiency"],
            "Days of Cash Left": m["days_of_cash_left"],
            "Days Logged": m["days_logged"],
            "Top Suggestion": suggs[0][2] if suggs else "",
        })

    df_export = pd.DataFrame(records).sort_values("Risk Score (0-100)", ascending=False)
    st.dataframe(df_export, use_container_width=True)

    csv = df_export.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download Risk Report CSV",
        data=csv,
        file_name=f"epc_risk_report_{date.today()}.csv",
        mime="text/csv",
        use_container_width=True
    )

    if data["daily_logs"]:
        logs_df_all = pd.DataFrame(data["daily_logs"])
        logs_df_all["total"] = logs_df_all["wages"] + logs_df_all["materials"] + logs_df_all["machinery"]
        csv2 = logs_df_all.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download Daily Logs CSV",
            data=csv2,
            file_name=f"epc_daily_logs_{date.today()}.csv",
            mime="text/csv",
            use_container_width=True
        )
