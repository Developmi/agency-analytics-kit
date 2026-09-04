#!/usr/bin/env bash
set -euo pipefail

# Agency Analytics Kit — Bootstrap RBAC idempotente (spec B1/B2, design D2)
# ---------------------------------------------------------------------------
# Crea, de forma re-ejecutable (NFR2):
#   1. el rol de solo-lectura `metabase_reader` (CREATE ROLE no tiene
#      IF NOT EXISTS -> DO block con guarda; si ya existe solo se sincroniza
#      LOGIN + PASSWORD con el entorno),
#   2. los schemas `staging` + `raw_*` (los 10 conectores) y
#      `public` (los objetos de monitoreo ya los crea 01-*.sql),
#   3. los GRANT de USAGE (schemas) y SELECT (tablas de monitoreo),
#   4. ALTER DEFAULT PRIVILEGES con grantor = POSTGRES_USER (futuro creador
#      de objetos vía dlt/dbt) para que los objetos futuros sigan siendo
#      legibles por metabase_reader sin GRANT manual (B2).
#
# El entrypoint de postgres ejecuta los init en orden lexicográfico: primero
# 01-create-pipeline-tables.sql (tablas de monitoreo) y luego este script.
# Solo corre en el primer arranque de un volumen vacío; re-ejecutarlo a mano
# es seguro (todos los statement son idempotentes).
#
# Variables de entorno (heredadas del container; defaults de desarrollo):
#   POSTGRES_USER  (default: agency_admin) — también grantor de default privileges
#   POSTGRES_DB    (default: agency_dw)
#   METABASE_READER_PASSWORD (default dev: metabase_reader_dev) — DEBE
#   coincidir con MB_DB_PASS de services/metabase/.env en despliegues reales.

POSTGRES_USER="${POSTGRES_USER:-agency_admin}"
POSTGRES_DB="${POSTGRES_DB:-agency_dw}"
METABASE_READER_PASSWORD="${METABASE_READER_PASSWORD:-metabase_reader_dev}"

# Schemas raw_* — espejan las 10 fuentes de models/sources.yml (raw_meta,
# raw_tiktok, raw_google, raw_facebook, raw_instagram, raw_tiktok_organic,
# raw_youtube, raw_pinterest, raw_ga4, raw_gtm). Mantener en sync al agregar
# un conector.
RAW_SCHEMAS=(raw_meta raw_tiktok raw_google raw_facebook raw_instagram \
             raw_tiktok_organic raw_youtube raw_pinterest raw_ga4 raw_gtm)

echo "[init] 02-bootstrap-rbac.sh: POSTGRES_USER=${POSTGRES_USER} db=${POSTGRES_DB}"

# --- 1) Rol metabase_reader (crear si falta; sincronizar password) ----------
# CREATE ROLE no tiene IF NOT EXISTS. :'reader_password' se interpola a nivel
# psql FUERA de dollar-quotes (dentro de $do$ NO se interpola) y format('%L')
# se encarga del escapado correcto del password. \gexec ejecuta la fila
# generada solo si el WHERE matchea (idempotente).
psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
     -v reader_password="${METABASE_READER_PASSWORD}" <<'SQL'
SELECT format('CREATE ROLE metabase_reader LOGIN PASSWORD %L', :'reader_password')
WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'metabase_reader')
\gexec

SELECT format('ALTER ROLE metabase_reader WITH LOGIN PASSWORD %L', :'reader_password')
WHERE EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'metabase_reader')
\gexec
SQL
echo "[init] rol metabase_reader OK"

# --- 2) Schemas staging + raw_* ---------------------------------------------
psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<SQL
CREATE SCHEMA IF NOT EXISTS staging;
SQL
for schema in "${RAW_SCHEMAS[@]}"; do
  psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
       -c "CREATE SCHEMA IF NOT EXISTS ${schema};"
done
echo "[init] schemas staging + ${#RAW_SCHEMAS[@]} raw_* OK"

# --- 3) Grants: USAGE en schemas + SELECT en tablas de monitoreo ------------
# public/staging/raw_*: USAGE. Las tablas de monitoreo (creadas por 01-*.sql
# como POSTGRES_USER) necesitan GRANT explícito; los objetos futuros quedan
# cubiertos por los ALTER DEFAULT PRIVILEGES del paso 4.
psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<SQL
GRANT USAGE ON SCHEMA public TO metabase_reader;
GRANT USAGE ON SCHEMA staging TO metabase_reader;
GRANT SELECT ON public.pipeline_runs     TO metabase_reader;
GRANT SELECT ON public.pipeline_run_steps TO metabase_reader;
SQL
for schema in "${RAW_SCHEMAS[@]}"; do
  psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
       -c "GRANT USAGE ON SCHEMA ${schema} TO metabase_reader;"
done
echo "[init] grants USAGE/SELECT OK"

# --- 4) ALTER DEFAULT PRIVILEGES (grantor = POSTGRES_USER = creador dlt/dbt) -
# Objetos futuros (tablas y vistas creadas por dlt/dbt como POSTGRES_USER) en
# public (marts dbt), staging (views dbt) y raw_* (tablas dlt) quedan
# legibles por metabase_reader sin GRANT manual (B2).
for schema in public staging "${RAW_SCHEMAS[@]}"; do
  psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
       -c "ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA ${schema} GRANT SELECT ON TABLES TO metabase_reader;"
done
echo "[init] default privileges OK (grantor=${POSTGRES_USER})"
echo "[init] 02-bootstrap-rbac.sh completo"
