# streamlit_trip_cost_app.py
# --------------------------------------------------
# Travel / Orders Cost Calculator
#
# - Simple mode:
#     * Uses a fixed monthly pay per rank (RANK_TO_MONTHLY_SIMPLE).
#     * Uses Standard CONUS lodging & M&IE: 110 / 68.
#     * User enters: rank, people, days, rental flags.
#
# - Advanced mode:
#     * Uses PAY_TABLE from pay_table.py (rank + years of service).
#     * Uses FY26_RATES from fy26_rates.py (STATE/DESTINATION with seasons).
#     * User enters: travel start/end date, state, destination,
#       rank, years-of-service, people, rental flags.
#
# Both modes:
#     * Airfare: # Travelers × $410.
#     * Rental cars:
#           - Global override: cars × $50 × days, OR
#           - Per-line: 1 car per checked line × that line's days (simple)
#             or × trip days (advanced).
#     * Shows ZIP→ZIP mileage (info only).
#     * Produces Excel export (RLAS Lines, DTS Summary, Overview).
#
# Requirements:
#   streamlit
#   pandas
#   openpyxl
#   pgeocode

import io
import re
from datetime import date, timedelta
from typing import Dict, List, Tuple, Optional, Any

import pandas as pd
import streamlit as st
import pgeocode

from pay_table import PAY_TABLE
from fy26_rates import FY26_RATES

# ---------- Embedded SIMPLE monthly base pay (one value per rank) ----------
# These are your “good enough” values for Simple mode.
RANK_TO_MONTHLY_SIMPLE: Dict[str, float] = {
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
ALL_RANKS: List[str] = list(RANK_TO_MONTHLY_SIMPLE.keys())

# Standard CONUS (used in Simple mode)
STANDARD_CONUS_LODGING = 110.0
STANDARD_CONUS_MIE = 68.0

# DTS fixed rates
AIRFARE_RATE = 410.0  # per traveler
RENTAL_RATE = 50.0    # per car per day


# ---------- Helpers: generic ----------

def normalize_zip(z: str) -> Optional[str]:
    m = re.match(r"(\d{5})", z or "")
    return m.group(1) if m else None


@st.cache_data(show_spinner=False)
def zip_distance_miles(zip_a: str, zip_b: str) -> Optional[float]:
    """Return great-circle distance between two US ZIPs in miles, or None."""
    try:
        geod = pgeocode.GeoDistance("US")
        km = geod.query_postal_code(zip_a, zip_b)
        if pd.isna(km):
            return None
        return float(km) * 0.621371
    except Exception:
        return None


def daterange(start: date, end: date):
    """Yield each date from start to end inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


# ---------- Helpers: Simple mode RLAS ----------

def compute_rlas_line_simple(rank: str, people: int, days: int) -> Tuple[dict, float]:
    """Simple mode: use RANK_TO_MONTHLY_SIMPLE mapping."""
    monthly = RANK_TO_MONTHLY_SIMPLE[rank]
    daily_rate = monthly / 30.0
    total = daily_rate * days * people
    rec = {
        "Rank": rank,
        "People": people,
        "Days": days,
        "Years of Service": None,
        "Daily Rate": round(daily_rate, 2),
        "RLAS Line Total": round(total, 2),
    }
    return rec, total


# ---------- Helpers: Advanced mode RLAS (PAY_TABLE) ----------

def get_monthly_pay_from_table(rank: str, yos: int) -> float:
    """
    Look up monthly pay from PAY_TABLE given rank and years of service.
    Expected PAY_TABLE[rank] = list of (max_years, monthly_pay) sorted by max_years.
    """
    bands = PAY_TABLE[rank]
    for max_years, monthly in bands:
        if yos <= max_years:
            return float(monthly)
    # If yos is beyond last band, use the last band's pay
    return float(bands[-1][1])


def compute_rlas_line_advanced(rank: str, yos: int, people: int, days: int) -> Tuple[dict, float]:
    monthly = get_monthly_pay_from_table(rank, yos)
    daily_rate = monthly / 30.0
    total = daily_rate * days * people
    rec = {
        "Rank": rank,
        "People": people,
        "Days": days,
        "Years of Service": yos,
        "Daily Rate": round(daily_rate, 2),
        "RLAS Line Total": round(total, 2),
    }
    return rec, total


# ---------- Helpers: FY26 seasonal per diem (Advanced mode) ----------

def _ensure_date(d: Any, default: Optional[date] = None) -> date:
    """Convert FY26_RATES season_begin/season_end values to date objects."""
    if d is None or (isinstance(d, float) and pd.isna(d)):
        if default is None:
            raise ValueError("No default date provided")
        return default
    if isinstance(d, date):
        return d
    # Assume string or something pandas can parse
    return pd.to_datetime(d).date()


def get_state_choices() -> List[str]:
    return sorted(FY26_RATES.keys())


def get_destinations_for_state(state: str) -> List[str]:
    return sorted(FY26_RATES[state].keys())


def get_daily_rates_by_date(state: str, destination: str,
                            start: date, end: date) -> List[Tuple[date, float, float]]:
    """
    For each calendar day in [start, end], return (date, lodging_rate, mie_rate)
    based on FY26_RATES seasons.
    """
    seasons = FY26_RATES[state][destination]  # list of dicts
    out: List[Tuple[date, float, float]] = []

    for current_day in daterange(start, end):
        applicable = None
        for s in seasons:
            sb = _ensure_date(s.get("season_begin"), default=start)
            se = _ensure_date(s.get("season_end"), default=end)
            if sb <= current_day <= se:
                applicable = s
                break
        if applicable is None:
            # Fall back to Standard CONUS if available, else raise
            if "CONUS" in FY26_RATES and "Standard CONUS" in FY26_RATES["CONUS"]:
                sc = FY26_RATES["CONUS"]["Standard CONUS"][0]
                lod = float(sc["lodging"])
                mie = float(sc["mie"])
            else:
                raise ValueError(f"No FY26 seasonal rate for {state}/{destination} on {current_day}")
        else:
            lod = float(applicable["lodging"])
            mie = float(applicable["mie"])
        out.append((current_day, lod, mie))

    return out


def compute_seasonal_per_diem_per_person(
    state: str,
    destination: str,
    start: date,
    end: date,
    apply_75: bool
) -> Tuple[float, float]:
    """
    Compute lodging & M&IE totals per person for the whole trip,
    using FY26 seasons + optional 75% rule on first/last day.
    Returns (lodging_total, mie_total) per person.
    """
    daily_rates = get_daily_rates_by_date(state, destination, start, end)
    num_days = len(daily_rates)

    lodging_total = sum(l for _, l, _ in daily_rates)

    # Apply 75% rule to M&IE
    mie_total = 0.0
    for idx, (_, _, mie_rate) in enumerate(daily_rates):
        if apply_75:
            if num_days == 1:
                factor = 0.75
            else:
                if idx == 0 or idx == num_days - 1:
                    factor = 0.75
                else:
                    factor = 1.0
        else:
            factor = 1.0
        mie_total += mie_rate * factor

    return lodging_total, mie_total


# ---------- Session-state helpers for line storage ----------

def init_simple_rows(n: int = 3):
    if "rows_simple" not in st.session_state:
        st.session_state.rows_simple = [
            {"rank": "E4", "people": 1, "days": 1, "rental": False},
            {"rank": "E5", "people": 1, "days": 1, "rental": False},
            {"rank": "O1", "people": 1, "days": 1, "rental": False},
        ][:n]


def init_advanced_rows(n: int = 3):
    if "rows_adv" not in st.session_state:
        st.session_state.rows_adv = [
            {"rank": "E4", "yos": 4, "people": 1, "rental": False},
            {"rank": "E5", "yos": 6, "people": 1, "rental": False},
            {"rank": "O1", "yos": 2, "people": 1, "rental": False},
        ][:n]


# ---------- UI setup ----------

st.set_page_config(page_title="Trip Cost Calculator", page_icon="🪖", layout="wide")
st.title("🪖 Travel / Orders Cost Calculator")
st.caption(
    "Simple mode: Standard CONUS + approximate pay per rank.  "
    "Advanced mode: FY26 seasonal per diem + exact pay by years-of-service."
)

# Shared DTS inputs (used by both modes)
st.subheader("Global DTS Inputs")

col_zip1, col_zip2 = st.columns(2)
with col_zip1:
    origin_zip = st.text_input("Origin ZIP (for mileage)", value="66048", max_chars=10)
with col_zip2:
    dest_zip_for_mileage = st.text_input("Destination ZIP (for mileage)", value="35005", max_chars=10)

colDTS1, colDTS2, colDTS3 = st.columns(3)
with colDTS1:
    airfare_count = st.number_input("Number of Soldiers using airfare", min_value=0, step=1, value=1)
with colDTS2:
    rental_cars_global = st.number_input("Number of rental cars (global override)", min_value=0, step=1, value=0)
with colDTS3:
    rental_days_global = st.number_input("Rental car days (global override)", min_value=0, step=1, value=0)

if rental_cars_global > 0 and rental_days_global == 0:
    st.info("Provide **Rental car days** for the global override, or set cars to 0 to use per-line rental flags.")

st.divider()

# ---------- Tabs: Simple vs Advanced ----------
tab_simple, tab_advanced = st.tabs(["Simple mode", "Advanced mode"])


# ============================
# SIMPLE MODE TAB
# ============================
with tab_simple:
    st.subheader("Simple mode: Standard CONUS + approximate pay per rank")
    init_simple_rows(3)

    left, right = st.columns([3, 1])
    with right:
        if st.button("➕ Add line (simple)", use_container_width=True):
            st.session_state.rows_simple.append({"rank": "E4", "people": 1, "days": 1, "rental": False})
        if st.button("♻️ Reset lines (simple)", use_container_width=True):
            st.session_state.rows_simple = []
            init_simple_rows(3)

    with left:
        for i, row in enumerate(st.session_state.rows_simple):
            c1, c2, c3, c4, c5 = st.columns([1.3, 1, 1, 1.2, 0.4])
            with c1:
                st.session_state.rows_simple[i]["rank"] = st.selectbox(
                    f"Rank (line {i+1})",
                    ALL_RANKS,
                    index=ALL_RANKS.index(row["rank"]) if row["rank"] in ALL_RANKS else 0,
                    key=f"s_rank_{i}",
                )
            with c2:
                st.session_state.rows_simple[i]["people"] = st.number_input(
                    "People", min_value=1, step=1, value=int(row["people"]), key=f"s_people_{i}"
                )
            with c3:
                st.session_state.rows_simple[i]["days"] = st.number_input(
                    "Days on orders", min_value=1, step=1, value=int(row["days"]), key=f"s_days_{i}"
                )
            with c4:
                st.session_state.rows_simple[i]["rental"] = st.checkbox(
                    "Rental car (info)",
                    value=bool(row["rental"]),
                    key=f"s_rental_{i}",
                    help="Used only if global cars=0.",
                )
            with c5:
                if st.button("🗑️", key=f"s_del_{i}", help="Delete this line"):
                    st.session_state.rows_simple.pop(i)
                    st.experimental_rerun()

    st.markdown(
        f"**Standard CONUS per diem used:** Lodging ${STANDARD_CONUS_LODGING:,.2f}/night, "
        f"M&IE ${STANDARD_CONUS_MIE:,.2f}/day."
    )
    apply_75_simple = st.checkbox(
        "75% M&IE for first & last day (simple)",
        value=True,
        help="Applies to each line individually based on its 'Days on orders'.",
    )

    compute_simple = st.button("🚀 Compute (Simple mode)", type="primary")

    if compute_simple:
        if not st.session_state.rows_simple:
            st.warning("No lines to compute in Simple mode.")
            st.stop()

        # --- RLAS Simple ---
        rlas_rows = []
        rlas_total = 0.0
        total_people = 0

        mie_total = 0.0
        lodging_total = 0.0

        for row in st.session_state.rows_simple:
            rank = row["rank"]
            people = int(row["people"])
            days = int(row["days"])

            rec, line_total = compute_rlas_line_simple(rank, people, days)
            rlas_rows.append(rec)
            rlas_total += line_total
            total_people += people

            # Lodging = standard rate per person per night
            lodging_total += STANDARD_CONUS_LODGING * people * days

            # M&IE with 75% (per line)
            if apply_75_simple:
                if days == 1:
                    mie_days_effective = 0.75
                elif days >= 2:
                    mie_days_effective = 0.75 + (days - 2) + 0.75
                else:
                    mie_days_effective = 0.0
            else:
                mie_days_effective = days
            mie_total += STANDARD_CONUS_MIE * mie_days_effective * people

        rlas_df = pd.DataFrame(rlas_rows)
        rlas_total = round(rlas_total, 2)
        mie_total = round(mie_total, 2)
        lodging_total = round(lodging_total, 2)

        # --- DTS: Airfare ---
        airfare_total = round(AIRFARE_RATE * float(airfare_count), 2)

        # --- DTS: Rentals ---
        if rental_cars_global > 0:
            rental_total = round(
                RENTAL_RATE * float(rental_cars_global) * float(rental_days_global), 2
            )
            rental_mode = "Global override"
        else:
            rental_total = 0.0
            for row in st.session_state.rows_simple:
                if bool(row.get("rental", False)):
                    rental_total += RENTAL_RATE * float(int(row["days"]))
            rental_total = round(rental_total, 2)
            rental_mode = "Per-line checkboxes (per-line days)"

        # --- Mileage (info only) ---
        oz = normalize_zip(origin_zip)
        dz = normalize_zip(dest_zip_for_mileage)
        miles = zip_distance_miles(oz, dz) if (oz and dz) else None

        dts_total = round(mie_total + lodging_total + airfare_total + rental_total, 2)
        overall_total = round(rlas_total + dts_total, 2)

        # --- Display ---
        st.success("Simple mode computation complete.")
        c1, c2, c3 = st.columns(3)
        c1.metric("RLAS Total (orders pay)", f"${rlas_total:,.2f}")
        c2.metric("DTS Total (travel)", f"${dts_total:,.2f}")
        c3.metric("Overall Grand Total", f"${overall_total:,.2f}")

        st.subheader("RLAS Lines (Simple)")
        st.dataframe(rlas_df, use_container_width=True)

        st.subheader("DTS Summary (Simple)")
        rows = [
            ("M&IE (Std CONUS)", mie_total, f"${STANDARD_CONUS_MIE:,.2f}/person/day; 75% F/LD: {'Yes' if apply_75_simple else 'No'}"),
            ("Lodging (Std CONUS)", lodging_total, f"${STANDARD_CONUS_LODGING:,.2f}/person/night"),
            ("Airfare", airfare_total, f"$410 × {int(airfare_count)} traveler(s)"),
            (f"Rental cars ({rental_mode})", rental_total, "$50/car/day"),
        ]
        if miles is not None and oz and dz:
            rows.append(("Mileage (info)", 0.0, f"{miles:,.1f} miles from {oz} to {dz}"))
        dts_df = pd.DataFrame(rows, columns=["Component", "Amount", "Notes"])
        dts_df["Amount"] = dts_df["Amount"].round(2)
        st.dataframe(dts_df, use_container_width=True)

        # Excel export
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
            label="📥 Download results (Simple mode, Excel)",
            data=output,
            file_name="trip_cost_results_simple.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


# ============================
# ADVANCED MODE TAB
# ============================
with tab_advanced:
    st.subheader("Advanced mode: FY26 seasonal per diem + exact pay by YOS")

    init_advanced_rows(3)

    # Travel dates and per-diem location
    col_dates, col_loc = st.columns(2)
    with col_dates:
        travel_start = st.date_input("Travel start date", value=date(2025, 10, 1))
        travel_end = st.date_input("Travel end date", value=date(2025, 10, 5))
        if travel_end < travel_start:
            st.error("Travel end date must be on or after start date.")
    with col_loc:
        states = get_state_choices()
        default_state_idx = states.index("CONUS") if "CONUS" in states else 0
        state = st.selectbox("Per diem State", options=states, index=default_state_idx)
        dests = get_destinations_for_state(state)
        default_dest_idx = 0
        if state == "CONUS":
            for i, d in enumerate(dests):
                if "standard" in d.lower():
                    default_dest_idx = i
                    break
        destination = st.selectbox("Per diem Destination", options=dests, index=default_dest_idx)

    apply_75_adv = st.checkbox(
        "75% M&IE for first & last day (advanced)",
        value=True,
        help="Applied based on full trip dates (not per line).",
    )

    leftA, rightA = st.columns([3, 1])
    with rightA:
        if st.button("➕ Add line (advanced)", use_container_width=True):
            st.session_state.rows_adv.append({"rank": "E4", "yos": 4, "people": 1, "rental": False})
        if st.button("♻️ Reset lines (advanced)", use_container_width=True):
            st.session_state.rows_adv = []
            init_advanced_rows(3)

    with leftA:
        for i, row in enumerate(st.session_state.rows_adv):
            c1, c2, c3, c4, c5 = st.columns([1.3, 1, 1, 1.2, 0.4])
            with c1:
                st.session_state.rows_adv[i]["rank"] = st.selectbox(
                    f"Rank (line {i+1})",
                    ALL_RANKS,
                    index=ALL_RANKS.index(row["rank"]) if row["rank"] in ALL_RANKS else 0,
                    key=f"a_rank_{i}",
                )
            with c2:
                st.session_state.rows_adv[i]["yos"] = st.number_input(
                    "YOS", min_value=0, max_value=40, step=1, value=int(row["yos"]), key=f"a_yos_{i}"
                )
            with c3:
                st.session_state.rows_adv[i]["people"] = st.number_input(
                    "People", min_value=1, step=1, value=int(row["people"]), key=f"a_people_{i}"
                )
            with c4:
                st.session_state.rows_adv[i]["rental"] = st.checkbox(
                    "Rental car (info)",
                    value=bool(row["rental"]),
                    key=f"a_rental_{i}",
                    help="Used only if global cars=0.",
                )
            with c5:
                if st.button("🗑️", key=f"a_del_{i}", help="Delete this line"):
                    st.session_state.rows_adv.pop(i)
                    st.experimental_rerun()

    compute_adv = st.button("🚀 Compute (Advanced mode)", type="primary")

    if compute_adv:
        if not st.session_state.rows_adv:
            st.warning("No lines to compute in Advanced mode.")
            st.stop()
        if travel_end < travel_start:
            st.error("Travel end date must be on or after start date.")
            st.stop()

        num_trip_days = (travel_end - travel_start).days + 1

        # --- RLAS Advanced ---
        rlas_rows = []
        rlas_total = 0.0
        total_people = 0
        for row in st.session_state.rows_adv:
            rank = row["rank"]
            yos = int(row["yos"])
            people = int(row["people"])
            rec, line_total = compute_rlas_line_advanced(rank, yos, people, num_trip_days)
            rlas_rows.append(rec)
            rlas_total += line_total
            total_people += people

        rlas_df = pd.DataFrame(rlas_rows)
        rlas_total = round(rlas_total, 2)

        # --- FY26 per diem per person (seasonal, with 75% rule) ---
        lodging_per_person, mie_per_person = compute_seasonal_per_diem_per_person(
            state, destination, travel_start, travel_end, apply_75_adv
        )
        lodging_total = round(lodging_per_person * total_people, 2)
        mie_total = round(mie_per_person * total_people, 2)

        # --- Airfare ---
        airfare_total = round(AIRFARE_RATE * float(airfare_count), 2)

        # --- DTS: Rentals ---
        if rental_cars_global > 0:
            rental_total = round(
                RENTAL_RATE * float(rental_cars_global) * float(rental_days_global), 2
            )
            rental_mode = "Global override"
        else:
            rental_total = 0.0
            for row in st.session_state.rows_adv:
                if bool(row.get("rental", False)):
                    rental_total += RENTAL_RATE * float(num_trip_days)
            rental_total = round(rental_total, 2)
            rental_mode = "Per-line checkboxes (trip days)"

        # --- Mileage (info only) ---
        oz = normalize_zip(origin_zip)
        dz = normalize_zip(dest_zip_for_mileage)
        miles = zip_distance_miles(oz, dz) if (oz and dz) else None

        # --- DTS & overall totals ---
        dts_total = round(mie_total + lodging_total + airfare_total + rental_total, 2)
        overall_total = round(rlas_total + dts_total, 2)

        # ---------- Display ----------
        st.success("Advanced mode computation complete.")

        st.markdown(
            f"**FY26 per diem used for {state} / {destination}:** "
            f"Lodging and M&IE from seasonal table, "
            f"travel dates {travel_start.isoformat()} → {travel_end.isoformat()}."
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("RLAS Total (orders pay)", f"${rlas_total:,.2f}")
        c2.metric("DTS Total (travel)", f"${dts_total:,.2f}")
        c3.metric("Overall Grand Total", f"${overall_total:,.2f}")

        st.subheader("RLAS Lines (Advanced)")
        st.dataframe(rlas_df, use_container_width=True)

        st.subheader("DTS Summary (Advanced)")
        rows = [
            ("M&IE (FY26, seasonal)", mie_total, f"Per-person total = ${mie_per_person:,.2f}; 75% F/LD: {'Yes' if apply_75_adv else 'No'}"),
            ("Lodging (FY26, seasonal)", lodging_total, f"Per-person total = ${lodging_per_person:,.2f}"),
            ("Airfare", airfare_total, f"$410 × {int(airfare_count)} traveler(s)"),
            (f"Rental cars ({rental_mode})", rental_total, "$50/car/day"),
        ]
        if miles is not None and oz and dz:
            rows.append(("Mileage (info)", 0.0, f"{miles:,.1f} miles from {oz} to {dz}"))
        dts_df = pd.DataFrame(rows, columns=["Component", "Amount", "Notes"])
        dts_df["Amount"] = dts_df["Amount"].round(2)
        st.dataframe(dts_df, use_container_width=True)

        # --- Excel export (Advanced) ---
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
            label="📥 Download results (Advanced mode, Excel)",
            data=output,
            file_name="trip_cost_results_advanced.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        with st.expander("Preview of PAY_TABLE & FY26_RATES keys"):
            st.write("Ranks in PAY_TABLE:", sorted(PAY_TABLE.keys()))
            st.write("States in FY26_RATES:", sorted(FY26_RATES.keys()))