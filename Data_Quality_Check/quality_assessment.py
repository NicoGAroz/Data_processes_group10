"""
Profiling script for the MOVER EPIC patient information table.
Grain assumed: one row per surgery (LOG_ID). Patient key: MRN.
Adjust PATH to your file. Column names are the ones you provided.
"""
import pandas as pd
import numpy as np

PATH = "patient_information.csv"

KEY_SURGERY = "LOG_ID"
KEY_PATIENT = "MRN"
TARGET = "ICU_ADMIN_FLAG"

DATETIME_COLS = [
    "HOSP_ADMSN_TIME", "HOSP_DISCH_TIME", "IN_OR_DTTM", "OUT_OR_DTTM",
    "AN_START_DATETIME", "AN_STOP_DATETIME",
]
DATE_COLS = ["SURGERY_DATE"]
NUMERIC_COLS = ["WEIGHT", "LOS"]
CATEGORICAL_COLS = [
    "SEX", "DISCH_DISP", "PRIMARY_ANES_TYPE_NM", "ASA_RATING",
    "PATIENT_CLASS_GROUP", "PATIENT_CLASS_NM",
]
# Code/label pairs that should map 1:1
CODE_LABEL_PAIRS = [
    ("DISCH_DISP_C", "DISCH_DISP"),
    ("ASA_RATING_C", "ASA_RATING"),
    ("PATIENT_CLASS_GROUP", "PATIENT_CLASS_NM"),
]

# Columns recorded at or after the end of surgery / hospital stay:
# NOT usable as features in a preoperative model (leakage). Verify each one.
LEAKAGE_SUSPECTS = [
    "DISCH_DISP_C", "DISCH_DISP", "HOSP_DISCH_TIME", "LOS",
    "OUT_OR_DTTM", "AN_STOP_DATETIME",
]

defects = []  # becomes your known-defects log draft


def log_defect(column, description, n_rows, how_found):
    defects.append({
        "table": "patient_information", "column": column,
        "description": description, "rows_affected": int(n_rows),
        "pct_rows": round(100 * n_rows / len(df), 3), "how_found": how_found,
        "impact_on_icu_prediction": "", "mitigation": "",  # fill in manually
    })


def section(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ---------------------------------------------------------------- load
df = pd.read_csv(PATH, low_memory=False)


# ---------------------------------------------------------------- 1. overview
section("1. OVERVIEW")
print(f"Rows: {len(df):,} | Columns: {df.shape[1]}")
df.info()
expected = [KEY_SURGERY, KEY_PATIENT, TARGET] + DATETIME_COLS + DATE_COLS \
    + NUMERIC_COLS + CATEGORICAL_COLS + ["PRIMARY_PROCEDURE_NM"]
missing_cols = [c for c in expected if c not in df.columns]
print("Expected columns not found in file:", missing_cols or "none")

# Parse dates once; unparseable values become NaT and are counted
raw_text = df[DATETIME_COLS + DATE_COLS].copy()
for c in DATETIME_COLS + DATE_COLS:
    df[c] = pd.to_datetime(df[c], errors="coerce")
    n_fail = int(df[c].isna().sum() - raw_text[c].isna().sum())
    if n_fail:
        log_defect(c, "Values that could not be parsed as dates", n_fail,
                   "pd.to_datetime(errors='coerce')")


ft_in = df["HEIGHT"].astype("string").str.extract(r"(\d+)'\s*(\d+)")
df["HEIGHT_CM"] = (pd.to_numeric(ft_in[0]) * 12 + pd.to_numeric(ft_in[1])) * 2.54
n_unparsed = int((df["HEIGHT"].notna() & df["HEIGHT_CM"].isna()).sum())
print("HEIGHT values not parsed:", n_unparsed)

df["WEIGHT_KG"] = df["WEIGHT"] * 0.0283495      # ounces to kg (confirm in docs)
df["BMI"] = df["WEIGHT_KG"] / (df["HEIGHT_CM"] / 100) ** 2
df["AGE_YEARS"] = pd.to_numeric(df["BIRTH_DATE"])

# ---------------------------------------------------------------- 2. missing
section("2. MISSING VALUES (% per column)")
miss = (df.isna().mean() * 100).sort_values(ascending=False).round(2)
print(miss[miss > 0])
for c, pct in miss[miss > 0].items():
    log_defect(c, "Missing values", df[c].isna().sum(), "isna() rate")

# Hidden missing codes in text columns
for c in CATEGORICAL_COLS + ["PRIMARY_PROCEDURE_NM"]:
    s = df[c].astype("string").str.strip().str.lower()
    hidden = s.isin(["", "unknown", "unk", "n/a", "na", "none", "null", "-1", "999"])
    if hidden.sum():
        print(f"{c}: {hidden.sum()} hidden-missing strings")
        log_defect(c, "Hidden missing codes (blank/unknown/N/A...)",
                   hidden.sum(), "string match")

# Does missingness relate to the outcome? (decides imputation strategy)
section("2b. MISSINGNESS vs ICU OUTCOME")
icu = df[TARGET].astype("string").str.strip().str.lower().map(
    {"1": 1, "0": 0, "yes": 1, "no": 0, "y": 1, "n": 0, "true": 1, "false": 0})
if icu.isna().all():
    print("Could not map ICU_ADMIN_FLAG to 0/1; check its values below.")
else:
    for c in ["HEIGHT", "WEIGHT", "ASA_RATING", "PRIMARY_ANES_TYPE_NM"]:
        print(f"ICU rate | {c} missing: "
              f"{icu[df[c].isna()].mean():.3f} vs present: "
              f"{icu[df[c].notna()].mean():.3f}")

# ---------------------------------------------------------------- 3. duplicates
section("3. DUPLICATES")
n_full = df.duplicated().sum()
n_key = df.duplicated(subset=[KEY_SURGERY]).sum()
print(f"Fully duplicated rows: {n_full}")
print(f"Repeated {KEY_SURGERY} (should be 0 if grain = one row per surgery): {n_key}")
if n_full:
    log_defect(KEY_SURGERY, "Fully duplicated rows", n_full, "df.duplicated()")
if n_key:
    log_defect(KEY_SURGERY, "Repeated surgery ID (conflicting versions?)",
               n_key, "duplicated(subset=LOG_ID)")
    dup_ids = df[df.duplicated(subset=[KEY_SURGERY], keep=False)]
    print(dup_ids.sort_values(KEY_SURGERY).head(10))

per_patient = df.groupby(KEY_PATIENT)[KEY_SURGERY].nunique()
print(f"\nDistinct patients (MRN): {per_patient.shape[0]:,}")
print(f"Patients with more than one surgery: {(per_patient > 1).sum():,}")
print(per_patient.describe())
# Note: repeat surgeries per patient matter for the train/test split
# (split by MRN to avoid leakage between train and test).

# ---------------------------------------------------------------- 4. categorical
section("4. CATEGORICAL VALUES")
for c in CATEGORICAL_COLS:
    print(f"\n--- {c} ---")
    print(df[c].value_counts(dropna=False).head(15))

proc = df["PRIMARY_PROCEDURE_NM"].astype("string")
normalised = proc.str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
print(f"\nPRIMARY_PROCEDURE_NM distinct: {proc.nunique()} raw, "
      f"{normalised.nunique()} after trim/lowercase/space normalisation")
if proc.nunique() != normalised.nunique():
    log_defect("PRIMARY_PROCEDURE_NM",
               "Procedure names differ only by case/spacing",
               proc.nunique() - normalised.nunique(), "normalisation compare")
print("Procedures with > 400 instances:",
      (normalised.value_counts() > 400).sum(), "(WP1 partition strategy)")

# Code <-> label consistency
section("4b. CODE/LABEL CONSISTENCY")
for code, label in CODE_LABEL_PAIRS:
    if code in df.columns and label in df.columns:
        n_labels = df.groupby(code, dropna=True)[label].nunique()
        bad = n_labels[n_labels > 1]
        n_codes = df.groupby(label, dropna=True)[code].nunique()
        bad2 = n_codes[n_codes > 1]
        print(f"{code} -> {label}: codes with >1 label: {len(bad)}; "
              f"labels with >1 code: {len(bad2)}")
        if len(bad) or len(bad2):
            log_defect(f"{code}/{label}", "Code and label not 1:1",
                       len(bad) + len(bad2), "groupby nunique")

# ---------------------------------------------------------------- 5. numeric
section("5. NUMERIC RANGES AND OUTLIERS")

PROFILE_COLS = ["HEIGHT_CM", "WEIGHT_KG", "BMI", "AGE_YEARS", "LOS"]
q = [0, .001, .01, .05, .5, .95, .99, .999, 1]

for c in PROFILE_COLS:
    print(f"\n--- {c} ---")
    print(df[c].describe().round(2))
    print(df[c].quantile(q).round(2))

# Placeholder plausibility limits (adjust once units are confirmed in the docs).
# NaN values are ignored by these comparisons, so missing rows are not counted.
limits = {
    "HEIGHT_CM": (120, 230),
    "WEIGHT_KG": (20, 350),
    "BMI":       (10, 80),
    "AGE_YEARS": (0, 110),
    "LOS":       (0, None),     # only a lower bound: no negative stays
}

print("\nValues outside the placeholder limits:")
for c, (lo, hi) in limits.items():
    mask = pd.Series(False, index=df.index)
    if lo is not None:
        mask |= df[c] < lo
    if hi is not None:
        mask |= df[c] > hi
    n = int(mask.sum())
    print(f"  {c}: {n}  (limits {lo} to {hi})")
    if n:
        log_defect(c, f"Values outside plausible range ({lo} to {hi})",
                   n, "range rule (placeholder limits)")

# Look at the extremes directly before deciding anything
print("\nSmallest and largest values:")
for c in ["HEIGHT_CM", "WEIGHT_KG", "BMI"]:
    print(f"\n{c} lowest 5:\n{df[c].nsmallest(5).round(1).to_string()}")
    print(f"{c} highest 5:\n{df[c].nlargest(5).round(1).to_string()}")

# Outliers can matter differently for ICU admission: check the rate at the extremes
if not icu.isna().all():
    print("\nICU rate by BMI band:")
    bmi_band = pd.cut(df["BMI"], [0, 18.5, 25, 30, 35, 40, 200])
    print(icu.groupby(bmi_band, observed=True).agg(["mean", "count"]).round(3))
    print("\nICU rate by age band:")
    age_band = pd.cut(df["AGE_YEARS"], [-1, 17, 39, 59, 74, 120])
    print(icu.groupby(age_band, observed=True).agg(["mean", "count"]).round(3))


# ---------------------------------------------------------------- 6. dates
section("6. DATE AND TIMESTAMP LOGIC")
print("Date ranges:")
for c in DATETIME_COLS + DATE_COLS:
    print(f"  {c}: {df[c].min()} -> {df[c].max()}")

checks = {
    "HOSP_DISCH_TIME < HOSP_ADMSN_TIME": df["HOSP_DISCH_TIME"] < df["HOSP_ADMSN_TIME"],
    "OUT_OR_DTTM < IN_OR_DTTM": df["OUT_OR_DTTM"] < df["IN_OR_DTTM"],
    "AN_STOP_DATETIME < AN_START_DATETIME": df["AN_STOP_DATETIME"] < df["AN_START_DATETIME"],
    "IN_OR_DTTM < HOSP_ADMSN_TIME": df["IN_OR_DTTM"] < df["HOSP_ADMSN_TIME"],
    "HOSP_DISCH_TIME < OUT_OR_DTTM": df["HOSP_DISCH_TIME"] < df["OUT_OR_DTTM"],
    "SURGERY_DATE != date of IN_OR_DTTM":
    df["IN_OR_DTTM"].notna()
    & (df["SURGERY_DATE"].dt.normalize() != df["IN_OR_DTTM"].dt.normalize()),
}
for name, mask in checks.items():
    mask = mask.fillna(False)
    print(f"{name}: {int(mask.sum())}")
    if mask.sum():
        log_defect("dates", f"Inconsistent order: {name}", mask.sum(),
                   "timestamp ordering check")

# LOS vs recomputed
los_calc = (df["HOSP_DISCH_TIME"] - df["HOSP_ADMSN_TIME"]).dt.total_seconds() / 86400
diff = (df["LOS"] - los_calc).abs()
print(f"\nLOS differs from (discharge - admission) by > 1 day: {(diff > 1).sum()}")
print("(Check the documentation for the unit of LOS first: days vs hours.)")

# Surgeries per year (throughput for the Source Systems / Scalability sections)
section("6b. THROUGHPUT")
per_year = df.groupby(df["SURGERY_DATE"].dt.year)[KEY_SURGERY].count()
print(per_year)
print(f"Average surgeries per day: "
      f"{len(df) / max(1, (df['SURGERY_DATE'].max() - df['SURGERY_DATE'].min()).days):.1f}")
print(f"In-memory size: {df.memory_usage(deep=True).sum() / 1e6:.1f} MB")

# ---------------------------------------------------------------- 7. target
section("7. TARGET: ICU_ADMIN_FLAG")
print(df[TARGET].value_counts(dropna=False))
if not icu.isna().all():
    print(f"\nICU admission rate: {icu.mean() * 100:.1f}%  (WP1 reference: 45.3%)")
    print("\nICU rate by PATIENT_CLASS_GROUP:")
    print(icu.groupby(df["PATIENT_CLASS_GROUP"]).mean().round(3))
    print("\nICU rate by ASA_RATING:")
    print(icu.groupby(df["ASA_RATING"]).mean().round(3))
    print("\nICU rate by PRIMARY_ANES_TYPE_NM:")
    print(icu.groupby(df["PRIMARY_ANES_TYPE_NM"]).mean().round(3))

# ---------------------------------------------------------------- 8. leakage
section("8. LEAKAGE REVIEW")
print("Likely NOT available before surgery (exclude from preoperative features):")
for c in LEAKAGE_SUSPECTS:
    print(f"  - {c}")
print("\nTo verify with the documentation (is it planned/pre-op or recorded"
      " during/after surgery?):")
for c in ["PRIMARY_ANES_TYPE_NM", "ASA_RATING", "ASA_RATING_C",
          "PATIENT_CLASS_GROUP", "PATIENT_CLASS_NM", "AN_START_DATETIME",
          "IN_OR_DTTM"]:
    print(f"  - {c}")

# ---------------------------------------------------------------- 9. defects log
section("9. DEFECTS LOG DRAFT")
log = pd.DataFrame(defects)
print(log[["column", "description", "rows_affected", "pct_rows"]].to_string(index=False))
log.to_csv("defects_log_draft.csv", index=False)
print("\nSaved defects_log_draft.csv (fill in impact and mitigation columns).")


# ---------------------------------------------------------------- 10. follow-up
section("10. FOLLOW-UP INVESTIGATIONS")

# a) Whitespace variants in anaesthesia type
print(df["PRIMARY_ANES_TYPE_NM"].map(repr).value_counts())

# b) Conflicting LOG_IDs (same ID, different values) after removing exact duplicates
deduped = df.drop_duplicates()
conflicts = deduped[deduped.duplicated("LOG_ID", keep=False)].sort_values("LOG_ID")
print(len(conflicts), "rows share a LOG_ID but differ")
print(conflicts.T.head(25))        # transposed so you can compare versions side by side

# c) ICU rate after deduplication
print("ICU rate after dedup:", (deduped["ICU_ADMIN_FLAG"] == "Yes").mean())

# d) Discharge code that has two labels
x = df.groupby("DISCH_DISP_C")["DISCH_DISP"].unique()
print(x[x.str.len() > 1])