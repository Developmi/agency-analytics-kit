#!/usr/bin/env bash
set -euo pipefail

# Agency Analytics Kit — Bootstrap RBAC idempotente v2 (spec D, design D4/D5)
# ---------------------------------------------------------------------------
# v2 (multi-tenancy-real): los schemas por tenant YA NO se pre-crean aquí.
# `raw_<connector>_<client_id>` los crea dlt y `client_<client_id>` los crea
# dbt al primer load/build (create-on-first-load, D4) — ambos como
# POSTGRES_USER, único escritor operacional. El rol de solo-lectura de
# Metabase es OPT-IN vía env-gate (D5, default OFF):
#
#   METABASE_READER_ENABLED=false  (default)
#
# Con el gate en OFF no existe rol ni grants (escenario D-S1). Con el valor
# EXACTO "true" (gate booleano estricto; cualquier otro valor = OFF) la rama
# es idempotente y cubre:
#   1. rol `metabase_reader` (crear si falta / sincronizar password) — DO
#      block + \gexec con format('%L'), mismo patrón que v1,
#   2. catch-up USAGE + SELECT sobre objetos YA EXISTENTES, enumerados vía
#      information_schema.schemata (sin listas YAML/hardcodeadas → cubre
#      schemas tenant creados ANTES del enable, D5),
#   3. ALTER DEFAULT PRIVILEGES GLOBALES (PG16, sin IN SCHEMA → aplican a
#      schemas creados DESPUÉS del enable) con grantor = POSTGRES_USER, el
#      creador real de objetos vía dlt/dbt (D4/D-R2).
#
# El entrypoint de postgres ejecuta los init en orden lexicográfico (primero
# 01-create-pipeline-tables.sql y luego este script) solo en el primer arranque
# de un volumen vacío. Re-ejecutarlo a mano es seguro (idempotente, exit 0):
#
#   docker exec agency_postgres /docker-entrypoint-initdb.d/02-bootstrap-rbac.sh
#
# Variables de entorno (heredadas del container; defaults de desarrollo):
#   POSTGRES_USER            (default: agency_admin) — también grantor de default privileges
#   POSTGRES_DB              (default: agency_dw)
#   METABASE_READER_ENABLED  (default: false) — "true" habilita la rama Metabase
#   METABASE_READER_PASSWORD (default dev: metabase_reader_dev) — DEBE
#   coincidir con MB_DB_PASS de services/metabase/.env en despliegues reales.

POSTGRES_USER="${POSTGRES_USER:-agency_admin}"
POSTGRES_DB="${POSTGRES_DB:-agency_dw}"
METABASE_READER_ENABLED="${METABASE_READER_ENABLED:-false}"
METABASE_READER_PASSWORD="${METABASE_READER_PASSWORD:-metabase_reader_dev}"

echo "[init] 02-bootstrap-rbac.sh v2: POSTGRES_USER=${POSTGRES_USER} db=${POSTGRES_DB} METABASE_READER_ENABLED=${METABASE_READER_ENABLED}"

# --- 1) Schema compartido staging (cadena de observabilidad) -----------------
# `public` ya existe por defecto y sus objetos los crea 01-*.sql. staging lo
# consumen los modelos dbt de observabilidad con config(schema='staging');
# crearlo aquí mantiene el init autocontenido (IF NOT EXISTS, idempotente).
psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<'SQL'
CREATE SCHEMA IF NOT EXISTS staging;
SQL
echo "[init] schema compartido staging OK"

# --- 2) metabase_reader: rama OPT-IN (env-gate estricto) ----------------------
# Gate booleano estricto: SOLO el valor exacto "true" habilita la rama;
# cualquier otro valor (vacío, "True", "1", ...) se trata como OFF — fail-safe
# por defecto sin role ni grants (D-S1, threat matrix shell/subprocess).
if [ "${METABASE_READER_ENABLED}" = "true" ]; then
  # 2a) Rol (crear si falta; sincronizar password). CREATE ROLE no tiene
  # IF NOT EXISTS. :'reader_password' se interpola a nivel psql FUERA de
  # dollar-quotes y format('%L') se encarga del escapado correcto; \gexec
  # ejecuta la fila generada solo si el WHERE matchea (idempotente).
  psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
       -v reader_password="${METABASE_READER_PASSWORD}" <<'SQL'
SELECT format('CREATE ROLE metabase_reader LOGIN PASSWORD %L', :'reader_password')
WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'metabase_reader')
\gexec

SELECT format('ALTER ROLE metabase_reader WITH LOGIN PASSWORD %L', :'reader_password')
WHERE EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'metabase_reader')
\gexec
SQL
  echo "[init] rol metabase_reader OK (creado o password sincronizado)"

  # 2b) Catch-up USAGE/SELECT sobre objetos EXISTENTES. La enumeración sale de
  # information_schema.schemata (schemas no-sistema de la DB actual) → cubre
  # schemas tenant creados antes del enable sin listas YAML/hardcodeadas; %I
  # quotea los nombres de schema. GRANT es aditivo → re-ejecutar es seguro.
  psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" <<'SQL'
SELECT format('GRANT USAGE ON SCHEMA %I TO metabase_reader', schema_name)
FROM information_schema.schemata
WHERE schema_name NOT LIKE 'pg\_%'
  AND schema_name <> 'information_schema'
\gexec

SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO metabase_reader', schema_name)
FROM information_schema.schemata
WHERE schema_name NOT LIKE 'pg\_%'
  AND schema_name <> 'information_schema'
\gexec
SQL
  echo "[init] catch-up grants USAGE/SELECT OK (objetos existentes)"

  # 2c) Default privileges GLOBALES (PG16; sin IN SCHEMA → aplican a schemas
  # creados DESPUÉS del enable). Grantor = POSTGRES_USER = creador real de
  # objetos vía dlt/dbt → los objetos futuros (raw_*_<client>, client_<id>,
  # staging, public) quedan legibles por metabase_reader sin GRANT manual.
  psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
       -c "ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} GRANT USAGE ON SCHEMAS TO metabase_reader;"
  psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
       -c "ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} GRANT SELECT ON TABLES TO metabase_reader;"
  echo "[init] default privileges globales OK (grantor=${POSTGRES_USER})"
else
  echo "[init] metabase_reader OFF (default): role y grants omitidos"
fi

echo "[init] 02-bootstrap-rbac.sh v2 completo"
