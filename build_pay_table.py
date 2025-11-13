# build_pay_table.py
# One-time script: convert pay table in MDEP Calculator.xlsx into PAY_TABLE.

from pathlib import Path
import re
import pandas as pd

EXCEL_PATH = Path("MDEP Calculator.xlsx")
OUTPUT_PATH = Path("pay_table.py")


def main():
    if not EXCEL_PATH.exists():
        raise FileNotFoundError(f"Excel file not found: {EXCEL_PATH}")

    # Two header rows (Pay Grade / Over / etc.)
    df = pd.read_excel(EXCEL_PATH, sheet_name=0, header=[0, 1])

    # Normalize column names
    cols = []
    for top, bottom in df.columns:
        top_str = str(top).strip()
        bottom_str = "" if pd.isna(bottom) else str(bottom).strip()
        cols.append((top_str, bottom_str))
    df.columns = pd.MultiIndex.from_tuples(cols)

    # Find the Pay Grade column
    pay_col = None
    for c in df.columns:
        if c[0].lower().startswith("pay grade"):
            pay_col = c
            break
    if pay_col is None:
        raise ValueError("Could not find 'Pay Grade' column.")

    # Build list of band columns and their upper-year bound
    band_cols = []
    for c in df.columns:
        if c == pay_col:
            continue
        top, bottom = c
        top_lower = top.lower()

        # Skip empty / weird columns
        if not top and not bottom:
            continue

        # Example:
        # top: "2 or less"  -> upper = 2
        # top: "Over", bottom: "2" -> "Over 2" => upper = 3?  (we'll use bottom as upper bound directly)
        if "or less" in top_lower:
            # extract the "2" from "2 or less"
            m = re.search(r"(\d+)", top)
            if not m:
                continue
            upper = int(m.group(1))
        else:
            # usually "Over" in top, number in bottom row
            m = re.search(r"(\d+)", bottom)
            if not m:
                continue
            upper = int(m.group(1))

        band_cols.append((c, upper))

    # Sort bands by upper bound ascending
    band_cols.sort(key=lambda x: x[1])

    pay_table: dict[str, list[tuple[int, float]]] = {}

    for _, row in df.iterrows():
        raw_grade = row[pay_col]
        if pd.isna(raw_grade):
            continue
        grade = str(raw_grade).strip()
        # skip numeric-only grades like "10" if you only want O10 row, etc.
        if not any(ch.isalpha() for ch in grade):
            continue

        bands_for_grade: list[tuple[int, float]] = []
        for col, upper in band_cols:
            val = row[col]
            if pd.isna(val):
                continue
            # Some cells contain "NA" as string
            try:
                pay = float(str(val).replace(",", ""))
            except ValueError:
                continue
            bands_for_grade.append((upper, pay))

        if not bands_for_grade:
            continue

        # Ensure sorted & unique
        bands_for_grade.sort(key=lambda x: x[0])
        pay_table[grade] = bands_for_grade

    # Write pay_table.py
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        f.write("# Auto-generated from build_pay_table.py\n")
        f.write("PAY_TABLE = {\n")
        for grade in sorted(pay_table.keys()):
            f.write(f'    "{grade}": [\n')
            for upper, pay in pay_table[grade]:
                f.write(f"        ({upper}, {pay:.2f}),\n")
            f.write("    ],\n")
        f.write("}\n")

    print(f"Wrote PAY_TABLE for {len(pay_table)} grades to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
