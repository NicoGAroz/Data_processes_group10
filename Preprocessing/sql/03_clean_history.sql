-- =====================================================================
-- 03_clean_history.sql : raw.patient_history -> clean.history
-- Grain: one row per distinct (patient, diagnosis code, diagnosis name).
-- The source repeats the whole history once per surgery of the patient and has no dates,
-- so the timing of a diagnosis relative to a surgery is UNKNOWN (leakage risk).
-- Acute / post-surgical diagnoses are therefore flagged and kept out of the features.
-- =====================================================================
DROP TABLE IF EXISTS clean.history CASCADE;

-- H00 latest raw load only
CREATE TEMP TABLE _h_raw ON COMMIT DROP AS
SELECT * FROM raw.patient_history
WHERE _load_id = (SELECT max(_load_id) FROM raw.patient_history);

INSERT INTO etl.rule_log (rule_id, description, rows_affected)
SELECT 'H00', 'rows in latest raw load', count(*) FROM _h_raw;

-- H01 normalise text; placeholder codes (IMO0001, 10000) are treated as "no code"
CREATE TEMP TABLE _h_norm ON COMMIT DROP AS
SELECT
    etl.clean_text(mrn) AS mrn,
    CASE WHEN upper(etl.clean_text(diagnosis_code)) IN ('IMO0001', '10000') THEN NULL
         ELSE upper(etl.clean_text(diagnosis_code)) END AS dx_code,
    etl.clean_text(dx_name) AS dx_name
FROM _h_raw
WHERE etl.clean_text(mrn) IS NOT NULL;

-- H02 exact duplicates (the history is repeated once per surgery)
CREATE TEMP TABLE _h_dedup ON COMMIT DROP AS
SELECT DISTINCT mrn, dx_code, dx_name FROM _h_norm;

INSERT INTO etl.rule_log (rule_id, description, rows_affected)
SELECT 'H02', 'exact duplicate rows removed',
       (SELECT count(*) FROM _h_norm) - (SELECT count(*) FROM _h_dedup);

-- H03 ICD-9 chapter (the codes in this table are ICD-9-CM) and acute / post-surgical flag
CREATE TABLE clean.history AS
SELECT
    d.mrn,
    d.dx_code,
    d.dx_name,
    CASE
        WHEN d.dx_code IS NULL THEN 'uncoded'
        WHEN d.dx_code ~ '^V' THEN 'V'
        WHEN d.dx_code ~ '^E' THEN 'E'
        WHEN d.dx_code ~ '^\d{3}(\.\d{1,2})?$' THEN
            CASE
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN   1 AND 139 THEN 'infectious'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 140 AND 239 THEN 'neoplasms'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 240 AND 279 THEN 'endocrine'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 280 AND 289 THEN 'blood'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 290 AND 319 THEN 'mental'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 320 AND 389 THEN 'nervous'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 390 AND 459 THEN 'circulatory'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 460 AND 519 THEN 'respiratory'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 520 AND 579 THEN 'digestive'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 580 AND 629 THEN 'genitourinary'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 630 AND 679 THEN 'pregnancy'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 680 AND 709 THEN 'skin'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 710 AND 739 THEN 'musculoskeletal'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 740 AND 759 THEN 'congenital'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 760 AND 779 THEN 'perinatal'
                WHEN substring(d.dx_code FROM 1 FOR 3)::int BETWEEN 780 AND 799 THEN 'symptoms'
                ELSE 'injury'
            END
        ELSE 'other'
    END AS icd9_chapter,
    -- TODO: AVOIDING LEAKAGE. The surgeries have no date, therefore we don't know if they are pre-, peri- or postoperative.
    -- Current placeholder: 
    -- Heuristic: acute events, trauma, procedure complications, "S/P" and post-operative states.
    -- They may have been recorded AFTER the surgery, so they are not preoperative information.
    (COALESCE(d.dx_code ~ '^99[6-9]', false)
     OR COALESCE(d.dx_code ~ '^E', false)
     OR COALESCE(d.dx_name ~* ('\m(shock|sepsis|septic|ards|rhabdomyolysis|herniation|contusion|'
                               || 'perforation|iatrogenic)\M|acute kidney injury|\maki\M|'
                               || 'acute respiratory|respiratory failure|cerebral edema|hemorrhage|'
                               || '\mburns?\M|fracture|\ms/p\M|status post|post[- ]?op|'
                               || 'post[- ]?procedur|(?<!without )(?<!with )complication'), false)
    ) AS is_acute_or_postop
FROM _h_dedup d;

CREATE INDEX clean_history_mrn_idx ON clean.history (mrn);

INSERT INTO etl.rule_log (rule_id, description, rows_affected)
SELECT 'H03a', 'rows without a usable diagnosis code', count(*) FROM clean.history WHERE dx_code IS NULL
UNION ALL
SELECT 'H03b', 'rows flagged acute / post-surgical (excluded from features)',
       count(*) FROM clean.history WHERE is_acute_or_postop
UNION ALL
SELECT 'H04', 'rows in clean.history', count(*) FROM clean.history
UNION ALL
SELECT 'H05', 'distinct patients in clean.history', count(DISTINCT mrn) FROM clean.history;
