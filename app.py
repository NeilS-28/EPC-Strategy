import streamlit as st
import pandas as pd
import numpy as np
import json
import os
from datetime import date, datetime, timedelta
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
#  DATA PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────
DATA_FILE = "epc_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            d = json.load(f)
        # ── Migration: backfill fields added after initial release ──────────
        changed = False
        for ms in d.get("milestones", []):
            if "start_date" not in ms:
                # Use created_at if available, otherwise today
                ms["start_date"] = ms.get("created_at", str(date.today()))
                changed = True
        if changed:
            save_data(d)
        return d
    return {"milestones": []}

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
#  PLANNED SPEND ENGINE
#  Derives daily spend schedule purely from milestone parameters.
#  Materials are distributed evenly; labourers and machinery are active
#  only for their specified number of days starting from start_date.
# ─────────────────────────────────────────────────────────────────────────────
def compute_planned_schedule(ms):
    """
    Returns a DataFrame with one row per calendar day of the milestone,
    containing planned wages, materials, machinery, and total spend.
    """
    start = date.fromisoformat(ms["start_date"])
    deadline_days = ms["deadline_days"]
    days = [start + timedelta(days=i) for i in range(deadline_days)]

    labourers = ms.get("labourers", [])
    materials = ms.get("materials", [])
    machines = ms.get("machines", [])

    # Total material cost spread evenly across all days
    total_mat_cost = sum(m["quantity"] * m["unit_cost"] for m in materials)
    daily_mat = total_mat_cost / max(deadline_days, 1)

    rows = []
    for i, d in enumerate(days):
        # Wages: sum of all labourer categories active on this day
        wages = sum(
            l["count"] * l["daily_rate"]
            for l in labourers
            if i < l["days"]          # active for first l["days"] days
        )
        # Machinery: active for first m["days"] days
        mach = sum(
            m["count"] * m["daily_rate"]
            for m in machines
            if i < m["days"]
        )
        rows.append({
            "date": d,
            "wages": round(wages, 2),
            "materials": round(daily_mat, 2),
            "machinery": round(mach, 2),
            "total": round(wages + daily_mat + mach, 2),
            "day_index": i,
        })

    return pd.DataFrame(rows)


def get_actuals_up_to_today(ms):
    """
    Returns the planned schedule sliced to days that have already passed
    (from start_date up to and including today). These are the 'actuals'
    used for risk scoring — no manual log entry needed.
    """
    schedule = compute_planned_schedule(ms)
    today = date.today()
    actuals = schedule[schedule["date"] <= today].copy()
    return actuals


def days_elapsed(ms):
    start = date.fromisoformat(ms["start_date"])
    today = date.today()
    elapsed = (today - start).days + 1
    return max(0, min(elapsed, ms["deadline_days"]))


def days_remaining(ms):
    start = date.fromisoformat(ms["start_date"])
    end = start + timedelta(days=ms["deadline_days"])
    remaining = (end - date.today()).days
    return max(0, remaining)


# ─────────────────────────────────────────────────────────────────────────────
#  SCORING ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def score_milestone(ms):
    deadline_days = ms["deadline_days"]
    total_budget = ms["total_cost"]
    today = date.today()
    start = date.fromisoformat(ms["start_date"])
    elapsed = days_elapsed(ms)
    remaining = days_remaining(ms)

    actuals = get_actuals_up_to_today(ms)
    schedule = compute_planned_schedule(ms)

    if not actuals.empty and elapsed > 0:
        total_spent = actuals["total"].sum()
        wages_spent = actuals["wages"].sum()
        mat_spent = actuals["materials"].sum()
        mach_spent = actuals["machinery"].sum()
        avg_daily = actuals["total"].mean()
        projected_total = avg_daily * deadline_days
    else:
        total_spent = 0.0
        wages_spent = mat_spent = mach_spent = 0.0
        avg_daily = total_budget / max(deadline_days, 1)
        projected_total = total_budget

    days_logged = elapsed  # elapsed days = days with computed actuals

    # ── Layer 1: Probability of Delay ─────────────────────────────────────
    if elapsed > 0:
        budget_pressure = min(projected_total / max(total_budget, 1), 2.0)
        pct_time = min(elapsed / max(deadline_days, 1), 1.0)
        urgency = 0.35 if remaining <= 7 else 0.15 if remaining <= 14 else 0.0
        PoD = min(0.95, budget_pressure * 0.35 + pct_time * 0.20 + urgency)
    else:
        urgency = 0.35 if remaining <= 7 else 0.15 if remaining <= 14 else 0.0
        PoD = urgency
        pct_time = 0.0

    # ── Layer 2: Cost of Delay ────────────────────────────────────────────
    CoD_per_day = avg_daily
    CoD_norm = min(CoD_per_day * deadline_days / max(total_budget, 1), 1.0) if elapsed > 0 else 0.0

    # ── Layer 3: Cash Flow Timing Sensitivity ─────────────────────────────
    if remaining <= 3:    CFTS = 1.00
    elif remaining <= 7:  CFTS = 0.85
    elif remaining <= 14: CFTS = 0.65
    elif remaining <= 30: CFTS = 0.40
    else:                 CFTS = 0.20

    raw_score = (PoD * 0.40 + CoD_norm * 0.35 + CFTS * 0.25) * 100
    score = int(min(100, round(raw_score)))

    pct_spent = total_spent / max(total_budget, 1)
    burn_efficiency = (pct_spent / pct_time) if pct_time > 0.05 else 1.0
    remaining_budget = max(total_budget - total_spent, 0)
    days_of_cash_left = (remaining_budget / avg_daily) if avg_daily > 0 else remaining

    return {
        "score": score, "PoD": round(PoD, 3), "CoD_per_day": round(CoD_per_day, 2),
        "CFTS": round(CFTS, 2), "CoD_norm": round(CoD_norm, 3),
        "avg_daily": round(avg_daily, 2), "total_spent": round(total_spent, 2),
        "wages_spent": round(wages_spent, 2), "mat_spent": round(mat_spent, 2),
        "mach_spent": round(mach_spent, 2), "projected_total": round(projected_total, 2),
        "pct_spent": round(pct_spent, 4), "pct_time": round(pct_time, 4),
        "burn_efficiency": round(burn_efficiency, 3),
        "days_elapsed": elapsed, "days_remaining": remaining,
        "days_of_cash_left": round(days_of_cash_left, 1),
        "remaining_budget": round(remaining_budget, 2),
        "not_started": elapsed == 0,
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
    remaining = metrics["days_remaining"]
    pct_time = metrics["pct_time"]

    if be > 1.40:
        sugg.append(("⚡ OVERSPEND", "error",
            f"Burning {round((be-1)*100)}% faster than schedule. Review labour allocation and machinery hours immediately."))
    elif be > 1.20:
        sugg.append(("⚠ PACE RISK", "warning",
            f"Spend pace {round((be-1)*100)}% above plan. Verify physical progress matches expenditure."))
    if be < 0.55 and pct_time > 0.25:
        sugg.append(("🐢 SLOW BURN", "info",
            f"Only {round(metrics['pct_spent']*100)}% of budget used at {round(pct_time*100)}% of timeline. Risk of back-loaded cost surge."))
    if remaining <= 7 and PoD > 0.35:
        sugg.append(("🚨 DEADLINE CRITICAL", "error",
            f"Only {remaining} days left with {round(PoD*100)}% delay probability. Trigger resource surge and escalate."))

    labourers = ms.get("labourers", [])
    if labourers:
        total_labour = sum(l["count"] * l["daily_rate"] * l["days"] for l in labourers)
        if total_labour > ms["total_cost"] * 0.60:
            sugg.append(("💰 LABOUR OPTIMISE", "warning",
                f"Labour = {round(total_labour/ms['total_cost']*100)}% of budget. Audit utilisation; redeploy idle staff to critical-path activities."))

    machines = ms.get("machines", [])
    if machines and be > 1.05:
        names = ", ".join(m["name"] for m in machines[:2])
        sugg.append(("⚙ MACHINERY SAVINGS", "info",
            f"Machinery costs elevated. Shift {names} to off-peak hours or return idle units."))

    if metrics["days_of_cash_left"] < 10:
        sugg.append(("🏦 CASH ALERT", "error",
            f"Only {metrics['days_of_cash_left']:.1f} days of cash runway at current burn. Activate overdraft or accelerate billing trigger."))

    if metrics["projected_total"] > ms["total_cost"] * 1.10:
        ovr = round((metrics["projected_total"] / ms["total_cost"] - 1) * 100)
        sugg.append(("📊 BUDGET OVERRUN", "error",
            f"Projected to exceed budget by {ovr}%. Renegotiate scope or reduce resource intensity."))

    if not sugg:
        sugg.append(("✅ ON TRACK", "success",
            "Milestone is within budget and timeline. Maintain current execution pace."))
    return sugg

# ─────────────────────────────────────────────────────────────────────────────
#  RESOURCE FORM HELPER  (shared by Add + Edit)
# ─────────────────────────────────────────────────────────────────────────────
def render_resource_form(prefix, deadline_days, defaults=None):
    """
    Renders labourers / materials / machinery inputs.
    prefix   : unique string key prefix ("add" or "edit")
    defaults : existing milestone dict (for edit mode) or None
    Returns  : (labourers, materials, machines)
    """
    d = defaults or {}
    existing_l = d.get("labourers", [])
    existing_m = d.get("materials", [])
    existing_x = d.get("machines", [])

    nl_key = f"_{prefix}_nl"
    nm_key = f"_{prefix}_nm"
    nx_key = f"_{prefix}_nx"

    if nl_key not in st.session_state:
        st.session_state[nl_key] = max(len(existing_l), 1)
    if nm_key not in st.session_state:
        st.session_state[nm_key] = max(len(existing_m), 1)
    if nx_key not in st.session_state:
        st.session_state[nx_key] = max(len(existing_x), 1)

    # ── Labourers ─────────────────────────────────────────────────────────
    st.markdown('<p class="section-header">👷 Labourers</p>', unsafe_allow_html=True)
    c1, _, __ = st.columns([1, 1, 2])
    with c1:
        st.number_input("Labourer categories", min_value=0, max_value=10,
                        key=f"wgt_{prefix}_nl")
        st.session_state[nl_key] = int(st.session_state[f"wgt_{prefix}_nl"])
    labourers = []
    for i in range(st.session_state[nl_key]):
        ev = existing_l[i] if i < len(existing_l) else {}
        lc1, lc2, lc3, lc4 = st.columns(4)
        with lc1: ln = st.text_input(f"Role {i+1}", value=ev.get("name", f"Category {i+1}"), key=f"{prefix}_ln_{i}")
        with lc2: lc = st.number_input(f"Workers {i+1}", min_value=1, value=ev.get("count", 5), key=f"{prefix}_lc_{i}")
        with lc3: lr = st.number_input(f"Rate ($/day) {i+1}", min_value=0.0, value=float(ev.get("daily_rate", 120.0)), key=f"{prefix}_lr_{i}")
        with lc4: ld = st.number_input(f"Days active {i+1}", min_value=1, value=ev.get("days", int(deadline_days)), key=f"{prefix}_ld_{i}")
        labourers.append({"name": ln, "count": int(lc), "daily_rate": float(lr), "days": int(ld)})

    # ── Materials ─────────────────────────────────────────────────────────
    st.markdown('<p class="section-header">📦 Materials</p>', unsafe_allow_html=True)
    c2, _, __ = st.columns([1, 1, 2])
    with c2:
        st.number_input("Material types", min_value=0, max_value=15,
                        key=f"wgt_{prefix}_nm")
        st.session_state[nm_key] = int(st.session_state[f"wgt_{prefix}_nm"])
    materials = []
    for i in range(st.session_state[nm_key]):
        ev = existing_m[i] if i < len(existing_m) else {}
        mc1, mc2, mc3 = st.columns(3)
        with mc1: mn = st.text_input(f"Material {i+1}", value=ev.get("name", f"Material {i+1}"), key=f"{prefix}_mn_{i}")
        with mc2: mq = st.number_input(f"Quantity {i+1}", min_value=0.0, value=float(ev.get("quantity", 100.0)), key=f"{prefix}_mq_{i}")
        with mc3: mu = st.number_input(f"Unit cost ($) {i+1}", min_value=0.0, value=float(ev.get("unit_cost", 10.0)), key=f"{prefix}_mu_{i}")
        materials.append({"name": mn, "quantity": float(mq), "unit_cost": float(mu)})

    # ── Machinery ─────────────────────────────────────────────────────────
    st.markdown('<p class="section-header">⚙️ Machinery</p>', unsafe_allow_html=True)
    c3, _, __ = st.columns([1, 1, 2])
    with c3:
        st.number_input("Machine types", min_value=0, max_value=10,
                        key=f"wgt_{prefix}_nx")
        st.session_state[nx_key] = int(st.session_state[f"wgt_{prefix}_nx"])
    machines = []
    for i in range(st.session_state[nx_key]):
        ev = existing_x[i] if i < len(existing_x) else {}
        xc1, xc2, xc3, xc4 = st.columns(4)
        with xc1: xn = st.text_input(f"Machine {i+1}", value=ev.get("name", f"Machine {i+1}"), key=f"{prefix}_xn_{i}")
        with xc2: xc = st.number_input(f"Units {i+1}", min_value=1, value=ev.get("count", 1), key=f"{prefix}_xc_{i}")
        with xc3: xr = st.number_input(f"Rate ($/day) {i+1}", min_value=0.0, value=float(ev.get("daily_rate", 500.0)), key=f"{prefix}_xr_{i}")
        with xc4: xd = st.number_input(f"Days active {i+1}", min_value=1, value=ev.get("days", int(deadline_days)), key=f"{prefix}_xd_{i}")
        machines.append({"name": xn, "count": int(xc), "daily_rate": float(xr), "days": int(xd)})

    return labourers, materials, machines

# ─────────────────────────────────────────────────────────────────────────────
#  CUSTOM CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    [data-testid="stAppViewContainer"] { background-color: #0e1117; }
    .score-badge {
        display: inline-block; padding: 4px 14px; border-radius: 20px;
        font-weight: 700; font-size: 0.85rem;
    }
    .section-header {
        color: #63b3ed; font-size: 0.9rem; font-weight: 700;
        text-transform: uppercase; letter-spacing: 2px;
        border-bottom: 1px solid #2d3561;
        padding-bottom: 6px; margin: 20px 0 12px 0;
    }
    .stTabs [data-baseweb="tab"] { color: #a0aec0; font-size: 0.9rem; }
    .stTabs [aria-selected="true"] { color: #63b3ed !important; }
    div[data-testid="stMetricValue"] { color: #ffffff; }
    .timeline-bar-bg {
        background: #1a1f2e; border-radius: 8px; height: 18px;
        width: 100%; overflow: hidden; margin-top: 4px;
    }
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
        ["📊 Dashboard", "➕ Add Milestone", "🔍 Milestone Detail",
         "✏️ Edit Milestone", "🗑️ Manage Milestones", "📤 Export Report"],
        label_visibility="collapsed"
    )
    st.markdown("---")
    data = get_data()
    st.markdown(f"**Milestones:** {len(data['milestones'])}")
    st.markdown("---")
    st.markdown("""
    <div style='font-size:0.75rem; color:#718096;'>
    <b>Scoring Formula</b><br>
    PoD × 0.40 + CoD_norm × 0.35 + CFTS × 0.25<br><br>
    <b>Spend = auto-calculated</b><br>
    from milestone resources × elapsed days
    </div>
    """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
#  PAGE: DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
if page == "📊 Dashboard":
    st.markdown("# 📊 Project Intelligence Dashboard")
    data = get_data()
    milestones = data["milestones"]

    if not milestones:
        st.info("No milestones yet. Use **➕ Add Milestone** to get started.")
        st.stop()

    scored = []
    for ms in milestones:
        m = score_milestone(ms)
        scored.append({**ms, **m})
    scored.sort(key=lambda x: x["score"], reverse=True)

    total_budget = sum(s["total_cost"] for s in scored)
    total_spent  = sum(s["total_spent"] for s in scored)
    total_remain = sum(s["remaining_budget"] for s in scored)
    critical     = sum(1 for s in scored if s["score"] >= 70)
    high         = sum(1 for s in scored if 45 <= s["score"] < 70)
    avg_score    = np.mean([s["score"] for s in scored])

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Budget",        f"${total_budget:,.0f}")
    c2.metric("Total Spent (to date)",f"${total_spent:,.0f}", delta=f"-${total_remain:,.0f} remaining")
    c3.metric("Critical Milestones", critical, delta="Needs action" if critical else "All clear")
    c4.metric("High Risk",           high)
    c5.metric("Avg Risk Score",      f"{avg_score:.1f}/100")

    st.markdown("---")

    col_gauge, col_bar = st.columns([1, 2])
    with col_gauge:
        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number", value=avg_score,
            title={"text": "Portfolio Risk", "font": {"color": "#a0aec0"}},
            gauge={
                "axis": {"range": [0, 100], "tickcolor": "#a0aec0"},
                "bar": {"color": risk_color(avg_score)},
                "bgcolor": "#1a1f2e",
                "steps": [
                    {"range": [0, 25],   "color": "#0d2b1a"},
                    {"range": [25, 45],  "color": "#0d1f2b"},
                    {"range": [45, 70],  "color": "#2b2200"},
                    {"range": [70, 100], "color": "#2b0d0d"},
                ],
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
            "Milestone": s["title"][:25], "Score": s["score"],
        } for s in scored])
        fig_bar = px.bar(
            df_bar, x="Score", y="Milestone", orientation="h",
            color="Score", color_continuous_scale=["#00CC66","#00C0F0","#FFA500","#FF4B4B"],
            range_color=[0, 100], text="Score"
        )
        fig_bar.update_layout(
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font={"color": "#a0aec0"}, height=250,
            margin=dict(l=10, r=10, t=10, b=10),
            coloraxis_showscale=False, yaxis={"autorange": "reversed"}
        )
        fig_bar.update_traces(textposition="outside")
        st.plotly_chart(fig_bar, use_container_width=True)

    st.markdown('<p class="section-header">Ranked Milestone Risk Matrix</p>', unsafe_allow_html=True)
    for s in scored:
        sc  = s["score"]
        col = risk_color(sc)
        elapsed   = s["days_elapsed"]
        remaining = s["days_remaining"]
        deadline  = s["deadline_days"]
        pct_done  = elapsed / max(deadline, 1)

        with st.container():
            r1,r2,r3,r4,r5,r6,r7,r8 = st.columns([3,1,1.2,1.2,1,1,1,1.8])
            with r1: st.markdown(f"**{s['title'][:28]}**")
            with r2: st.markdown(f"⏱ {remaining}d left")
            with r3: st.markdown(f"💰 ${s['total_cost']:,.0f}")
            with r4: st.markdown(f"📉 ${s['total_spent']:,.0f} spent")
            with r5: st.markdown(f"PoD: **{round(s['PoD']*100)}%**")
            with r6: st.markdown(f"CoD: ${s['CoD_per_day']:,.0f}/d")
            with r7: st.markdown(f"CFTS: {s['CFTS']}")
            with r8:
                tag = ' <span style="font-size:0.7rem;color:#718096;">(not started)</span>' \
                      if s.get("not_started") else ""
                st.markdown(
                    f'<span class="score-badge" style="background:{col}22;color:{col};border:1px solid {col};">'
                    f'{sc}/100 {risk_label(sc)}</span>{tag}',
                    unsafe_allow_html=True
                )

        # Timeline progress bar
        bar_html = (
            f'<div class="timeline-bar-bg">'
            f'<div style="background:{col};width:{pct_done*100:.1f}%;height:100%;border-radius:8px;"></div>'
            f'</div>'
        )
        st.markdown(bar_html, unsafe_allow_html=True)
        st.caption(f"Day {elapsed} of {deadline} — started {s['start_date']}")
        st.markdown("")

    # ── Risk Explainer ────────────────────────────────────────────────────
    st.markdown('<p class="section-header">Risk Explainer</p>', unsafe_allow_html=True)
    explainer_options = {s["title"]: s for s in scored}
    selected_explain = st.selectbox(
        "Select a milestone to explain its risk score:",
        list(explainer_options.keys()),
        key="dashboard_explainer_select"
    )
    ex = explainer_options[selected_explain]
    ex_ms = next(ms for ms in milestones if ms["id"] == ex["id"])
    ex_col = risk_color(ex["score"])

    # Score badge header
    st.markdown(
        f'<div style="background:#1a1f2e; border:1px solid #2d3561; border-left:5px solid {ex_col}; '
        f'border-radius:12px; padding:20px 24px; margin: 12px 0;">'
        f'<div style="display:flex; align-items:center; gap:14px; margin-bottom:16px;">'
        f'<span style="font-size:1.3rem; font-weight:800; color:#fff;">{ex["title"]}</span>'
        f'<span class="score-badge" style="background:{ex_col}22; color:{ex_col}; border:1px solid {ex_col}; font-size:1rem;">'
        f'{ex["score"]}/100 &nbsp; {risk_label(ex["score"])}</span>'
        f'</div>',
        unsafe_allow_html=True
    )

    # Build natural language explanation
    reasons = []
    positives = []

    # ── Not started ───────────────────────────────────────────────────────
    if ex.get("not_started"):
        reasons.append(("📅", "Not yet started", f"This milestone starts on <b>{ex['start_date']}</b>. No spend has occurred yet, so risk is driven purely by deadline proximity."))

    # ── Timeline pressure ─────────────────────────────────────────────────
    elapsed   = ex["days_elapsed"]
    remaining = ex["days_remaining"]
    deadline  = ex["deadline_days"]
    pct_done  = elapsed / max(deadline, 1)

    if remaining <= 3:
        reasons.append(("🚨", "Deadline in 3 days or less", f"Only <b>{remaining} day(s)</b> remain. Any slippage now directly delays payment or handover with zero buffer."))
    elif remaining <= 7:
        reasons.append(("⚠️", "Deadline very close", f"<b>{remaining} days</b> remaining — cash flow timing sensitivity is at maximum. A single day of disruption is high-impact."))
    elif remaining <= 14:
        reasons.append(("⏱️", "Tight deadline window", f"With <b>{remaining} days</b> left and {round(pct_done*100)}% of the timeline elapsed, urgency is elevated."))
    else:
        positives.append(("✅", "Comfortable runway", f"<b>{remaining} days</b> remaining gives adequate buffer to absorb minor disruptions."))

    # ── Budget burn ───────────────────────────────────────────────────────
    be = ex["burn_efficiency"]
    if not ex.get("not_started"):
        pct_spent = round(ex["pct_spent"] * 100)
        pct_time  = round(ex["pct_time"]  * 100)

        if be > 1.40:
            reasons.append(("💸", "Severely overspending", f"Spending <b>{round((be-1)*100)}% faster</b> than the schedule allows. At current pace, projected total is <b>${ex['projected_total']:,.0f}</b> vs budget of <b>${ex['total_cost']:,.0f}</b>."))
        elif be > 1.20:
            reasons.append(("📈", "Spending above plan", f"Burn rate is <b>{round((be-1)*100)}% over</b> plan. {pct_spent}% of budget consumed at {pct_time}% of timeline."))
        elif be < 0.60 and pct_time > 20:
            reasons.append(("🐢", "Underspending vs timeline", f"Only <b>{pct_spent}%</b> of budget used at <b>{pct_time}%</b> of timeline. Risk of a cost surge in the final stretch."))
        else:
            positives.append(("✅", "Spend on track", f"<b>{pct_spent}%</b> of budget consumed at <b>{pct_time}%</b> of timeline — burn rate is healthy."))

    # ── PoD breakdown ─────────────────────────────────────────────────────
    pod_pct = round(ex["PoD"] * 100)
    if pod_pct >= 60:
        reasons.append(("🔴", "Very high delay probability", f"Probability of Delay is <b>{pod_pct}%</b>, driven by the combination of budget pressure and timeline consumed."))
    elif pod_pct >= 35:
        reasons.append(("🟡", "Elevated delay probability", f"Probability of Delay is <b>{pod_pct}%</b>. Monitor closely — the milestone is approaching its risk threshold."))
    else:
        positives.append(("✅", "Low delay probability", f"Probability of Delay is only <b>{pod_pct}%</b>."))

    # ── Cash runway ───────────────────────────────────────────────────────
    cash_days = ex["days_of_cash_left"]
    if cash_days < 7:
        reasons.append(("🏦", "Critical cash runway", f"Only <b>{cash_days:.1f} days</b> of cash left at current burn rate. Overdraft facility or accelerated billing may be needed immediately."))
    elif cash_days < 14:
        reasons.append(("⚠️", "Short cash runway", f"<b>{cash_days:.1f} days</b> of cash runway. Plan for financing before funds run out."))
    else:
        positives.append(("✅", "Adequate cash runway", f"<b>{cash_days:.1f} days</b> of cash runway remaining at current burn rate."))

    # ── Labour concentration ───────────────────────────────────────────────
    labourers = ex_ms.get("labourers", [])
    if labourers:
        total_labour = sum(l["count"] * l["daily_rate"] * l["days"] for l in labourers)
        labour_pct   = round(total_labour / max(ex["total_cost"], 1) * 100)
        if labour_pct > 65:
            reasons.append(("👷", "Labour-heavy budget", f"Labour accounts for <b>{labour_pct}%</b> of the total contract value, making the budget highly sensitive to attendance, productivity, and headcount changes."))

    # ── Projected overrun ──────────────────────────────────────────────────
    if ex["projected_total"] > ex["total_cost"] * 1.10 and not ex.get("not_started"):
        overrun_pct = round((ex["projected_total"] / ex["total_cost"] - 1) * 100)
        reasons.append(("📊", "Budget overrun projected", f"Current trajectory puts final cost at <b>${ex['projected_total']:,.0f}</b> — <b>{overrun_pct}% over</b> the <b>${ex['total_cost']:,.0f}</b> contract value."))
    elif not ex.get("not_started"):
        positives.append(("✅", "No overrun projected", f"Projected final cost of <b>${ex['projected_total']:,.0f}</b> is within the <b>${ex['total_cost']:,.0f}</b> contract value."))

    # ── Render reasons ─────────────────────────────────────────────────────
    if reasons:
        st.markdown(
            '<div style="font-size:0.8rem; font-weight:700; color:#a0aec0; '
            'text-transform:uppercase; letter-spacing:1px; margin-bottom:8px;">'
            '⚠ Risk Drivers</div>',
            unsafe_allow_html=True
        )
        for icon, title, detail in reasons:
            st.markdown(
                f'<div style="background:#2b1a1a; border:1px solid #4a2020; border-radius:8px; '
                f'padding:12px 16px; margin-bottom:8px;">'
                f'<span style="font-size:1.1rem;">{icon}</span> '
                f'<span style="color:#ff6b6b; font-weight:700;">{title}</span><br>'
                f'<span style="color:#cbd5e0; font-size:0.88rem;">{detail}</span>'
                f'</div>',
                unsafe_allow_html=True
            )

    if positives:
        st.markdown(
            '<div style="font-size:0.8rem; font-weight:700; color:#a0aec0; '
            'text-transform:uppercase; letter-spacing:1px; margin: 12px 0 8px 0;">'
            '✓ Positive Factors</div>',
            unsafe_allow_html=True
        )
        for icon, title, detail in positives:
            st.markdown(
                f'<div style="background:#0d2b1a; border:1px solid #1a4a2a; border-radius:8px; '
                f'padding:12px 16px; margin-bottom:8px;">'
                f'<span style="font-size:1.1rem;">{icon}</span> '
                f'<span style="color:#68d391; font-weight:700;">{title}</span><br>'
                f'<span style="color:#cbd5e0; font-size:0.88rem;">{detail}</span>'
                f'</div>',
                unsafe_allow_html=True
            )

    # Summary sentence
    score = ex["score"]
    if score >= 70:
        summary = f"<b>{ex['title']}</b> is <span style='color:#FF4B4B; font-weight:700;'>CRITICAL</span> because multiple high-severity risk drivers are active simultaneously. Immediate intervention is required."
    elif score >= 45:
        summary = f"<b>{ex['title']}</b> is <span style='color:#FFA500; font-weight:700;'>HIGH RISK</span> — significant pressure exists on budget or timeline that warrants close monitoring and proactive mitigation."
    elif score >= 25:
        summary = f"<b>{ex['title']}</b> is <span style='color:#00C0F0; font-weight:700;'>MEDIUM RISK</span> — some risk factors are present but the milestone is broadly manageable. Keep a watchful eye."
    else:
        summary = f"<b>{ex['title']}</b> is <span style='color:#00CC66; font-weight:700;'>LOW RISK</span> — no significant risk drivers detected. Maintain current execution pace."

    st.markdown(
        f'<div style="margin-top:16px; padding:14px 18px; background:#16213e; '
        f'border-radius:8px; border:1px solid #2d3561; color:#e2e8f0; font-size:0.92rem;">'
        f'💬 {summary}</div>',
        unsafe_allow_html=True
    )
    st.markdown('</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
#  PAGE: ADD MILESTONE
# ─────────────────────────────────────────────────────────────────────────────
elif page == "➕ Add Milestone":
    st.markdown("# ➕ Add New Milestone")

    st.markdown('<p class="section-header">Basic Info</p>', unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)
    with col1: title         = st.text_input("Milestone Title", placeholder="e.g. Foundation Works", key="add_title")
    with col2: start_date    = st.date_input("Start Date", value=date.today(), key="add_start")
    with col3: deadline_days = st.number_input("Duration (days)", min_value=1, value=30, key="add_deadline")
    with col4: total_cost    = st.number_input("Total Contract Value ($)", min_value=0.0, value=50000.0, step=1000.0, key="add_cost")
    phases = st.number_input("Number of Phases", min_value=1, value=1, key="add_phases")

    # Initialise resource counters for Add form
    for k, v in [("wgt_add_nl", 1), ("wgt_add_nm", 1), ("wgt_add_nx", 1)]:
        if k not in st.session_state:
            st.session_state[k] = v

    labourers, materials, machines = render_resource_form("add", int(deadline_days))

    # Live cost preview
    planned_labour   = sum(l["count"] * l["daily_rate"] * l["days"] for l in labourers)
    planned_material = sum(m["quantity"] * m["unit_cost"] for m in materials)
    planned_machine  = sum(m["count"] * m["daily_rate"] * m["days"] for m in machines)
    planned_total    = planned_labour + planned_material + planned_machine
    variance         = total_cost - planned_total

    st.markdown('<p class="section-header">Cost Preview</p>', unsafe_allow_html=True)
    pv1, pv2, pv3, pv4, pv5 = st.columns(5)
    pv1.metric("Labour",      f"${planned_labour:,.0f}")
    pv2.metric("Materials",   f"${planned_material:,.0f}")
    pv3.metric("Machinery",   f"${planned_machine:,.0f}")
    pv4.metric("Planned Total", f"${planned_total:,.0f}")
    pv5.metric("Contract vs Plan", f"${variance:+,.0f}",
               delta="✅ Margin" if variance >= 0 else "⚠️ Over")

    st.markdown("")
    if st.button("✅ Save Milestone", use_container_width=True, type="primary", key="add_save"):
        if not title:
            st.error("Please enter a milestone title.")
        else:
            ms_id = f"MS{len(get_data()['milestones'])+1:03d}_{int(datetime.now().timestamp())}"
            milestone = {
                "id": ms_id, "title": title,
                "start_date": str(start_date),
                "deadline_days": int(deadline_days), "phases": int(phases),
                "total_cost": float(total_cost),
                "labourers": labourers, "materials": materials, "machines": machines,
                "created_at": str(date.today()),
            }
            d = get_data()
            d["milestones"].append(milestone)
            persist(d)

            # Clear form session keys
            for k in list(st.session_state.keys()):
                if k.startswith("add_") or k.startswith("wgt_add") or k.startswith("_add_"):
                    st.session_state.pop(k, None)

            st.success(f"✅ **{title}** saved! Spend will be auto-calculated from {start_date}.")
            st.rerun()

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
    m  = score_milestone(ms)
    sc = m["score"]

    start      = date.fromisoformat(ms["start_date"])
    end_date   = start + timedelta(days=ms["deadline_days"])
    elapsed    = m["days_elapsed"]
    remaining  = m["days_remaining"]

    # Header row
    st.markdown(f"### {ms['title']}")
    hc1, hc2, hc3, hc4, hc5, hc6 = st.columns(6)
    hc1.metric("Risk Score",    f"{sc}/100",          delta=risk_label(sc))
    hc2.metric("Days Elapsed",  f"{elapsed}d")
    hc3.metric("Days Remaining",f"{remaining}d")
    hc4.metric("PoD",           f"{round(m['PoD']*100)}%")
    hc5.metric("Cash Runway",   f"{m['days_of_cash_left']:.1f}d")
    hc6.metric("Burn Efficiency",f"{m['burn_efficiency']:.2f}x",
               delta="Overspending" if m["burn_efficiency"] > 1.2 else "Normal")

    # Timeline bar
    pct_done = elapsed / max(ms["deadline_days"], 1)
    col = risk_color(sc)
    st.markdown(
        f'<div class="timeline-bar-bg" style="height:22px;">'
        f'<div style="background:{col};width:{pct_done*100:.1f}%;height:100%;border-radius:8px;'
        f'display:flex;align-items:center;padding-left:8px;color:#fff;font-size:0.75rem;font-weight:700;">'
        f'Day {elapsed}/{ms["deadline_days"]}</div></div>',
        unsafe_allow_html=True
    )
    st.caption(f"📅 {start.strftime('%d %b %Y')} → {end_date.strftime('%d %b %Y')}")
    st.markdown("")

    tab1, tab2, tab3, tab4 = st.tabs(["📊 Score Breakdown", "💰 Budget & Resources", "📅 Spend Schedule", "💡 Suggestions"])

    # ── Tab 1: Score Breakdown ────────────────────────────────────────────
    with tab1:
        col_g, col_d = st.columns([1, 1])
        with col_g:
            values = [round(m["PoD"]*0.40*100,1), round(m["CoD_norm"]*0.35*100,1), round(m["CFTS"]*0.25*100,1)]
            # Avoid all-zero pie (not started)
            if sum(values) == 0:
                values = [0.01, 0.01, 0.01]
            fig_pie = go.Figure(go.Pie(
                labels=["PoD (×0.40)", "CoD_norm (×0.35)", "CFTS (×0.25)"],
                values=values, hole=0.55,
                marker=dict(colors=["#FF4B4B", "#FFA500", "#00C0F0"]),
                textinfo="label+percent"
            ))
            fig_pie.update_layout(
                paper_bgcolor="#0e1117", font={"color": "#a0aec0"},
                height=280, showlegend=False, margin=dict(l=0, r=0, t=20, b=0),
                annotations=[{"text": f"<b>{sc}</b>", "font": {"size": 26, "color": "#fff"}, "showarrow": False}]
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_d:
            st.markdown("")
            for layer, val, norm, weight, desc in [
                ("Layer 1 — Probability of Delay", f"{round(m['PoD']*100)}%", m["PoD"], 0.40, "Budget burn rate, time consumed, urgency pressure"),
                ("Layer 2 — Cost of Delay",        f"${m['CoD_per_day']:,.0f}/day", m["CoD_norm"], 0.35, "Daily financial bleeding if milestone slips"),
                ("Layer 3 — CF Timing Sensitivity",f"{m['CFTS']:.2f}", m["CFTS"], 0.25, "Proximity of cash-flow trigger / payment milestone"),
            ]:
                contrib = round(norm * weight * 100)
                st.markdown(f"**{layer}** — `{val}` → **+{contrib}pts**")
                st.progress(float(norm))
                st.caption(desc)
                st.markdown("")

        if m.get("not_started"):
            st.info("ℹ️ Milestone has not started yet — only deadline urgency (CFTS) contributes to the score.")

    # ── Tab 2: Budget & Resources ─────────────────────────────────────────
    with tab2:
        bc1, bc2 = st.columns(2)
        with bc1:
            st.metric("Total Budget",     f"${ms['total_cost']:,.2f}")
            st.metric("Spent to Date",    f"${m['total_spent']:,.2f}")
            st.metric("Remaining",        f"${m['remaining_budget']:,.2f}")
            st.metric("Projected Total",  f"${m['projected_total']:,.2f}",
                      delta="⚠ Overrun" if m["projected_total"] > ms["total_cost"] else "✅ OK")
            st.metric("Avg Daily Spend",  f"${m['avg_daily']:,.2f}/day")

        with bc2:
            fig_donut = go.Figure(go.Pie(
                labels=["Wages", "Materials", "Machinery", "Remaining"],
                values=[m["wages_spent"], m["mat_spent"], m["mach_spent"], m["remaining_budget"]],
                hole=0.5, marker=dict(colors=["#FF4B4B","#FFA500","#00C0F0","#00CC66"])
            ))
            fig_donut.update_layout(
                paper_bgcolor="#0e1117", font={"color": "#a0aec0"},
                height=300, margin=dict(l=0, r=0, t=20, b=0)
            )
            st.plotly_chart(fig_donut, use_container_width=True)

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

    # ── Tab 3: Spend Schedule ─────────────────────────────────────────────
    with tab3:
        schedule = compute_planned_schedule(ms)
        actuals  = get_actuals_up_to_today(ms)
        budget_per_day = ms["total_cost"] / max(ms["deadline_days"], 1)

        schedule["date_str"] = schedule["date"].astype(str)
        schedule["period"]   = schedule["date"].apply(
            lambda d: "Past / Today" if d <= date.today() else "Upcoming"
        )

        fig_line = go.Figure()
        fig_line.add_trace(go.Bar(
            x=schedule["date_str"], y=schedule["total"],
            name="Planned Daily Spend",
            marker_color=schedule["period"].map({"Past / Today": "#63b3ed", "Upcoming": "#2d3561"}),
            opacity=0.8
        ))
        if not actuals.empty:
            actuals["rolling7"] = actuals["total"].rolling(7, min_periods=1).mean()
            fig_line.add_trace(go.Scatter(
                x=actuals["date"].astype(str), y=actuals["rolling7"],
                name="7-Day Rolling Avg", line=dict(color="#FFA500", width=2.5)
            ))
        fig_line.add_hline(y=budget_per_day, line_dash="dash",
                           line_color="#FF4B4B", annotation_text="Budget/Day")
        fig_line.update_layout(
            paper_bgcolor="#0e1117", plot_bgcolor="#1a1f2e",
            font={"color": "#a0aec0"}, height=320,
            legend=dict(bgcolor="#0e1117"),
            margin=dict(l=10, r=10, t=20, b=10)
        )
        st.plotly_chart(fig_line, use_container_width=True)

        col_view = st.radio("Show", ["Past (Actuals)", "Full Schedule"], horizontal=True, key="sched_view")
        if col_view == "Past (Actuals)":
            df_show = actuals.copy()
        else:
            df_show = schedule.copy()

        if df_show.empty:
            st.info("Milestone has not started yet — no actuals to display.")
        else:
            df_show["date"] = df_show["date"].astype(str)
            df_show = df_show.rename(columns={
                "wages": "Wages ($)", "materials": "Materials ($)",
                "machinery": "Machinery ($)", "total": "Total ($)"
            })
            cols = ["date", "Wages ($)", "Materials ($)", "Machinery ($)", "Total ($)"]
            st.dataframe(df_show[cols].reset_index(drop=True), use_container_width=True)

    # ── Tab 4: Suggestions ────────────────────────────────────────────────
    with tab4:
        for tag, kind, text in generate_suggestions(ms, m):
            if kind == "error":   st.error(f"**{tag}** — {text}")
            elif kind == "warning": st.warning(f"**{tag}** — {text}")
            elif kind == "info":    st.info(f"**{tag}** — {text}")
            else:                   st.success(f"**{tag}** — {text}")

    st.markdown("---")
    with st.expander("⚠️ Danger Zone"):
        st.warning("Deleting this milestone cannot be undone.")
        confirm_name = st.text_input("Type the milestone title to confirm:", key="confirm_delete_detail")
        if st.button("🗑️ Delete This Milestone", type="secondary", key="delete_btn_detail"):
            if confirm_name.strip() == ms["title"]:
                d = get_data()
                d["milestones"] = [m2 for m2 in d["milestones"] if m2["id"] != ms["id"]]
                persist(d)
                st.success("Milestone deleted.")
                st.rerun()
            else:
                st.error("Title does not match.")

# ─────────────────────────────────────────────────────────────────────────────
#  PAGE: EDIT MILESTONE
# ─────────────────────────────────────────────────────────────────────────────
elif page == "✏️ Edit Milestone":
    st.markdown("# ✏️ Edit Milestone")
    data = get_data()
    milestones = data["milestones"]

    if not milestones:
        st.info("No milestones to edit.")
        st.stop()

    options = {ms["title"]: ms for ms in milestones}
    selected_name = st.selectbox("Select Milestone to Edit", list(options.keys()), key="edit_select")

    # When selection changes, clear edit form state so it re-populates with new defaults
    if st.session_state.get("_edit_last_selected") != selected_name:
        for k in list(st.session_state.keys()):
            if k.startswith("edit_") or k.startswith("wgt_edit") or k.startswith("_edit_n"):
                st.session_state.pop(k, None)
        st.session_state["_edit_last_selected"] = selected_name

    ms = options[selected_name]
    m  = score_milestone(ms)

    st.markdown("---")
    st.info(
        f"**Current status:** Day {m['days_elapsed']} of {ms['deadline_days']} | "
        f"${m['total_spent']:,.0f} spent to date | Risk: {m['score']}/100 {risk_label(m['score'])}\n\n"
        f"Changes apply **from today forward** — past actuals are already computed from the original parameters."
    )

    # ── Editable Fields ───────────────────────────────────────────────────
    st.markdown('<p class="section-header">Basic Info</p>', unsafe_allow_html=True)

    e1, e2, e3, e4 = st.columns(4)
    with e1:
        new_title = st.text_input("Milestone Title", value=ms["title"], key="edit_title")
    with e2:
        new_start = st.date_input("Start Date", value=date.fromisoformat(ms["start_date"]), key="edit_start")
    with e3:
        # Remaining duration only — can extend deadline, not shorten past elapsed days
        min_dur = max(m["days_elapsed"] + 1, 1)
        new_deadline = st.number_input(
            "Total Duration (days)", min_value=min_dur,
            value=max(ms["deadline_days"], min_dur), key="edit_deadline",
            help=f"Minimum {min_dur} days (cannot shorten past already-elapsed days)"
        )
    with e4:
        new_cost = st.number_input("Total Contract Value ($)", min_value=0.0,
                                   value=float(ms["total_cost"]), step=1000.0, key="edit_cost")
    new_phases = st.number_input("Number of Phases", min_value=1, value=ms.get("phases", 1), key="edit_phases")

    # Initialise resource counters for Edit form with current values
    for k, v in [
        ("wgt_edit_nl", max(len(ms.get("labourers", [])), 1)),
        ("wgt_edit_nm", max(len(ms.get("materials", [])), 1)),
        ("wgt_edit_nx", max(len(ms.get("machines",  [])), 1)),
    ]:
        if k not in st.session_state:
            st.session_state[k] = v

    new_labourers, new_materials, new_machines = render_resource_form("edit", int(new_deadline), defaults=ms)

    # Live cost preview
    planned_labour   = sum(l["count"] * l["daily_rate"] * l["days"] for l in new_labourers)
    planned_material = sum(m2["quantity"] * m2["unit_cost"] for m2 in new_materials)
    planned_machine  = sum(m2["count"] * m2["daily_rate"] * m2["days"] for m2 in new_machines)
    planned_total    = planned_labour + planned_material + planned_machine
    variance         = new_cost - planned_total

    st.markdown('<p class="section-header">Updated Cost Preview</p>', unsafe_allow_html=True)
    pv1, pv2, pv3, pv4, pv5 = st.columns(5)
    pv1.metric("Labour",        f"${planned_labour:,.0f}")
    pv2.metric("Materials",     f"${planned_material:,.0f}")
    pv3.metric("Machinery",     f"${planned_machine:,.0f}")
    pv4.metric("Planned Total", f"${planned_total:,.0f}")
    pv5.metric("Contract vs Plan", f"${variance:+,.0f}",
               delta="✅ Margin" if variance >= 0 else "⚠️ Over")

    st.markdown("")
    if st.button("💾 Save Changes", use_container_width=True, type="primary", key="edit_save"):
        if not new_title:
            st.error("Title cannot be empty.")
        else:
            updated = {
                **ms,
                "title":         new_title,
                "start_date":    str(new_start),
                "deadline_days": int(new_deadline),
                "phases":        int(new_phases),
                "total_cost":    float(new_cost),
                "labourers":     new_labourers,
                "materials":     new_materials,
                "machines":      new_machines,
                "edited_at":     str(date.today()),
            }
            d = get_data()
            d["milestones"] = [updated if m2["id"] == ms["id"] else m2 for m2 in d["milestones"]]
            persist(d)

            # Clear edit form state
            for k in list(st.session_state.keys()):
                if k.startswith("edit_") or k.startswith("wgt_edit") or k.startswith("_edit_n"):
                    st.session_state.pop(k, None)

            st.success(f"✅ **{new_title}** updated successfully!")
            st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
#  PAGE: MANAGE MILESTONES
# ─────────────────────────────────────────────────────────────────────────────
elif page == "🗑️ Manage Milestones":
    st.markdown("# 🗑️ Manage Milestones")
    data = get_data()
    milestones = data["milestones"]

    if not milestones:
        st.info("No milestones to manage.")
        st.stop()

    st.markdown("Type a milestone's title in the confirm box to unlock its delete button.")
    st.markdown("---")

    for ms in milestones:
        m  = score_milestone(ms)
        sc = m["score"]
        col = risk_color(sc)
        safe_key = ms["id"].replace("-","_").replace(".","_")

        label_html = (
            f"<div style='background:#1a1f2e;border:1px solid #2d3561;"
            f"border-left:4px solid {col};border-radius:10px;"
            f"padding:12px 16px;margin-bottom:4px;'>"
            f"<span style='font-size:1.05rem;font-weight:700;color:#fff;'>{ms['title']}</span>"
            f" &nbsp; <span style='font-size:0.8rem;color:#a0aec0;'>"
            f"Start: {ms['start_date']} &nbsp;|&nbsp; "
            f"Duration: {ms['deadline_days']}d &nbsp;|&nbsp; "
            f"Budget: ${ms['total_cost']:,.0f} &nbsp;|&nbsp; "
            f"Spent: ${m['total_spent']:,.0f} &nbsp;|&nbsp; "
            f"Risk: <span style='color:{col};font-weight:700;'>{sc}/100 {risk_label(sc)}</span>"
            f"</span></div>"
        )
        st.markdown(label_html, unsafe_allow_html=True)

        ca, cb = st.columns([3, 1])
        with ca:
            confirm_input = st.text_input(
                "confirm", key=f"confirm_{safe_key}",
                placeholder=f'Type "{ms["title"]}" to unlock delete',
                label_visibility="collapsed"
            )
        with cb:
            delete_ok = confirm_input.strip() == ms["title"]
            if st.button(
                "🗑️ Delete" if delete_ok else "🔒 Locked",
                key=f"del_{safe_key}", type="secondary",
                disabled=not delete_ok, use_container_width=True
            ):
                d = get_data()
                d["milestones"] = [m2 for m2 in d["milestones"] if m2["id"] != ms["id"]]
                persist(d)
                st.success(f"✅ Deleted: {ms['title']}")
                st.rerun()
        st.markdown("")

    st.markdown("---")
    with st.expander("☢️ Delete ALL Milestones"):
        st.error("This permanently deletes every milestone. Cannot be undone.")
        nuke_confirm = st.text_input("Type DELETE ALL to confirm:", key="nuke_confirm")
        if st.button("☢️ Wipe Everything", type="secondary"):
            if nuke_confirm.strip() == "DELETE ALL":
                d = get_data()
                d["milestones"] = []
                persist(d)
                st.success("All milestones deleted.")
                st.rerun()
            else:
                st.error("Type exactly: DELETE ALL")

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
        m    = score_milestone(ms)
        sugg = generate_suggestions(ms, m)
        records.append({
            "Milestone ID":       ms["id"],
            "Title":              ms["title"],
            "Start Date":         ms["start_date"],
            "Deadline (days)":    ms["deadline_days"],
            "Days Elapsed":       m["days_elapsed"],
            "Days Remaining":     m["days_remaining"],
            "Total Budget ($)":   ms["total_cost"],
            "Total Spent ($)":    m["total_spent"],
            "Remaining Budget ($)":m["remaining_budget"],
            "Projected Total ($)":m["projected_total"],
            "Avg Daily Spend ($)":m["avg_daily"],
            "PoD (0-1)":          m["PoD"],
            "CoD per Day ($)":    m["CoD_per_day"],
            "CFTS (0-1)":         m["CFTS"],
            "Risk Score (0-100)": m["score"],
            "Risk Level":         risk_label(m["score"]),
            "Burn Efficiency":    m["burn_efficiency"],
            "Days of Cash Left":  m["days_of_cash_left"],
            "Top Suggestion":     sugg[0][2] if sugg else "",
        })

    df_export = pd.DataFrame(records).sort_values("Risk Score (0-100)", ascending=False)
    st.dataframe(df_export, use_container_width=True)

    csv = df_export.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download Risk Report CSV", data=csv,
                       file_name=f"epc_risk_report_{date.today()}.csv",
                       mime="text/csv", use_container_width=True)

    # Export full schedule for every milestone
    all_schedules = []
    for ms in milestones:
        sched = compute_planned_schedule(ms)
        sched.insert(0, "milestone", ms["title"])
        sched.insert(1, "milestone_id", ms["id"])
        all_schedules.append(sched)

    if all_schedules:
        full_sched = pd.concat(all_schedules, ignore_index=True)
        full_sched["date"] = full_sched["date"].astype(str)
        csv2 = full_sched[["milestone","milestone_id","date","wages","materials","machinery","total"]].to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Download Full Spend Schedule CSV", data=csv2,
                           file_name=f"epc_spend_schedule_{date.today()}.csv",
                           mime="text/csv", use_container_width=True)
