"""
Follow-up checks for patient_history (mrn, diagnosis_code, dx_name).
Fixes the problems of the first script (missing codes treated as one value,
name counts distorted by deduplicating on code only, too broad leakage regex)
and tests the hypotheses raised by its output.
Standalone: loads both files itself.
"""
import pandas as pd
import numpy as np

HISTORY_PATH = "patient_history.csv"
SURGERY_PATH = "patient_information.csv"


def section(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ---------------------------------------------------------------- load
h = pd.read_csv(HISTORY_PATH, dtype="string")
h.columns = h.columns.str.strip().str.lower()
for c in h.columns:
    h[c] = h[c].str.strip()
h["diagnosis_code"] = h["diagnosis_code"].str.upper()

raw_s = pd.read_csv(SURGERY_PATH, dtype="string",
                    usecols=["LOG_ID", "MRN", "ICU_ADMIN_FLAG", "PATIENT_CLASS_GROUP"])
raw_s = raw_s.drop_duplicates()
s = raw_s.drop_duplicates(subset="LOG_ID").copy()          # one row per surgery
s["icu"] = (s["ICU_ADMIN_FLAG"].str.strip().str.lower()
            .map({"yes": 1, "no": 0}).astype(float))
n_surg = s.groupby("MRN")["LOG_ID"].nunique()               # surgeries per patient

# ---------------------------------------------------------------- 0. surgery-table hint
section("0. CONFLICTING LOG_IDs IN THE SURGERY TABLE")
mrn_per_log = raw_s.groupby("LOG_ID")["MRN"].nunique()
print("LOG_IDs that appear with more than one MRN:", int((mrn_per_log > 1).sum()))
print("Distinct MRNs before / after keeping one row per LOG_ID:",
      raw_s["MRN"].nunique(), "/", s["MRN"].nunique())

# ---------------------------------------------------------------- 1. duplicates
section("1. DUPLICATES")
u = h.drop_duplicates()
print(f"Rows: {len(h):,} | exact duplicates: {len(h) - len(u):,} "
      f"({100 * (len(h) - len(u)) / len(h):.1f}%) | unique rows: {len(u):,}")

# Hypothesis: the history is repeated once per surgery/visit of the patient
copies = h.groupby(["mrn", "dx_name"]).size().rename("copies").reset_index()
m = copies.merge(n_surg.rename("n_surgeries"), left_on="mrn", right_index=True)
print("\nCopies per (mrn, dx_name):")
print(m["copies"].describe().round(2))
print("\nShare of (mrn, dx_name) with copies == number of surgeries of the patient:",
      round(float((m["copies"] == m["n_surgeries"]).mean()), 3))
print("\nRow-wise share: copies (columns) by surgeries of the patient (rows), capped at 6:")
print(pd.crosstab(m["n_surgeries"].clip(upper=6), m["copies"].clip(upper=6),
                  normalize="index").round(2))

# ---------------------------------------------------------------- 2. coded vs uncoded
section("2. CODED VS UNCODED ROWS (after removing exact duplicates)")
coded = u[u["diagnosis_code"].notna()]
unc = u[u["diagnosis_code"].isna()]
print(f"Coded rows: {len(coded):,} | uncoded rows: {len(unc):,} "
      f"({100 * len(unc) / len(u):.1f}%)")

has_code = u.groupby("mrn")["diagnosis_code"].apply(lambda x: x.notna().any())
print(f"Patients with no coded diagnosis at all: {int((~has_code).sum()):,} "
      f"of {len(has_code):,}")

# Can the missing codes be back-filled from the name?
name_info = coded.groupby("dx_name")["diagnosis_code"].agg(n_codes="nunique", code="first")
fill_n = unc["dx_name"].map(name_info["n_codes"])
can_fill = fill_n.eq(1)
never = ~unc["dx_name"].isin(name_info.index)
print(f"\nUncoded rows whose name has exactly ONE code elsewhere (back-fillable): "
      f"{int(can_fill.sum()):,} ({100 * can_fill.mean():.1f}%)")
print(f"Uncoded rows whose name never appears with a code: "
      f"{int(never.sum()):,} ({100 * never.mean():.1f}%)")
print("\nMost frequent uncoded names that cannot be back-filled:")
print(unc.loc[~can_fill, "dx_name"].value_counts().head(20).to_string())

# ---------------------------------------------------------------- 3. corrected counts
section("3. DIAGNOSES PER PATIENT AND TOP CODES (coded rows only)")
cu = coded.drop_duplicates(subset=["mrn", "diagnosis_code"]).copy()
per_pat = cu.groupby("mrn").size()
print("Distinct codes per patient:")
print(per_pat.describe().round(2))
rep_name = coded.groupby("diagnosis_code")["dx_name"].agg(lambda x: x.value_counts().index[0])
top = cu.groupby("diagnosis_code")["mrn"].nunique().sort_values(ascending=False).head(20)
print("\nTop 20 codes by number of patients (with the most frequent name):")
print(pd.DataFrame({"patients": top, "example_name": rep_name.reindex(top.index)}).to_string())

# ---------------------------------------------------------------- 4. ICD-9 chapters
section("4. ICD-9 CHAPTERS AND ICU RATE")
BOUNDS = [(1, 139, "001-139 infectious"), (140, 239, "140-239 neoplasms"),
          (240, 279, "240-279 endocrine/metabolic"), (280, 289, "280-289 blood"),
          (290, 319, "290-319 mental"), (320, 389, "320-389 nervous/sense"),
          (390, 459, "390-459 circulatory"), (460, 519, "460-519 respiratory"),
          (520, 579, "520-579 digestive"), (580, 629, "580-629 genitourinary"),
          (630, 679, "630-679 pregnancy"), (680, 709, "680-709 skin"),
          (710, 739, "710-739 musculoskeletal"), (740, 759, "740-759 congenital"),
          (760, 779, "760-779 perinatal"), (780, 799, "780-799 symptoms"),
          (800, 999, "800-999 injury/poisoning")]


def icd9_chapter(code):
    if pd.isna(code):
        return "uncoded"
    c = str(code).upper()
    if c.startswith("V"):
        return "V supplementary"
    if c.startswith("E"):
        return "E external causes"
    if "." not in c and len(c) > 3:
        return "other/placeholder"
    try:
        n = int(c[:3])
    except ValueError:
        return "other/placeholder"
    for lo, hi, name in BOUNDS:
        if lo <= n <= hi:
            return name
    return "other/placeholder"


cu["chapter"] = cu["diagnosis_code"].map(icd9_chapter)
print(cu["chapter"].value_counts().to_string())

flags = pd.crosstab(cu["mrn"], cu["chapter"]).clip(upper=1)
rows = []
for ch in flags.columns:
    has = s["MRN"].map(flags[ch]).fillna(0).astype(int) == 1
    rows.append((ch, int(has.sum()), s.loc[has, "icu"].mean(), s.loc[~has, "icu"].mean()))
print("\nICU rate for surgeries of patients with / without a diagnosis in each chapter:")
print(pd.DataFrame(rows, columns=["chapter", "surgeries_with", "icu_with", "icu_without"])
      .round(3).to_string(index=False))

# ---------------------------------------------------------------- 5. patients without history
section("5. SURGERIES WITHOUT HISTORY")
s["has_history"] = s["MRN"].isin(set(h["mrn"]))
print("Share of each patient class among surgeries with / without history:")
print(pd.crosstab(s["PATIENT_CLASS_GROUP"], s["has_history"], normalize="columns").round(3))
print("\nICU rate by patient class and presence of history:")
print(s.groupby(["PATIENT_CLASS_GROUP", "has_history"])["icu"]
      .agg(["mean", "count"]).round(3))

s["n_codes"] = s["MRN"].map(per_pat).fillna(0)
s["n_surgeries_patient"] = s["MRN"].map(n_surg)
print("\nSpearman correlation between history length and surgeries per patient:")
print(s[["n_codes", "n_surgeries_patient"]].corr(method="spearman").round(2))
print("\nICU rate by number of distinct coded diagnoses (corrected count):")
band = pd.cut(s["n_codes"], [-1, 0, 2, 5, 10, 20, 50, 10_000])
print(s.groupby(band, observed=True)["icu"].agg(["mean", "count"]).round(3))

# ---------------------------------------------------------------- 6. leakage / acute conditions
section("6. POST-SURGICAL AND ACUTE DIAGNOSES")
pat = (r"post[- ]?op|post[- ]?procedur|\bs/p\b|status post|iatrogenic|"
       r"(?<!without )(?<!with )complication")
flag = u["dx_name"].str.contains(pat, case=False, regex=True, na=False)
print(f"Unique rows with a post-surgical-looking name (refined regex): {int(flag.sum()):,}")
print(u.loc[flag, "dx_name"].value_counts().head(20).to_string())
post_pat = set(u.loc[flag, "mrn"])
s["has_post_dx"] = s["MRN"].isin(post_pat)
print("\nICU rate for surgeries of patients with / without such a diagnosis:")
print(s.groupby("has_post_dx")["icu"].agg(["mean", "count"]).round(3))

sc = s[["LOG_ID", "MRN", "icu"]].merge(cu[["mrn", "diagnosis_code"]],
                                       left_on="MRN", right_on="mrn")
assoc = sc.groupby("diagnosis_code")["icu"].agg(["mean", "count"])
assoc["example_name"] = rep_name.reindex(assoc.index)
big = assoc[assoc["count"] >= 200]
print("\nCodes with the highest ICU rate (>= 200 surgeries):")
print(big.sort_values("mean", ascending=False).head(15).round(3).to_string())
print("\nCodes with the lowest ICU rate (>= 200 surgeries):")
print(big.sort_values("mean").head(10).round(3).to_string())


sd = pd.read_csv(SURGERY_PATH, dtype="string", usecols=["MRN", "SURGERY_DATE"])
sd["year"] = pd.to_datetime(sd["SURGERY_DATE"], errors="coerce").dt.year
last_year = sd.groupby("MRN")["year"].max()
u = u.assign(uncoded=u["diagnosis_code"].isna())
share = u.groupby("mrn")["uncoded"].mean()
print(share.groupby(share.index.map(last_year)).mean().round(3))