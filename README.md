# 🏗️ EPC Project Intelligence Portal

A Streamlit web application for EPC (Engineering, Procurement & Construction) project milestone risk management.

## Features

- **Three-Layer Risk Scoring** — Probability of Delay (PoD), Cost of Delay (CoD), Cash Flow Timing Sensitivity (CFTS)
- **Interactive Dashboard** — Portfolio-level KPIs and ranked risk matrix with Plotly charts
- **Milestone Management** — Add/delete milestones with full resource planning (labourers, materials, machinery)
- **Daily Spend Logging** — Track wages, materials, and machinery costs day-by-day
- **Optimisation Suggestions** — Automated alerts for overspend, deadline risk, cash runway, etc.
- **CSV Export** — Download risk reports and daily logs

## Scoring Formula

```
Risk Score (0–100) = PoD × 0.40 + CoD_norm × 0.35 + CFTS × 0.25
```

| Layer | Weight | Description |
|-------|--------|-------------|
| Probability of Delay (PoD) | 40% | Budget burn rate, time consumed, urgency |
| Cost of Delay normalised (CoD_norm) | 35% | Daily financial impact if milestone slips |
| Cash Flow Timing Sensitivity (CFTS) | 25% | Proximity of payment trigger |

## Risk Levels

| Score | Level |
|-------|-------|
| 70–100 | 🔴 CRITICAL |
| 45–69 | 🟡 HIGH |
| 25–44 | 🔵 MEDIUM |
| 0–24 | 🟢 LOW |

---

## 🚀 Deploy to Streamlit Cloud (Recommended — Free)

1. **Fork or push this repo to GitHub**

2. **Go to [share.streamlit.io](https://share.streamlit.io)**

3. **Sign in with GitHub** and click **"New app"**

4. Fill in:
   - **Repository:** `your-username/your-repo-name`
   - **Branch:** `main`
   - **Main file path:** `app.py`

5. Click **Deploy** — live in ~60 seconds!

> ⚠️ Note: Streamlit Cloud apps use ephemeral storage. Data saved in `epc_data.json` will reset on each restart. For persistent storage, connect a database (see below).

---

## 🖥️ Run Locally

```bash
# 1. Clone the repo
git clone https://github.com/your-username/epc-portal.git
cd epc-portal

# 2. Create a virtual environment (optional but recommended)
python -m venv venv
source venv/bin/activate      # macOS/Linux
venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
streamlit run app.py
```

Then open `http://localhost:8501` in your browser.

---

## 📁 File Structure

```
epc-portal/
├── app.py              # Main Streamlit application
├── requirements.txt    # Python dependencies
├── .gitignore
├── README.md
└── epc_data.json       # Auto-created on first use (gitignored)
```

---

## 🔧 Optional: Persistent Storage

For production use, replace the JSON file with a database. Streamlit Cloud supports:

- **Streamlit Secrets + Supabase** (free PostgreSQL)
- **TiDB Cloud** (free MySQL-compatible)
- **MongoDB Atlas** (free NoSQL)

See [Streamlit docs on connections](https://docs.streamlit.io/develop/concepts/connections) for setup guides.
