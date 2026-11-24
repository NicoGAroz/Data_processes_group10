#!/usr/bin/env python3
"""
Creates SYNTHETIC files with the same columns and the same quirks as the MOVER tables
(height as feet/inches text, weight in ounces, age in BIRTH_DATE, duplicates, conflicting
LOG_IDs, trailing spaces, placeholder codes, history repeated per surgery, no history dates).
Use it to dry-run the whole pipeline before touching real data. NOT real patients.

  python tools/make_synthetic_data.py --n 6000 --out synthetic
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

p = argparse.ArgumentParser()
p.add_argument("--n", type=int, default=6000, help="number of surgeries")
p.add_argument("--out", default="synthetic")
p.add_argument("--seed", type=int, default=7)
a = p.parse_args()
rng = np.random.default_rng(a.seed)
out = Path(a.out)
out.mkdir(exist_ok=True)


def hex_ids(n, k=16):
    return ["".join(rng.choice(list("0123456789abcdef"), k)) for _ in range(n)]


n = a.n
n_pat = int(n * 0.62)
patients = hex_ids(n_pat)
pat_of_surgery = rng.choice(patients, n)
log_ids = hex_ids(n)

# --- procedures with different ICU risk
procs = [f"PROCEDURE {i:02d}" for i in range(60)]
proc_p = rng.dirichlet(np.ones(60) * 0.6)
proc_base = rng.uniform(0.03, 0.95, 60)
proc_idx = rng.choice(60, n, p=proc_p)
procedure = np.array(procs)[proc_idx]

dates = pd.to_datetime("2019-01-01") + pd.to_timedelta(rng.integers(0, 365 * 4 + 220, n), unit="D")
inpatient = rng.random(n) < (0.2 + 0.7 * proc_base[proc_idx])
asa_labels = ["Healthy", "Mild Systemic Disease", "Severe Systemic Disease",
              "Incapacitating Disease", "Moribund", "Brain Dead"]
asa_i = rng.choice(6, n, p=[0.05, 0.28, 0.46, 0.19, 0.015, 0.005])
logit = (np.log(proc_base[proc_idx] / (1 - proc_base[proc_idx])) + 1.3 * inpatient
         + 0.45 * (asa_i - 2) + rng.normal(0, 0.8, n) - 0.9)
icu = rng.random(n) < 1 / (1 + np.exp(-logit))

age = rng.integers(18, 91, n)
kg = np.clip(rng.normal(78, 18, n), 30, 200)
ft_in = np.round(rng.normal(66.5, 4, n)).astype(int)
height = np.array([f"{h // 12}' {h % 12}" for h in ft_in], dtype=object)
height[rng.random(n) < 0.20] = None
anes = rng.choice(["General", "Monitored Anesthesia Care (MAC)", "Regional", "Spinal",
                   "Moderate Sedation - by non-anesthesia staff only", "Choice Per Patient on Day of Surgery"],
                  n, p=[0.8, 0.08, 0.05, 0.02, 0.04, 0.01]).astype(object)
anes = np.where(anes == "Monitored Anesthesia Care (MAC)",
                np.where(rng.random(n) < 0.9, "Monitored Anesthesia Care (MAC) ", anes), anes)
anes[rng.random(n) < 0.01] = None

adm_days = np.where(inpatient, rng.integers(0, 4, n), 0)
in_or = dates + pd.to_timedelta(rng.integers(7 * 60, 16 * 60, n), unit="m")
out_or = in_or + pd.to_timedelta(rng.integers(40, 300, n), unit="m")
admsn = (dates - pd.to_timedelta(adm_days, unit="D")) + pd.to_timedelta(rng.integers(0, 6 * 60, n), unit="m")
los = np.where(inpatient, rng.integers(1, 30, n) + 8 * icu, 0)
disch = out_or + pd.to_timedelta(los, unit="D")
fmt = "%Y-%m-%d %H:%M:%S"

df = pd.DataFrame({
    "LOG_ID": log_ids, "MRN": pat_of_surgery,
    "DISCH_DISP_C": np.where(rng.random(n) < 0.7, 15.0, 69.0),
    "DISCH_DISP": np.where(rng.random(n) < 0.7, "Home Routine", "Skilled Nursing Facility"),
    "HOSP_ADMSN_TIME": admsn.strftime(fmt), "HOSP_DISCH_TIME": disch.strftime(fmt), "LOS": los.astype(float),
    "ICU_ADMIN_FLAG": np.where(icu, "Yes", "No"), "SURGERY_DATE": dates.strftime("%Y-%m-%d"),
    "BIRTH_DATE": age, "HEIGHT": height, "WEIGHT": np.round(kg * 35.274, 2),
    "SEX": rng.choice(["Male", "Female"], n), "PRIMARY_ANES_TYPE_NM": anes,
    "ASA_RATING_C": (asa_i + 1).astype(float), "ASA_RATING": np.array(asa_labels)[asa_i],
    "PATIENT_CLASS_GROUP": np.where(inpatient, "Inpatient", "Outpatient"),
    "PATIENT_CLASS_NM": np.where(inpatient, "Inpatient Admission", "Hospital Outpatient Surgery"),
    "PRIMARY_PROCEDURE_NM": procedure,
    "IN_OR_DTTM": in_or.strftime(fmt), "OUT_OR_DTTM": out_or.strftime(fmt),
    "AN_START_DATETIME": (in_or - pd.Timedelta(minutes=20)).strftime(fmt),
    "AN_STOP_DATETIME": (out_or + pd.Timedelta(minutes=5)).strftime(fmt),
}).astype(object)

# missing values (informative: missing ASA / OR times more common in outpatient cases)
for col, p_miss in [("ASA_RATING", 0.10), ("IN_OR_DTTM", 0.10), ("OUT_OR_DTTM", 0.10),
                    ("AN_START_DATETIME", 0.11), ("AN_STOP_DATETIME", 0.11), ("WEIGHT", 0.04)]:
    m = rng.random(n) < p_miss
    df.loc[m, col] = None
    if col == "ASA_RATING":
        df.loc[m, "ASA_RATING_C"] = None

# --- planted defects
df.loc[3, "SEX"] = "Unknown"
df.loc[5, "HEIGHT"] = "3' 0"                      # implausible height
df.loc[6, "HEIGHT"] = "tall"                      # not parseable
df.loc[7, "WEIGHT"] = 90000.0                     # implausible weight
df.loc[9, "OUT_OR_DTTM"] = df.loc[9, "IN_OR_DTTM"]  # order issue candidates are random anyway
conflict = df.iloc[10:15].copy()
conflict["ASA_RATING"] = None
conflict["ASA_RATING_C"] = None
two_pat = df.iloc[20:22].copy()
two_pat["MRN"] = hex_ids(2)
df = pd.concat([df, df.sample(frac=0.02, random_state=1), conflict, two_pat], ignore_index=True)
df = df.sample(frac=1, random_state=2).reset_index(drop=True)
df.to_csv(out / "patient_information.csv", index=False)

# --- history: repeated once per surgery, no dates, acute diagnoses only for ICU patients (leak!)
chronic = [("401.9", "Essential hypertension"), ("401.9", "HTN (hypertension)"), ("250.00", "Diabetes mellitus (CMS-HCC)"),
           ("272.4", "Hyperlipidemia"), ("530.81", "GERD (gastroesophageal reflux disease)"),
           ("427.31", "Paroxysmal atrial fibrillation (CMS-HCC)"), ("585.6", "ESRD (end stage renal disease) (CMS-HCC)"),
           ("414.00", "CAD (coronary artery disease)"), ("327.23", "OSA (obstructive sleep apnea)"),
           ("244.9", "Hypothyroidism"), ("285.9", "Anemia"), ("V70.0", "Routine general medical examination"),
           ("592.0", "Nephrolithiasis"), ("IMO0001", "Unspecified problem"), ("790.29", "Prediabetes"),
           ("496", "COPD (chronic obstructive pulmonary disease)"), ("188.9", "Bladder cancer"),
           ("311", "Depression"), ("278.00", "Obesity")]
uncoded_names = ["Hypertension, unspecified type", "Type 2 diabetes mellitus without complication (CMS-HCC)",
                 "Anemia, unspecified type", "COVID-19", "Chest pain, unspecified type"]
acute = [("785.51", "Cardiogenic shock (CMS-HCC)"), ("038.9", "Sepsis (CMS-HCC)"), ("V45.89", "S/P craniotomy"),
         ("584.9", "AKI (acute kidney injury) (CMS-HCC)")]

patient_icu = pd.Series(icu, index=pat_of_surgery).groupby(level=0).max()
n_surg_of = pd.Series(pat_of_surgery).value_counts()
rows = []
for m_ in patients:
    if rng.random() > 0.97:
        continue
    k = int(rng.integers(1, 14))
    chosen = [chronic[i] for i in rng.choice(len(chronic), k)]
    entries = []
    for code, name in chosen:
        if rng.random() < 0.25:
            entries.append(("", uncoded_names[int(rng.integers(len(uncoded_names)))]))
        else:
            entries.append((code, name))
    if patient_icu.get(m_, False) and rng.random() < 0.25:
        entries.append(acute[int(rng.integers(len(acute)))])
    for _ in range(int(n_surg_of.get(m_, 1))):
        for code, name in entries:
            rows.append((m_, code, name))
hist = pd.DataFrame(rows, columns=["mrn", "diagnosis_code", "dx_name"])
hist.loc[hist.sample(5, random_state=3).index, "dx_name"] = hist["dx_name"] + " "   # trailing space
hist.to_csv(out / "patient_history.csv", index=False)
print(f"patient_information.csv: {len(df):,} rows | patient_history.csv: {len(hist):,} rows | ICU rate {icu.mean():.1%}")
