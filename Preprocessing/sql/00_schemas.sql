-- =====================================================================
-- 00_schemas.sql : layers, roles, helper functions, ETL log tables
--   raw   = typed copy of what was delivered (no cleaning)
--   clean = deduplicated, standardised, one row per business entity
--   mart  = target schema / model-ready tables
--   etl   = load log, rule log, helper functions
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS clean;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS etl;

-- Least-privilege group roles (skipped with a notice if the current user may not create roles).
-- mover_etl may write all layers; mover_reader may only read the mart (models, dashboard).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mover_etl') THEN
        CREATE ROLE mover_etl NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mover_reader') THEN
        CREATE ROLE mover_reader NOLOGIN;
    END IF;
    GRANT USAGE, CREATE ON SCHEMA raw, clean, mart, etl TO mover_etl;
    GRANT USAGE ON SCHEMA mart TO mover_reader;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'roles not created or granted (insufficient privilege) - ask your DBA';
END $$;

-- Text helper: trim, collapse repeated spaces, empty string -> NULL
CREATE OR REPLACE FUNCTION etl.clean_text(t text) RETURNS text
LANGUAGE sql IMMUTABLE AS
$$ SELECT NULLIF(btrim(regexp_replace(t, '\s+', ' ', 'g')), '') $$;

-- Safe casts: return NULL instead of raising. Used by the load gate and by the clean layer.
CREATE OR REPLACE FUNCTION etl.try_numeric(t text) RETURNS numeric
LANGUAGE plpgsql STABLE AS
$$ BEGIN RETURN btrim(t)::numeric; EXCEPTION WHEN others THEN RETURN NULL; END $$;

CREATE OR REPLACE FUNCTION etl.try_integer(t text) RETURNS integer
LANGUAGE plpgsql STABLE AS
$$ BEGIN RETURN btrim(t)::integer; EXCEPTION WHEN others THEN RETURN NULL; END $$;

CREATE OR REPLACE FUNCTION etl.try_date(t text) RETURNS date
LANGUAGE plpgsql STABLE AS
$$ BEGIN RETURN btrim(t)::date; EXCEPTION WHEN others THEN RETURN NULL; END $$;

CREATE OR REPLACE FUNCTION etl.try_timestamp(t text) RETURNS timestamp
LANGUAGE plpgsql STABLE AS
$$ BEGIN RETURN btrim(t)::timestamp; EXCEPTION WHEN others THEN RETURN NULL; END $$;

-- Lineage: one row per load (file or EHR query)
CREATE TABLE IF NOT EXISTS etl.load_log (
    load_id      bigserial PRIMARY KEY,
    table_name   text        NOT NULL,
    source_file  text        NOT NULL,
    file_sha256  text        NOT NULL,
    rows_loaded  bigint,
    loaded_at    timestamptz NOT NULL DEFAULT now(),
    status       text        NOT NULL DEFAULT 'ok'
);

-- Every ETL rule writes how many rows it touched (evidence for the quality documentation)
CREATE TABLE IF NOT EXISTS etl.rule_log (
    rule_id       text,
    description   text,
    rows_affected bigint,
    logged_at     timestamptz NOT NULL DEFAULT now()
);
