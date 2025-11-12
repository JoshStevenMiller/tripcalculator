# Streamlit Trip Cost Calculator (RLAS + DTS)
# --------------------------------------------------
# Host on Streamlit Cloud or run locally with:
#   pip install streamlit pandas
#   streamlit run streamlit_trip_cost_app.py
#
# What it does
# - No Excel upload required: monthly pay table is hard-coded below (from MDEP Calculator.xlsx).
# - User adds one or more "lines" consisting of: Rank, # of people, # of days, Rental Car checkbox
# - RLAS Cost (orders pay): Uses the embedded monthly base pay, divides by 30 (daily),
#   multiplies by days and headcount; shows per‑line totals and an RLAS grand total
# - DTS Cost (travel):
#     * Per diem: $85 per person per day (applied from the per-line people×days)
#     * Airfare: user inputs the number of Soldiers using airfare (global) × $410
#     * Rental car: **global override** (cars × $50 × days) OR per-line (1 car per checked line × days)
#       (If global cars > 0, per-line rental is ignored to prevent double counting.)
# - Outputs overall totals and lets the user download an Excel workbook with:
#     * Sheet "RLAS Lines" (per‑line RLAS computation)
#     * Sheet "DTS Summary" (per diem, airfare, rental, and DTS total)
#     * Sheet "Overview" (RLAS total, DTS total, and Overall Grand Total)

import io
from typing import Dict, List, Tuple

import pandas as pd
import streamlit as st

# ---------- Embedded monthly base pay (from provided Excel) ----------
# Rank → monthly pay (USD)
RANK_TO_MONTHLY: Dict[str, float] = {
    "O10": 18808.20,
    "O9": 18808.20,
    "O8": 18359.10,
    "O7": 16202.10,
    "O6": 13596.30,
    "O5": 11592.30,
    "O4": 10020.90,
    "O3": 7453.80,
    "O2": 6375.30,
    "O1": 5031.30,
    "W5": 10294.50,
    "W4": 8891.10,
    "W3": 7851.90,
    "W2": 6271.20,
    "W1": 5574.30,
    "E9": 8114.70,
    "E8": 6739.20,
    "E7": 5951.10,
    "E6": 4858.80,
    "E5": 3959.40,
    "E4": 3524.70,
    "E3": 3081.00,
    "E2": 2599.20,
    "E1": 2319.00,
}

ALL_RANKS: List[str] = list(RANK_TO_MONTHLY.keys())

# DTS fixed rates
PER_DIEM_RATE = 85.0   # per person per day
AIRFARE_RATE = 410.0   # per traveler (global input for count)
RENTAL_RATE  = 50.0    # per car per day

# ---------- Helpers ----------

def compute_rlas_line(rank: str, people: int, days: int) -> Tuple[dict, float]:
    monthly = RANK_TO_MONTHLY[rank]
    daily_rate = monthly / 30.0
    total = daily_rate * days * people
    rec = {
        "Rank": rank,
        "People": people,
        "Days": days,
        "Daily Rate": round(daily_rate, 2),
        "RLAS Line Total": round(total, 2),
    }
    return rec, total


def compute_per_diem(people: int, days: int) -> float:
    return PER_DIEM_RATE * people * days


def init_rows(n: int = 3):
    if "rows" not in st.session_state:
        st.session_state.rows = [
            {"rank": "E4", "people": 1, "days": 1, "rental": False},
            {"rank": "E5", "people": 1, "days": 1, "rental": False},
            {"rank": "O1", "people": 1, "days": 1, "rental": False},
        ][:n]


# ---------- UI ----------
st.set_page_config(page_title="Trip Cost Calculator", page_icon="🪖", layout="wide")
st.title("Travel / Orders Cost Calculator")
st.caption("RLAS (orders pay) + DTS (travel): add lines → set airfare/rental → compute totals → download Excel.")

init_rows(3)

st.subheader("1) Attendees & Orders (per line)")
left, right = st.columns([3, 1])
with right:
    if st.button("➕ Add another line", use_container_width=True):
        st.session_state.rows.append({"rank": "E4", "people": 1, "days": 1, "rental": False})
    if st.button("♻️ Reset lines", use_container_width=True):
        st.session_state.rows = []
        init_rows(3)

with left:
    for i, row in enumerate(st.session_state.rows):
        c1, c2, c3, c4, c5 = st.columns([1.3, 1, 1, 1.2, 0.4])
        with c1:
            st.session_state.rows[i]["rank"] = st.selectbox(
                f"Rank (line {i+1})", ALL_RANKS, index=ALL_RANKS.index(row["rank"]) if row["rank"] in ALL_RANKS else 0, key=f"rank_{i}"
            )
        with c2:
            st.session_state.rows[i]["people"] = st.number_input(
                "People", min_value=1, step=1, value=int(row["people"]), key=f"people_{i}"
            )
        with c3:
            st.session_state.rows[i]["days"] = st.number_input(
                "Days on orders", min_value=1, step=1, value=int(row["days"]), key=f"days_{i}"
            )
        with c4:
            st.session_state.rows[i]["rental"] = st.checkbox(
                "Rental car (info)", value=bool(row["rental"]), key=f"rental_{i}", help="Info only unless global cars=0 (see below)."
            )
        with c5:
            if st.button("🗑️", key=f"del_{i}", help="Delete this line"):
                st.session_state.rows.pop(i)
                st.rerun()

st.divider()

st.subheader("2) DTS Inputs (global)")
colA, colB, colC = st.columns([1.2, 1.2, 1.2])
with colA:
    st.metric("Per diem rate", f"${PER_DIEM_RATE:,.2f}/person/day")
with colB:
    st.metric("Airfare rate", f"${AIRFARE_RATE:,.2f}/traveler")
with colC:
    st.metric("Rental rate", f"${RENTAL_RATE:,.2f}/car/day")

col1, col2, col3 = st.columns([1.2, 1.2, 1.2])
with col1:
    airfare_count = st.number_input("Number of Soldiers using airfare", min_value=0, step=1, value=0)
with col2:
    rental_cars_global = st.number_input("Number of rental cars (global override)", min_value=0, step=1, value=0)
with col3:
    rental_days_global = st.number_input("Rental car days (global override)", min_value=0, step=1, value=0)

if rental_cars_global > 0 and rental_days_global == 0:
    st.info("Provide **Rental car days** for the global override, or set cars to 0 to use per‑line rental flags.")

with st.expander("How rental car is calculated"):
    st.write(
        """
        **Two modes** (to avoid double counting):
        1) **Global override set (cars > 0):** Rental = cars × $50 × rental_days_global. Per‑line checkboxes are ignored.
        2) **No global cars set (cars = 0):** Rental is computed from per‑line checkboxes, assuming **1 car per checked line** for that line’s number of days.
        """
    )

# ---------- Submit & Compute ----------
compute = st.button("Compute Totals", type="primary")

if compute:
    if not st.session_state.rows:
        st.warning("No lines to compute—add at least one.")
        st.stop()

    # --- RLAS (orders pay) ---
    rlas_rows = []
    rlas_total = 0.0
    per_diem_total = 0.0

    for row in st.session_state.rows:
        rec, line_total = compute_rlas_line(row["rank"], int(row["people"]), int(row["days"]))
        rlas_rows.append(rec)
        rlas_total += line_total
        per_diem_total += compute_per_diem(int(row["people"]), int(row["days"]))

    rlas_df = pd.DataFrame(rlas_rows)
    rlas_total = round(float(rlas_df["RLAS Line Total"].sum()), 2)

    # --- DTS: airfare ---
    airfare_total = round(AIRFARE_RATE * float(airfare_count), 2)

    # --- DTS: rental cars ---
    if rental_cars_global > 0:
        rental_total = round(RENTAL_RATE * float(rental_cars_global) * float(rental_days_global), 2)
        rental_mode = "Global override"
    else:
        # Per‑line mode: 1 car per checked line for that line's days
        rental_total = 0.0
        for row in st.session_state.rows:
            if bool(row.get("rental", False)):
                rental_total += RENTAL_RATE * float(int(row["days"]))
        rental_total = round(rental_total, 2)
        rental_mode = "Per‑line checkboxes"

    # --- DTS total ---
    per_diem_total = round(per_diem_total, 2)
    dts_total = round(per_diem_total + airfare_total + rental_total, 2)

    overall_total = round(rlas_total + dts_total, 2)

    # --- Display ---
    st.success("Computation complete.")

    c1, c2, c3 = st.columns(3)
    c1.metric("RLAS Total (orders pay)", f"${rlas_total:,.2f}")
    c2.metric("DTS Total (travel)", f"${dts_total:,.2f}")
    c3.metric("Overall Grand Total", f"${overall_total:,.2f}")

    st.subheader("RLAS Lines")
    st.dataframe(rlas_df, use_container_width=True)

    st.subheader("DTS Summary")
    dts_df = pd.DataFrame(
        {
            "Component": ["Per diem", "Airfare", f"Rental cars ({rental_mode})"],
            "Amount": [per_diem_total, airfare_total, rental_total],
            "Notes": [
                f"$85 × people × days (from lines)",
                f"$410 × {int(airfare_count)} traveler(s)",
                "Global override uses cars × $50 × days; otherwise 1 car per checked line × days",
            ],
        }
    )
    dts_df["Amount"] = dts_df["Amount"].round(2)
    st.dataframe(dts_df, use_container_width=True)

    # --- Build an Excel for download ---
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        rlas_df.to_excel(writer, index=False, sheet_name="RLAS Lines")
        dts_df.to_excel(writer, index=False, sheet_name="DTS Summary")
        overview = pd.DataFrame(
            {
                "Metric": ["RLAS Total", "DTS Total", "Overall Grand Total"],
                "Value": [rlas_total, dts_total, overall_total],
            }
        )
        overview.to_excel(writer, index=False, sheet_name="Overview")
    output.seek(0)

    st.download_button(
        label="📥 Download results (Excel)",
        data=output,
        file_name="trip_cost_results.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    with st.expander("Preview of embedded monthly base pay"):
        preview = (
            pd.DataFrame({"Rank": list(RANK_TO_MONTHLY.keys()), "Monthly": list(RANK_TO_MONTHLY.values())})
            .sort_values("Rank")
            .reset_index(drop=True)
        )
        st.dataframe(preview, use_container_width=True)

else:
    st.info("Add lines, set DTS inputs, then press **Compute Totals**.")
