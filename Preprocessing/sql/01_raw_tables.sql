-- =====================================================================
-- 01_raw_tables.sql : raw layer with real data types
-- Nothing is cleaned here. The loader validates the types at the door
-- (a value that cannot be cast stops the load and is reported).
-- Columns that are text or oddly encoded in the SOURCE stay as delivered
-- (see comments) and are fixed by named rules in the clean layer.
-- =====================================================================
CREATE TABLE IF NOT EXISTS raw.patient_information (
    log_id               text,
    mrn                  text,
    disch_disp_c         numeric,
    disch_disp           text,
    hosp_admsn_time      timestamp,
    hosp_disch_time      timestamp,
    los                  numeric,
    icu_admin_flag       text,
    surgery_date         date,
    birth_date           integer,    -- source defect: holds the age in years, not a date
    height               text,       -- source defect: text such as 5' 6 (feet and inches)
    weight               numeric,    -- source: apparently ounces
    sex                  text,
    primary_anes_type_nm text,
    asa_rating_c         numeric,
    asa_rating           text,
    patient_class_group  text,
    patient_class_nm     text,
    primary_procedure_nm text,
    in_or_dttm           timestamp,
    out_or_dttm          timestamp,
    an_start_datetime    timestamp,
    an_stop_datetime     timestamp,
    _load_id             bigint      NOT NULL,
    _loaded_at           timestamptz NOT NULL DEFAULT now(),
    _source_file         text        NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.patient_history (
    mrn            text,
    diagnosis_code text,
    dx_name        text,
    _load_id       bigint      NOT NULL,
    _loaded_at     timestamptz NOT NULL DEFAULT now(),
    _source_file   text        NOT NULL
);

CREATE INDEX IF NOT EXISTS raw_patient_information_load_idx ON raw.patient_information (_load_id);
CREATE INDEX IF NOT EXISTS raw_patient_history_load_idx     ON raw.patient_history (_load_id);
