-- =====================================================================
-- 04_mart.sql : target layer for the models
--   mart.patient_history_features : ONE row per patient (history aggregated BEFORE any join)
--   mart.model_dataset            : ONE row per surgery = features + target (icu)
-- Joining the raw history to surgeries would multiply every surgery by ~10 rows.
-- =====================================================================
DROP TABLE IF EXISTS mart.model_dataset CASCADE;
DROP TABLE IF EXISTS mart.patient_history_features CASCADE;

-- Row-level comorbidity flags (heuristic: ICD-9 prefix OR keyword in the name).
-- Keywords also cover diagnoses without a code (about a quarter of the history rows).
CREATE TEMP TABLE _hflags ON COMMIT DROP AS
SELECT
    h.mrn, h.dx_code, h.dx_name, h.icd9_chapter, h.is_acute_or_postop,
    COALESCE(h.dx_code ~ '^40[1-5]' OR (h.dx_name ~* 'hypertension|\mhtn\M'
         AND h.dx_name !~* 'pulmonary|portal|intracranial|ocular|gestational|pregnan'), false)  AS f_hypertension,
    COALESCE(h.dx_code ~ '^250' OR (h.dx_name ~* 'diabet'
         AND h.dx_name !~* 'pre-?diabet|insipidus|gestational'), false)                         AS f_diabetes,
    COALESCE(h.dx_code ~ '^41[0-4]' OR h.dx_name ~* 'coronary artery|\mCAD\M|myocardial infarction|angina', false) AS f_cad,
    COALESCE(h.dx_code ~ '^428' OR h.dx_name ~* 'heart failure|\mCHF\M|cardiomyopathy', false)  AS f_heart_failure,
    COALESCE(h.dx_code ~ '^427' OR h.dx_name ~* 'atrial fibrillation|arrhythmia|flutter', false) AS f_arrhythmia,
    COALESCE(h.dx_code ~ '^49[0-6]' OR h.dx_name ~* 'copd|chronic obstructive|emphysema|asthma', false) AS f_chronic_lung,
    COALESCE(h.dx_code ~ '^585' OR h.dx_name ~* 'chronic kidney|\mCKD\M|\mESRD\M|end[- ]stage renal|dialysis', false) AS f_ckd,
    COALESCE(h.dx_code ~ '^57[0-3]' OR h.dx_name ~* 'cirrhosis|hepatitis|liver disease|fatty liver', false) AS f_liver,
    COALESCE(h.dx_code ~ '^(1[4-9][0-9]|20[0-8])' OR h.dx_name ~* 'cancer|carcinoma|malignan|lymphoma|leukemia|melanoma|metasta', false) AS f_cancer,
    COALESCE(h.dx_code ~ '^(327\.23|780\.57)' OR h.dx_name ~* 'sleep apnea|\mOSA\M', false)     AS f_sleep_apnea,
    COALESCE(h.dx_code ~ '^278\.0' OR h.dx_name ~* 'obes', false)                               AS f_obesity,
    COALESCE(h.dx_code ~ '^43[3-8]' OR h.dx_name ~* 'stroke|\mCVA\M|\mTIA\M|cerebrovascular|cerebral infarction', false) AS f_cerebrovascular,
    COALESCE(h.dx_code ~ '^(290|331\.0)' OR h.dx_name ~* 'dementia|alzheimer', false)           AS f_dementia,
    COALESCE(h.dx_code ~ '^(296\.[23]|300\.0|311)' OR h.dx_name ~* 'depress|anxiety', false)    AS f_mood_anxiety,
    COALESCE(h.dx_code ~ '^244' OR h.dx_name ~* 'hypothyroid', false)                           AS f_hypothyroid,
    COALESCE(h.dx_code ~ '^28[0-5]' OR h.dx_name ~* 'anemia', false)                            AS f_anemia
FROM clean.history h;

CREATE TABLE mart.patient_history_features AS
SELECT f.*,
       (f.comorb_hypertension + f.comorb_diabetes + f.comorb_cad + f.comorb_heart_failure
        + f.comorb_arrhythmia + f.comorb_chronic_lung + f.comorb_ckd + f.comorb_liver
        + f.comorb_cancer + f.comorb_sleep_apnea + f.comorb_obesity + f.comorb_cerebrovascular
        + f.comorb_dementia + f.comorb_mood_anxiety + f.comorb_hypothyroid + f.comorb_anemia
       )::int AS n_comorb
FROM (
    SELECT
        mrn,
        -- counts use only chronic / preoperative-looking diagnoses
        count(DISTINCT COALESCE(dx_code, 'N:' || upper(dx_name))) FILTER (WHERE NOT is_acute_or_postop)  AS n_dx_chronic,
        count(DISTINCT dx_code) FILTER (WHERE NOT is_acute_or_postop AND dx_code IS NOT NULL)            AS n_dx_coded,
        count(DISTINCT upper(dx_name)) FILTER (WHERE NOT is_acute_or_postop AND dx_code IS NULL)         AS n_dx_uncoded,
        count(DISTINCT COALESCE(dx_code, 'N:' || upper(dx_name))) FILTER (WHERE is_acute_or_postop)      AS n_dx_acute_postop,  -- probe only, NOT a feature
        count(DISTINCT dx_code) FILTER (WHERE NOT is_acute_or_postop AND icd9_chapter = 'neoplasms')        AS n_dx_neoplasms,
        count(DISTINCT dx_code) FILTER (WHERE NOT is_acute_or_postop AND icd9_chapter = 'endocrine')        AS n_dx_endocrine,
        count(DISTINCT dx_code) FILTER (WHERE NOT is_acute_or_postop AND icd9_chapter = 'blood')            AS n_dx_blood,
        count(DISTINCT dx_code) FILTER (WHERE NOT is_acute_or_postop AND icd9_chapter = 'mental')           AS n_dx_mental,
        count(DISTINCT dx_code) FILTER (WHERE NOT is_acute_or_postop AND icd9_chapter = 'nervous')          AS n_dx_nervous,
        count(DISTINCT dx_code) FILTER (WHERE NOT is_acute_or_postop AND icd9_chapter = 'circulatory')      AS n_dx_circulatory,
        count(DISTINCT dx_code) FILTER (WHERE NOT is_acute_or_postop AND icd9_chapter = 'respiratory')      AS n_dx_respiratory,
        count(DISTINCT dx_code) FILTER (WHERE NOT is_acute_or_postop AND icd9_chapter = 'digestive')        AS n_dx_digestive,
        count(DISTINCT dx_code) FILTER (WHERE NOT is_acute_or_postop AND icd9_chapter = 'genitourinary')    AS n_dx_genitourinary,
        count(DISTINCT dx_code) FILTER (WHERE NOT is_acute_or_postop AND icd9_chapter = 'musculoskeletal')  AS n_dx_musculoskeletal,
        COALESCE((max(f_hypertension::int)    FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_hypertension,
        COALESCE((max(f_diabetes::int)        FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_diabetes,
        COALESCE((max(f_cad::int)             FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_cad,
        COALESCE((max(f_heart_failure::int)   FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_heart_failure,
        COALESCE((max(f_arrhythmia::int)      FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_arrhythmia,
        COALESCE((max(f_chronic_lung::int)    FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_chronic_lung,
        COALESCE((max(f_ckd::int)             FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_ckd,
        COALESCE((max(f_liver::int)           FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_liver,
        COALESCE((max(f_cancer::int)          FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_cancer,
        COALESCE((max(f_sleep_apnea::int)     FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_sleep_apnea,
        COALESCE((max(f_obesity::int)         FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_obesity,
        COALESCE((max(f_cerebrovascular::int) FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_cerebrovascular,
        COALESCE((max(f_dementia::int)        FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_dementia,
        COALESCE((max(f_mood_anxiety::int)    FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_mood_anxiety,
        COALESCE((max(f_hypothyroid::int)     FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_hypothyroid,
        COALESCE((max(f_anemia::int)          FILTER (WHERE NOT is_acute_or_postop)), 0) AS comorb_anemia
    FROM _hflags
    GROUP BY mrn
) f;

ALTER TABLE mart.patient_history_features ADD PRIMARY KEY (mrn);

-- Model dataset: one row per surgery. Only information available BEFORE surgery
-- (discharge, length of stay, OR-out and anaesthesia-stop times are deliberately absent).
CREATE TABLE mart.model_dataset AS
SELECT
    s.log_id,
    s.mrn,
    s.surgery_date,
    extract(year  FROM s.surgery_date)::smallint AS surgery_year,
    extract(month FROM s.surgery_date)::smallint AS surgery_month,
    extract(isodow FROM s.surgery_date)::smallint AS surgery_dow,
    s.icu_admission                               AS icu,            -- TARGET
    s.is_elective_proxy,
    s.age_years, s.sex, s.height_cm, s.weight_kg, s.bmi,
    CASE WHEN s.height_cm IS NULL
     THEN 1 ELSE 0
END::smallint AS height_missing,

CASE WHEN s.weight_kg IS NULL
     THEN 1 ELSE 0
END::smallint AS weight_missing,

s.asa_class,

CASE WHEN s.asa_class IS NULL
     THEN 1 ELSE 0
END::smallint AS asa_missing,

s.anes_type,

CASE WHEN s.anes_type IS NULL
     THEN 1 ELSE 0
END::smallint AS anes_missing,

s.patient_class_group,

CASE
    WHEN s.hosp_admsn_time IS NOT NULL
         AND s.hosp_admsn_time::date <= s.surgery_date
    THEN s.surgery_date - s.hosp_admsn_time::date
END AS preop_days,
COALESCE(NOT (s.hosp_admsn_time IS NOT NULL AND s.hosp_admsn_time::date <= s.surgery_date),
             true)::int                           AS preop_days_missing,

s.procedure_nm,

CASE WHEN h.mrn IS NOT NULL
     THEN 1 ELSE 0
END::smallint AS has_history,
    COALESCE(h.n_dx_chronic, 0)        AS n_dx_chronic,
    COALESCE(h.n_dx_coded, 0)          AS n_dx_coded,
    COALESCE(h.n_dx_uncoded, 0)        AS n_dx_uncoded,
    COALESCE(h.n_dx_acute_postop, 0)   AS n_dx_acute_postop,        -- leakage probe, not a feature
    COALESCE(h.n_dx_neoplasms, 0)      AS n_dx_neoplasms,
    COALESCE(h.n_dx_endocrine, 0)      AS n_dx_endocrine,
    COALESCE(h.n_dx_blood, 0)          AS n_dx_blood,
    COALESCE(h.n_dx_mental, 0)         AS n_dx_mental,
    COALESCE(h.n_dx_nervous, 0)        AS n_dx_nervous,
    COALESCE(h.n_dx_circulatory, 0)    AS n_dx_circulatory,
    COALESCE(h.n_dx_respiratory, 0)    AS n_dx_respiratory,
    COALESCE(h.n_dx_digestive, 0)      AS n_dx_digestive,
    COALESCE(h.n_dx_genitourinary, 0)  AS n_dx_genitourinary,
    COALESCE(h.n_dx_musculoskeletal, 0) AS n_dx_musculoskeletal,
    COALESCE(h.comorb_hypertension, 0)    AS comorb_hypertension,
    COALESCE(h.comorb_diabetes, 0)        AS comorb_diabetes,
    COALESCE(h.comorb_cad, 0)             AS comorb_cad,
    COALESCE(h.comorb_heart_failure, 0)   AS comorb_heart_failure,
    COALESCE(h.comorb_arrhythmia, 0)      AS comorb_arrhythmia,
    COALESCE(h.comorb_chronic_lung, 0)    AS comorb_chronic_lung,
    COALESCE(h.comorb_ckd, 0)             AS comorb_ckd,
    COALESCE(h.comorb_liver, 0)           AS comorb_liver,
    COALESCE(h.comorb_cancer, 0)          AS comorb_cancer,
    COALESCE(h.comorb_sleep_apnea, 0)     AS comorb_sleep_apnea,
    COALESCE(h.comorb_obesity, 0)         AS comorb_obesity,
    COALESCE(h.comorb_cerebrovascular, 0) AS comorb_cerebrovascular,
    COALESCE(h.comorb_dementia, 0)        AS comorb_dementia,
    COALESCE(h.comorb_mood_anxiety, 0)    AS comorb_mood_anxiety,
    COALESCE(h.comorb_hypothyroid, 0)     AS comorb_hypothyroid,
    COALESCE(h.comorb_anemia, 0)          AS comorb_anemia,
    COALESCE(h.n_comorb, 0)               AS n_comorb
FROM clean.surgery s
LEFT JOIN mart.patient_history_features h ON h.mrn = s.mrn
WHERE s.icu_admission IS NOT NULL;

ALTER TABLE mart.model_dataset ADD PRIMARY KEY (log_id);
CREATE INDEX mart_model_dataset_date_idx ON mart.model_dataset (surgery_date);
CREATE INDEX mart_model_dataset_mrn_idx  ON mart.model_dataset (mrn);

COMMENT ON TABLE mart.model_dataset IS
    'One row per surgery. icu = target. n_dx_acute_postop is a leakage probe and must not be used as a feature.';

INSERT INTO etl.rule_log (rule_id, description, rows_affected)
SELECT 'M01', 'rows in mart.model_dataset', count(*) FROM mart.model_dataset
UNION ALL
SELECT 'M02', 'surgeries without any history row', count(*) FROM mart.model_dataset WHERE has_history = 0;

-- Read-only access for models and dashboard (skipped if the role does not exist)
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'mover_reader') THEN
        GRANT SELECT ON ALL TABLES IN SCHEMA mart TO mover_reader;
    END IF;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'grant to mover_reader skipped (insufficient privilege)';
END $$;
