-- =====================================================================
-- 02_clean_surgery.sql : raw.patient_information -> clean.surgery
-- Grain: one row per surgery (log_id). Every rule logs its row count to etl.rule_log.
-- Rebuilds from the latest load of the raw table, so it can be re-run at any time.
-- =====================================================================
DROP TABLE IF EXISTS clean.surgery CASCADE;

-- S00 latest raw load only
CREATE TEMP TABLE _raw ON COMMIT DROP AS
SELECT * FROM raw.patient_information
WHERE _load_id = (SELECT max(_load_id) FROM raw.patient_information);

INSERT INTO etl.rule_log (rule_id, description, rows_affected)
SELECT 'S00', 'rows in latest raw load', count(*) FROM _raw;

-- S01 normalise text: trim, collapse spaces, empty -> NULL, lower-case flag, upper-case procedure,
--     'Unknown' sex -> NULL, placeholder anaesthesia type -> NULL, one spelling for discharge site
CREATE TEMP TABLE _norm ON COMMIT DROP AS
SELECT
    etl.clean_text(log_id)                                                    AS log_id,
    etl.clean_text(mrn)                                                       AS mrn,
    disch_disp_c,
    replace(etl.clean_text(disch_disp), 'Alternative Care', 'Alternate Care') AS disch_disp,
    hosp_admsn_time, hosp_disch_time, los,
    lower(etl.clean_text(icu_admin_flag))                                     AS icu_admin_flag,
    surgery_date, birth_date,
    etl.clean_text(height)                                                    AS height,
    weight,
    CASE WHEN lower(etl.clean_text(sex)) IN ('male', 'female')
         THEN initcap(etl.clean_text(sex)) END                                AS sex,
    CASE WHEN starts_with(lower(etl.clean_text(primary_anes_type_nm)), 'choice per patient')
         THEN NULL ELSE etl.clean_text(primary_anes_type_nm) END              AS primary_anes_type_nm,
    asa_rating_c,
    etl.clean_text(asa_rating)                                                AS asa_rating,
    etl.clean_text(patient_class_group)                                       AS patient_class_group,
    etl.clean_text(patient_class_nm)                                          AS patient_class_nm,
    upper(etl.clean_text(primary_procedure_nm))                               AS primary_procedure_nm,
    in_or_dttm, out_or_dttm, an_start_datetime, an_stop_datetime
FROM _raw;

-- S02 exact duplicates (after normalisation, so whitespace-only differences collapse too)
CREATE TEMP TABLE _dedup ON COMMIT DROP AS
SELECT DISTINCT * FROM _norm;

INSERT INTO etl.rule_log (rule_id, description, rows_affected)
SELECT 'S02', 'exact duplicate rows removed',
       (SELECT count(*) FROM _norm) - (SELECT count(*) FROM _dedup);

-- S03 a log_id that belongs to more than one patient cannot be trusted: drop all its versions
CREATE TEMP TABLE _multi ON COMMIT DROP AS
SELECT log_id FROM _dedup
WHERE log_id IS NOT NULL
GROUP BY log_id HAVING count(DISTINCT mrn) > 1;

INSERT INTO etl.rule_log (rule_id, description, rows_affected)
SELECT 'S03', 'log_ids linked to more than one patient (all versions excluded)', count(*) FROM _multi;

-- S04 several different versions of the same log_id: keep the most complete one
--     (ties broken deterministically by a hash of the row)
CREATE TEMP TABLE _kept ON COMMIT DROP AS
SELECT * FROM (
    SELECT d.*,
           row_number() OVER (
               PARTITION BY d.log_id
               ORDER BY num_nulls(d.mrn, d.disch_disp_c, d.disch_disp, d.hosp_admsn_time,
                                  d.hosp_disch_time, d.los, d.icu_admin_flag, d.surgery_date,
                                  d.birth_date, d.height, d.weight, d.sex, d.primary_anes_type_nm,
                                  d.asa_rating_c, d.asa_rating, d.patient_class_group,
                                  d.patient_class_nm, d.primary_procedure_nm, d.in_or_dttm,
                                  d.out_or_dttm, d.an_start_datetime, d.an_stop_datetime),
                        md5(d::text)
           ) AS rn
    FROM _dedup d
    WHERE d.log_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM _multi m WHERE m.log_id = d.log_id)
) x
WHERE rn = 1;

INSERT INTO etl.rule_log (rule_id, description, rows_affected)
SELECT 'S04', 'conflicting versions of a log_id dropped (most complete kept)',
       (SELECT count(*) FROM _dedup d
         WHERE d.log_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM _multi m WHERE m.log_id = d.log_id))
       - (SELECT count(*) FROM _kept);

-- S05 height: text '5'' 6' (feet, inches) -> centimetres
--     weight: raw weight is in ounces -> kilograms 
CREATE TEMP TABLE _parsed ON COMMIT DROP AS
SELECT k.*,
       CASE WHEN hp.parts IS NOT NULL
            THEN ((hp.parts)[1]::numeric * 12 + (hp.parts)[2]::numeric) * 2.54 END AS height_cm_raw,
       k.weight * 0.0283495                                                        AS weight_kg_raw
FROM _kept k
LEFT JOIN LATERAL (SELECT regexp_match(k.height, '^(\d+)\s*''\s*(\d+)') AS parts) hp ON true;

-- S06 plausibility limits: outside the range -> NULL (the value is not trusted, the row is kept)
CREATE TEMP TABLE _typed ON COMMIT DROP AS
SELECT p.*,
       CASE WHEN p.height_cm_raw BETWEEN 120 AND 230 THEN round(p.height_cm_raw, 1) END AS height_cm,
       CASE WHEN p.weight_kg_raw BETWEEN 20 AND 350  THEN round(p.weight_kg_raw, 1) END AS weight_kg
FROM _parsed p;

INSERT INTO etl.rule_log (rule_id, description, rows_affected)
SELECT 'S05', 'height text not parseable (set to NULL)',
       count(*) FILTER (WHERE height IS NOT NULL AND height_cm_raw IS NULL) FROM _typed
UNION ALL
SELECT 'S06a', 'height outside 120-230 cm (set to NULL)',
       count(*) FILTER (WHERE height_cm_raw IS NOT NULL AND height_cm IS NULL) FROM _typed
UNION ALL
SELECT 'S06b', 'weight outside 20-350 kg (set to NULL)',
       count(*) FILTER (WHERE weight_kg_raw IS NOT NULL AND weight_kg IS NULL) FROM _typed
UNION ALL
SELECT 'S06c', 'bmi outside 10-80 (set to NULL)',
       count(*) FILTER (WHERE height_cm IS NOT NULL AND weight_kg IS NOT NULL
                          AND NOT (weight_kg / ((height_cm / 100.0) ^ 2) BETWEEN 10 AND 80))
FROM _typed;

-- S07 final typed table: ICU label, age (renamed from birth_date), BMI, ordinal ASA class,
--     data-quality flags, elective proxy
CREATE TABLE clean.surgery AS
SELECT
    t.log_id,
    t.mrn,
    t.surgery_date,
    (CASE t.icu_admin_flag WHEN 'yes' THEN 1 WHEN 'no' THEN 0 END)::smallint        AS icu_admission,
    (CASE WHEN t.birth_date BETWEEN 0 AND 120 THEN t.birth_date END)::smallint      AS age_years,
    COALESCE(t.birth_date >= 90, false)                                             AS age_top_coded,
    t.sex,
    t.height_cm,
    t.weight_kg,
    CASE WHEN b.bmi_calc BETWEEN 10 AND 80 THEN round(b.bmi_calc, 2) END           AS bmi,
    t.asa_rating,
    (CASE t.asa_rating
        WHEN 'Healthy' THEN 1 WHEN 'Mild Systemic Disease' THEN 2
        WHEN 'Severe Systemic Disease' THEN 3 WHEN 'Incapacitating Disease' THEN 4
        WHEN 'Moribund' THEN 5 WHEN 'Brain Dead' THEN 6 END)::smallint              AS asa_class,
    t.primary_anes_type_nm                                                          AS anes_type,
    t.patient_class_group,
    t.patient_class_nm,
    t.primary_procedure_nm                                                          AS procedure_nm,
    t.hosp_admsn_time,
    t.in_or_dttm, t.out_or_dttm, t.an_start_datetime, t.an_stop_datetime,
    -- post-surgery columns: kept for analysis, NEVER used as model features
    t.hosp_disch_time, t.los, t.disch_disp_c, t.disch_disp,
    -- quality flags
   CASE
    WHEN
        COALESCE(t.out_or_dttm < t.in_or_dttm, false)
        OR COALESCE(t.in_or_dttm < t.hosp_admsn_time, false)
        OR COALESCE(t.hosp_disch_time < t.out_or_dttm, false)
        OR COALESCE(t.an_stop_datetime < t.an_start_datetime, false)
    THEN 1
    ELSE 0
END::smallint AS ts_order_issue,
    -- TODO scope rule: ASA 5/6 (moribund, brain dead) are never elective. CURRENT RULE IS ONLY PLACEHOLDER!
    -- The export has no elective/urgent field: We need to define this rule!
     CASE
    WHEN t.asa_rating IS NULL
         OR t.asa_rating NOT IN ('Moribund', 'Brain Dead')
    THEN 1
    ELSE 0
END::smallint AS is_elective_proxy
FROM _typed t
CROSS JOIN LATERAL (
    SELECT t.weight_kg / ((t.height_cm / 100.0) ^ 2) AS bmi_calc
) b;

ALTER TABLE clean.surgery ADD PRIMARY KEY (log_id);
CREATE INDEX clean_surgery_mrn_idx  ON clean.surgery (mrn);
CREATE INDEX clean_surgery_date_idx ON clean.surgery (surgery_date);

INSERT INTO etl.rule_log (rule_id, description, rows_affected)
SELECT 'S08', 'surgeries without a valid ICU label (excluded from the mart)',
       count(*) FROM clean.surgery WHERE icu_admission IS NULL
UNION ALL
SELECT 'S09', 'rows in clean.surgery', count(*) FROM clean.surgery;
