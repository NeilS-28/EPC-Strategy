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
