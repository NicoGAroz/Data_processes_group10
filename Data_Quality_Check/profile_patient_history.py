"""
Profiling script for the MOVER EPIC patient history table.
Columns: mrn, diagnosis_code, dx_name
Assumed grain: one row per patient per diagnosis (many rows per patient).
Optional: set SURGERY_PATH to also check the link to patient_information.
"""
import os
import pandas as pd
import numpy as np

HISTORY_PATH = "./patient_history.csv"
SURGERY_PATH = "./patient_information.csv"     # optional cross-table checks

KEY_PATIENT = "mrn"
CODE = "diagnosis_code"
NAME = "dx_name"

defects = []


def log_defect(column, description, n_rows, how_found):
    defects.append({
        "table": "patient_history", "column": column,
        "description": description, "rows_affected": int(n_rows),
        "pct_rows": round(100 * n_rows / len(df), 3), "how_found": how_found,
        "impact_on_icu_prediction": "", "mitigation": "",   # fill in manually
    })


def section(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ---------------------------------------------------------------- load
# Read everything as text: codes such as "0123" or "E11.9" must not be altered.
df = pd.read_csv(HISTORY_PATH, dtype="string", keep_default_na=True)
df.columns = df.columns.str.strip().str.lower()

# ---------------------------------------------------------------- 1. overview
section("1. OVERVIEW")
print(f"Rows: {len(df):,} | Columns: {df.shape[1]}")
df.info()
print("Expected columns not found:",
      [c for c in [KEY_PATIENT, CODE, NAME] if c not in df.columns] or "none")
print(df.head(10).to_string())
print(f"\nDistinct patients: {df[KEY_PATIENT].nunique():,}")
print(f"Distinct diagnosis codes: {df[CODE].nunique():,}")
print(f"Distinct diagnosis names: {df[NAME].nunique():,}")

# ---------------------------------------------------------------- 2. missing
section("2. MISSING VALUES")
miss = (df.isna().mean() * 100).round(3)
print(miss)
for c in df.columns:
    n = int(df[c].isna().sum())
    if n:
        log_defect(c, "Missing values", n, "isna()")

for c in df.columns:
    s = df[c].str.strip().str.lower()
    hidden = s.isin(["", "unknown", "unk", "n/a", "na", "none", "null", "-1", "999"])
    if hidden.sum():
        print(f"{c}: {int(hidden.sum())} hidden-missing strings")
        print(df.loc[hidden.fillna(False), c].value_counts().head(5))
        log_defect(c, "Hidden missing codes (blank/unknown/N/A...)",
                   hidden.sum(), "string match")

# ---------------------------------------------------------------- 3. whitespace / case
section("3. WHITESPACE AND CASE VARIANTS")
for c in [CODE, NAME]:
    raw_n = df[c].nunique()
    norm = df[c].str.strip().str.replace(r"\s+", " ", regex=True).str.upper()
    print(f"{c}: {raw_n:,} distinct raw -> {norm.nunique():,} after "
          f"trim / collapse spaces / uppercase")
    n_ws = int((df[c].notna() & (df[c] != df[c].str.strip())).sum())
    print(f"  values with leading/trailing spaces: {n_ws}")
    if n_ws:
        log_defect(c, "Leading/trailing whitespace", n_ws, "compare with strip()")
    if raw_n != norm.nunique():
        log_defect(c, "Values differ only by case/spacing",
                   raw_n - norm.nunique(), "normalisation compare")

# Work on a normalised copy for the checks below (raw df stays untouched)
d = df.copy()
d[KEY_PATIENT] = d[KEY_PATIENT].str.strip()
d[CODE] = d[CODE].str.strip().str.upper()
d[NAME] = d[NAME].str.strip().str.replace(r"\s+", " ", regex=True)

# ---------------------------------------------------------------- 4. duplicates
section("4. DUPLICATES")
n_full = int(df.duplicated().sum())
n_pair = int(d.duplicated(subset=[KEY_PATIENT, CODE]).sum())
print(f"Fully duplicated rows (raw): {n_full:,}")
print(f"Repeated (mrn, diagnosis_code) after normalising: {n_pair:,}")
if n_full:
    log_defect("all", "Fully duplicated rows", n_full, "df.duplicated()")
if n_pair > n_full:
    log_defect([KEY_PATIENT, CODE].__str__(),
               "Same patient+code repeated beyond exact duplicates "
               "(different name or spacing?)", n_pair - n_full,
               "duplicated(subset=[mrn, diagnosis_code])")
    rep = d[d.duplicated(subset=[KEY_PATIENT, CODE], keep=False)]
    print(rep.sort_values([KEY_PATIENT, CODE]).head(10).to_string())

# Same patient + code with different names?
names_per_pair = d.groupby([KEY_PATIENT, CODE])[NAME].nunique()
print(f"(mrn, code) pairs with more than one dx_name: "
      f"{int((names_per_pair > 1).sum()):,}")

# ---------------------------------------------------------------- 5. code format
section("5. DIAGNOSIS CODE FORMAT")


def shape(s):
    return (s.str.replace(r"[A-Za-z]", "A", regex=True)
             .str.replace(r"[0-9]", "9", regex=True))


codes = d[CODE].dropna()
print("Code length distribution:")
print(codes.str.len().value_counts().sort_index())
print("\nTop code shapes (A = letter, 9 = digit):")
print(shape(codes).value_counts().head(15))

has_dot = codes.str.contains(r"\.", regex=True)
print(f"\nCodes with a dot: {int(has_dot.sum()):,} | without: {int((~has_dot).sum()):,}")
print("(Mixed dotted/undotted notation would make the same code look different.)")

multi = codes.str.contains(r"[,;|/ ]", regex=True)
print(f"Cells that look like several codes in one field: {int(multi.sum()):,}")
if multi.sum():
    print(codes[multi].head(10).to_string())
    log_defect(CODE, "Multiple codes (or spaces) in one cell", multi.sum(),
               "regex [,;|/ ]")

icd10 = codes.str.match(r"^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$")
icd9 = codes.str.match(r"^(\d{3}(\.\d{1,2})?|[EV]\d{2,3}(\.\d{1,2})?)$")
fmt = np.select([icd10 & ~icd9, icd9 & ~icd10, icd10 & icd9],
                ["ICD-10-like", "ICD-9-like", "ambiguous"], "other")
print("\nIndicative coding system (regex-based, check against the docs):")
print(pd.Series(fmt).value_counts())
n_other = int((fmt == "other").sum())
if n_other:
    print("Examples of codes matching neither pattern:")
    print(codes[fmt == "other"].value_counts().head(15))
    log_defect(CODE, "Codes matching neither ICD-9 nor ICD-10 pattern",
               n_other, "regex check")
if (fmt == "ICD-10-like").any() and (fmt == "ICD-9-like").any():
    log_defect(CODE, "Mixed ICD-9 and ICD-10 coding systems",
               int((fmt == "ICD-9-like").sum()), "regex check")

# ---------------------------------------------------------------- 6. code <-> name
section("6. CODE / NAME CONSISTENCY")
labels_per_code = d.groupby(CODE)[NAME].nunique()
codes_per_name = d.groupby(NAME)[CODE].nunique()
print(f"Codes with more than one dx_name: {int((labels_per_code > 1).sum()):,}")
print(f"dx_names with more than one code: {int((codes_per_name > 1).sum()):,}")
print("(The second is often legitimate: several specific codes can share one name.)")
if (labels_per_code > 1).sum():
    log_defect(f"{CODE}/{NAME}", "Same code with different names",
               int((labels_per_code > 1).sum()), "groupby nunique")
    bad = labels_per_code[labels_per_code > 1].index[:5]
    print(d[d[CODE].isin(bad)].groupby(CODE)[NAME].unique().to_string())

only_code = int((d[CODE].notna() & d[NAME].isna()).sum())
only_name = int((d[CODE].isna() & d[NAME].notna()).sum())
print(f"\nCode present but name missing: {only_code:,}")
print(f"Name present but code missing: {only_name:,}")

# ---------------------------------------------------------------- 7. distributions
section("7. DISTRIBUTIONS")
dedup = d.drop_duplicates(subset=[KEY_PATIENT, CODE])
per_patient = dedup.groupby(KEY_PATIENT).size()
print("Diagnoses per patient (after removing duplicates):")
print(per_patient.describe().round(2))
print(per_patient.quantile([.5, .9, .99, .999, 1]))

print("\nTop 20 diagnoses (by number of patients):")
top = dedup.groupby([CODE, NAME], dropna=False)[KEY_PATIENT].nunique() \
           .sort_values(ascending=False).head(20)
print(top.to_string())

pat_per_code = dedup.groupby(CODE)[KEY_PATIENT].nunique()
print(f"\nCodes seen in fewer than 10 patients: {int((pat_per_code < 10).sum()):,} "
      f"of {len(pat_per_code):,}  (long tail -> group by ICD chapter / comorbidity)")

# ---------------------------------------------------------------- 8. leakage check
section("8. TEMPORAL / LEAKAGE REVIEW")
print("The table has NO date column: we cannot verify that a diagnosis was known")
print("before the surgery. Confirm in the MOVER documentation what 'history' means.")
post = d[NAME].str.contains(
    r"post[- ]?op|post[- ]?procedur|complication|following (surgery|procedure)|"
    r"due to (surgery|procedure)|iatrogenic",
    case=False, regex=True, na=False)
print(f"\nRows whose name suggests a post-surgical event: {int(post.sum()):,}")
if post.sum():
    print(d.loc[post, NAME].value_counts().head(15))
    log_defect(NAME, "Diagnoses that may describe post-surgical events "
               "(possible leakage)", post.sum(), "keyword search in dx_name")

# ---------------------------------------------------------------- 9. cross-table
section("9. LINK TO patient_information (surgery table)")
if os.path.exists(SURGERY_PATH):
    surg = pd.read_csv(SURGERY_PATH, dtype="string", usecols=["LOG_ID", "MRN", "ICU_ADMIN_FLAG"])
    surg = surg.drop_duplicates()
    surg = surg.drop_duplicates(subset="LOG_ID")        # one row per surgery
    surg["MRN"] = surg["MRN"].str.strip()
    surg["icu"] = surg["ICU_ADMIN_FLAG"].str.strip().str.lower().map({"yes": 1, "no": 0})

    surg_pat = set(surg["MRN"])
    hist_pat = set(dedup[KEY_PATIENT].dropna())
    print(f"Patients in surgery table: {len(surg_pat):,}")
    print(f"Patients in history table: {len(hist_pat):,}")
    print(f"History patients NOT in surgery table (orphans): {len(hist_pat - surg_pat):,}")
    print(f"Surgery patients with NO history rows: {len(surg_pat - hist_pat):,} "
          f"({100 * len(surg_pat - hist_pat) / len(surg_pat):.1f}%)")
    if hist_pat - surg_pat:
        log_defect(KEY_PATIENT, "History patients not present in surgery table",
                   dedup[KEY_PATIENT].isin(hist_pat - surg_pat).sum(),
                   "set difference of MRNs")

    # JOIN FAN-OUT: joining history directly to surgeries multiplies rows
    naive = surg.merge(dedup, left_on="MRN", right_on=KEY_PATIENT, how="left")
    print(f"\nSurgeries: {len(surg):,} | rows after naive join with history: "
          f"{len(naive):,}  (fan-out x{len(naive) / len(surg):.1f})")
    print("=> aggregate history to ONE row per patient before joining.")

    # Does having history / more diagnoses relate to ICU admission?
    surg["n_dx"] = surg["MRN"].map(per_patient).fillna(0).astype(int)
    surg["has_history"] = surg["n_dx"] > 0
    print("\nICU rate by presence of history:")
    print(surg.groupby("has_history")["icu"].agg(["mean", "count"]).round(3))
    print("\nICU rate by number of diagnoses:")
    band = pd.cut(surg["n_dx"], [-1, 0, 2, 5, 10, 20, 50, 10_000])
    print(surg.groupby(band, observed=True)["icu"].agg(["mean", "count"]).round(3))

    # Which diagnoses are most associated with ICU admission? (min 200 surgeries)
    sd = surg[["LOG_ID", "MRN", "icu"]].merge(
        dedup[[KEY_PATIENT, CODE, NAME]], left_on="MRN", right_on=KEY_PATIENT)
    assoc = sd.groupby(NAME)["icu"].agg(["mean", "count"])
    print("\nDiagnoses with the highest ICU rate (>= 200 surgeries):")
    print(assoc[assoc["count"] >= 200].sort_values("mean", ascending=False)
          .head(10).round(3))
    print("\nDiagnoses with the lowest ICU rate (>= 200 surgeries):")
    print(assoc[assoc["count"] >= 200].sort_values("mean").head(10).round(3))
else:
    print(f"{SURGERY_PATH} not found: skipping cross-table checks.")

# ---------------------------------------------------------------- 10. defects log
section("10. DEFECTS LOG DRAFT")
log = pd.DataFrame(defects)
if len(log):
    print(log[["column", "description", "rows_affected", "pct_rows"]].to_string(index=False))
    log.to_csv("defects_log_patient_history_draft.csv", index=False)
    print("\nSaved defects_log_patient_history_draft.csv")
else:
    print("No defects logged.")
