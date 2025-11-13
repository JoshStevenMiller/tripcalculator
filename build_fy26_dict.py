# build_fy26_dict.py
# One-time script: convert FY2026 per diem Excel into a seasonal FY26_RATES dict.

from pathlib import Path
import re
import datetime as dt
import pandas as pd

EXCEL_PATH = Path("FY2026_PerDiemMasterRatesFile.xlsx")
OUTPUT_PATH = Path("fy26_rates.py")


def find_column(df: pd.DataFrame, keywords) -> str:
    """
    Find the first column whose name contains ALL of the given keywords (case-insensitive).
    Example: find_column(df, ["fy26", "lodging"]) -> "FY26 Lodging Rate"
    """
    for c in df.columns:
        name = str(c).lower()
        if all(k in name for k in keywords):
            return c
    raise ValueError(f"Could not find a column with keywords {keywords}")


def parse_season_date(val) -> dt.date | None:
    """
    Convert strings like 'October 1' or 'Feb 28' into a date in the FY26 window.
    FY26 runs 1 Oct 2025 – 30 Sep 2026.
    - Months Oct/Nov/Dec -> year 2025
    - Months Jan–Sep     -> year 2026
    Blank/NaN -> None
    """
    if pd.isna(val):
        return None
    s = str(val).strip()
    if not s:
        return None

    # Extract month and day
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2})", s)
    if not m:
        # Try letting pandas guess (will default to current year, but we only need month/day)
        dt_guess = pd.to_datetime(s, errors="coerce")
        if pd.isna(dt_guess):
            return None
        month = dt_guess.month
        day = dt_guess.day
    else:
        month_name = m.group(1)
        day = int(m.group(2))
        # Map month name -> month number
        month = pd.to_datetime(month_name, format="%B").month  # 'October' etc.

    # FY26 window year logic
    if month >= 10:  # Oct, Nov, Dec
        year = 2025
    else:            # Jan–Sep
        year = 2026

    return dt.date(year, month, day)


def clean_money(series: pd.Series) -> pd.Series:
    """Strip $, commas, spaces from a currency column and convert to float."""
    return pd.to_numeric(
        series.astype(str).str.replace(r"[^0-9.\-]", "", regex=True),
        errors="coerce",
    )


def main():
    if not EXCEL_PATH.exists():
        raise FileNotFoundError(f"Excel file not found: {EXCEL_PATH}")

    # Header row is the SECOND row (first row is the big title line)
    df = pd.read_excel(EXCEL_PATH, sheet_name=0, header=1)
    df.columns = [str(c).strip() for c in df.columns]

    state_col   = find_column(df, ["state"])
    dest_col    = find_column(df, ["destination"])
    season_bcol = find_column(df, ["season", "begin"])
    season_ecol = find_column(df, ["season", "end"])
    lodging_col = find_column(df, ["fy26", "lodging"])
    mie_col     = find_column(df, ["fy26", "m&ie"])

    sub = df[[state_col, dest_col, season_bcol, season_ecol, lodging_col, mie_col]].copy()

    # Special handling: the top "Standard CONUS" row has empty STATE, but DESTINATION text.
    sub[state_col] = sub[state_col].fillna("")
    sub[dest_col]  = sub[dest_col].fillna("")

    # Clean currency
    sub[lodging_col] = clean_money(sub[lodging_col])
    sub[mie_col]     = clean_money(sub[mie_col])

    # Drop rows without money values
    sub = sub.dropna(subset=[lodging_col, mie_col])

    rates: dict[str, dict[str, list[dict]]] = {}

    for _, row in sub.iterrows():
        state = str(row[state_col]).strip()
        dest  = str(row[dest_col]).strip()

        # Standard CONUS fallback row (first one)
        if not state:
            state = "CONUS"
        if not dest:
            dest = "Standard CONUS"

        sb = parse_season_date(row[season_bcol])
        se = parse_season_date(row[season_ecol])
        lod = float(row[lodging_col])
        mie = float(row[mie_col])

        # If season dates are missing, treat as full FY window
        entry = {
            "season_begin": sb.isoformat() if sb else None,
            "season_end":   se.isoformat() if se else None,
            "lodging": lod,
            "mie": mie,
        }

        if state not in rates:
            rates[state] = {}
        if dest not in rates[state]:
            rates[state][dest] = []
        rates[state][dest].append(entry)

    # Write fy26_rates.py
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        f.write("# Auto-generated from build_fy26_dict.py\n")
        f.write("FY26_RATES = {\n")
        for state in sorted(rates.keys()):
            f.write(f'    "{state}": {{\n')
            for dest in sorted(rates[state].keys()):
                f.write(f'        "{dest}": [\n')
                for s in rates[state][dest]:
                    sb = repr(s["season_begin"])
                    se = repr(s["season_end"])
                    lod = s["lodging"]
                    mie = s["mie"]
                    f.write(
                        f"            {{'season_begin': {sb}, 'season_end': {se}, "
                        f"'lodging': {lod:.2f}, 'mie': {mie:.2f}}},\n"
                    )
                f.write("        ],\n")
            f.write("    },\n")
        f.write("}\n")

    print(
        f"Wrote {sum(len(dests) for dests in rates.values())} locations with "
        f"{sum(len(seasons) for dests in rates.values() for seasons in dests.values())} season rows "
        f"to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
