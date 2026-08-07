#!/bin/bash
set -euo pipefail

# ─── Config ────────────────────────────────────────────────────────────────
LOG_FILE="/var/log/agency_pipeline.log"
CLIENTS_DIR="./clients"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
PG_USER="${POSTGRES_USER:-agency_admin}"
PG_DB="${POSTGRES_DB:-agency_dw}"
PG_CONTAINER="agency_postgres"

# Fallback local si /var/log no es escribible
if [ ! -w "$(dirname "$LOG_FILE")" ] 2>/dev/null; then
  mkdir -p .pipeline/logs
  LOG_FILE=".pipeline/logs/agency_pipeline.log"
fi

# ─── Utilidades ────────────────────────────────────────────────────────────

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

send_telegram() {
  if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    log "[TELEGRAM] Saltado: TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados."
    return
  fi
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" \
    -d text="$1" \
    -d parse_mode="HTML" > /dev/null
}

check_container() {
  local name=$1
  local status
  status=$(docker inspect --format='{{.State.Health.Status}}' "$name" 2>/dev/null)
  if [ "$status" != "healthy" ]; then
    send_telegram "⚠️ <b>Pipeline Abortado</b>
Container <code>$name</code> no está healthy.
Status: <code>${status:-no encontrado}</code>"
    log "ERROR: Container $name unhealthy (status: ${status:-no encontrado}). Abortando."
    exit 1
  fi
}

# ─── Map connector name → dbt staging models ───────────────────────────────
get_connector_models() {
  case "$1" in
    meta)          echo "stg_meta__ads stg_meta__campaigns" ;;
    tiktok)        echo "stg_tiktok__ads stg_tiktok__campaigns" ;;
    google)        echo "stg_google__ads stg_google__campaigns" ;;
    facebook)      echo "stg_facebook__page_posts stg_facebook__feed stg_facebook__page_insights_daily" ;;
    instagram)     echo "stg_instagram__media stg_instagram__insights_daily" ;;
    tiktok_organic) echo "stg_tiktok_organic__profile_stats stg_tiktok_organic__videos_organic" ;;
    youtube)       echo "stg_youtube__channel_stats stg_youtube__videos stg_youtube__video_daily_analytics" ;;
    pinterest)     echo "stg_pinterest__boards stg_pinterest__pins stg_pinterest__board_insights" ;;
    ga4)           echo "stg_ga4__daily_stats stg_ga4__page_analytics stg_ga4__event_analytics" ;;
    gtm)           echo "stg_gtm__containers stg_gtm__tags stg_gtm__triggers" ;;
    public)        echo "stg_public__pipeline_runs stg_public__pipeline_run_steps" ;;
  esac
}

# ─── YAML helper (usa pyyaml dentro del container) ──────────────────────────
yaml_get() {
  local file=$1
  local path=$2
  docker exec agency_pipeline python3 -c "
import yaml, sys
with open('/app/clients/$(basename "$file")') as f:
    data = yaml.safe_load(f)
    val = data
    for p in '${path}'.lstrip('.').split('.'):
        if isinstance(val, dict):
            val = val.get(p)
        else:
            val = None
            break
    print(str(val).lower() if val is not None else 'false')
"
}

yaml_keys() {
  local file=$1
  local path=$2
  docker exec agency_pipeline python3 -c "
import yaml, sys
with open('/app/clients/$(basename "$file")') as f:
    data = yaml.safe_load(f)
    val = data
    for p in '${path}'.lstrip('.').split('.'):
        if isinstance(val, dict):
            val = val.get(p, {})
        else:
            val = {}
            break
    for k in (val or {}):
        print(k)
"
}

check_exit_code() {
  local code=$1
  local step=$2
  local client=$3
  if [ "$code" -ne 0 ]; then
    send_telegram "❌ <b>Error en Pipeline</b>
Paso: <code>$step</code>
Cliente: <code>$client</code>"
    log "ERROR: Falló $step para $client (exit code $code)"
    return 1
  fi
  return 0
}

# ─── Observabilidad: pipeline_runs ─────────────────────────────────────────

psql_exec() {
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -At -c "$1" < /dev/null | head -1
}

psql_cmd() {
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -c "$1" < /dev/null
}

pipeline_start_run() {
  local client_id=$1
  psql_exec "INSERT INTO public.pipeline_runs (client_id, status, started_at)
             VALUES ('${client_id}', 'running', NOW())
             RETURNING id"
}

pipeline_finish_run() {
  local run_id=$1
  local status=$2
  local connectors_ok=$3
  local connectors_failed=$4
  local dbt_status=$5
  local error_msg="${6:-}"
  if [ -n "$error_msg" ]; then
    psql_cmd "UPDATE public.pipeline_runs
              SET status='${status}',
                  finished_at=NOW(),
                  connectors_ok=${connectors_ok},
                  connectors_failed=${connectors_failed},
                  dbt_status='${dbt_status}',
                  dbt_duration_ms=EXTRACT(EPOCH FROM (NOW() - started_at))::int * 1000,
                  error_message='${error_msg//\'/\'\'}'
              WHERE id=${run_id}" > /dev/null
  else
    psql_cmd "UPDATE public.pipeline_runs
              SET status='${status}',
                  finished_at=NOW(),
                  connectors_ok=${connectors_ok},
                  connectors_failed=${connectors_failed},
                  dbt_status='${dbt_status}',
                  dbt_duration_ms=EXTRACT(EPOCH FROM (NOW() - started_at))::int * 1000
              WHERE id=${run_id}" > /dev/null
  fi
}

pipeline_start_step() {
  local run_id=$1
  local step_name=$2
  local connector="${3:-}"
  if [ -n "$connector" ]; then
    psql_exec "INSERT INTO public.pipeline_run_steps (run_id, step_name, connector, status, started_at)
               VALUES (${run_id}, '${step_name}', '${connector}', 'running', NOW())
               RETURNING id"
  else
    psql_exec "INSERT INTO public.pipeline_run_steps (run_id, step_name, status, started_at)
               VALUES (${run_id}, '${step_name}', 'running', NOW())
               RETURNING id"
  fi
}

pipeline_finish_step() {
  local step_id=$1
  local status=$2
  local error_msg="${3:-}"
  if [ -n "$error_msg" ]; then
    psql_cmd "UPDATE public.pipeline_run_steps
              SET status='${status}',
                  finished_at=NOW(),
                  duration_ms=EXTRACT(EPOCH FROM (NOW() - started_at))::int * 1000,
                  error_message='${error_msg//\'/\'\'}'
              WHERE id=${step_id}" > /dev/null
  else
    psql_cmd "UPDATE public.pipeline_run_steps
              SET status='${status}',
                  finished_at=NOW(),
                  duration_ms=EXTRACT(EPOCH FROM (NOW() - started_at))::int * 1000
              WHERE id=${step_id}" > /dev/null
  fi
}

# ─── Inicio ────────────────────────────────────────────────────────────────

log "=== INICIANDO PIPELINE NOCTURNO ==="

# 1. Validar containers
check_container "$PG_CONTAINER"
check_container "agency_pipeline"
log "Containers saludables. Continuando..."

# 2. Loop por cliente
overall_ok=0
overall_failed=0

for client_file in "$CLIENTS_DIR"/*.yml; do
  [ -f "$client_file" ] || continue

  client_id=$(yaml_get "$client_file" '.client_id')
  active=$(yaml_get "$client_file" '.active')

  if [ "$active" != "true" ]; then
    log "Cliente $client_id inactivo. Saltando."
    continue
  fi

  log "--- Procesando cliente: $client_id ---"

  # Crear run en la tabla de monitoreo
  run_id=$(pipeline_start_run "$client_id")
  connectors_total=0
  connectors_ok=0
  connectors_failed=0
  dbt_status="skipped"

  # Contar conectores habilitados
  while IFS= read -r conn; do
    [ -z "$conn" ] && continue
    if [ "$(yaml_get "$client_file" ".connectors.${conn}.enabled")" = "true" ]; then
      connectors_total=$((connectors_total + 1))
    fi
  done < <(yaml_keys "$client_file" ".connectors")

  # Extracción por conector habilitado
  while IFS='|' read -r conn_name conn_flag; do
    conn_name="${conn_name%% }"
    if [ "$conn_flag" != "true" ]; then
      continue
    fi

    step_id=$(pipeline_start_step "$run_id" "dlt_${conn_name}" "$conn_name")
    log "Extrayendo ${conn_name} para ${client_id}..."
    if docker exec agency_pipeline python "src/connectors/run_${conn_name}.py" --client "$client_id"; then
      connectors_ok=$((connectors_ok + 1))
      pipeline_finish_step "$step_id" "success"
    else
      connectors_failed=$((connectors_failed + 1))
      pipeline_finish_step "$step_id" "failed" "dlt_${conn_name} exited with non-zero status"
      send_telegram "⚠️ <b>Fallo dlt</b>
Cliente: <code>${client_id}</code>
Conector: <code>${conn_name}</code>"
      log "ERROR: dlt_${conn_name} falló para ${client_id}. Continuando con siguiente conector."
    fi
  done < <(while IFS= read -r conn; do
    [ -z "$conn" ] && continue
    echo "${conn}|$(yaml_get "$client_file" ".connectors.${conn}.enabled")"
  done < <(yaml_keys "$client_file" ".connectors"))

  # Transformación dbt (solo si al menos un conector funcionó)
  if [ "$connectors_ok" -gt 0 ]; then
    step_id=$(pipeline_start_step "$run_id" "dbt_run")

    # Leer schema del cliente desde YAML
    client_schema_val=$(yaml_get "$client_file" '.schema')

    # Construir selector dinámico de modelos según conectores habilitados
    dbt_select=""
    has_meta="false"
    has_tiktok="false"
    has_google="false"
    while IFS= read -r conn; do
      [ -z "$conn" ] && continue
      enabled=$(yaml_get "$client_file" ".connectors.${conn}.enabled")
      if [ "$enabled" = "true" ]; then
        dbt_select="$dbt_select $(get_connector_models "$conn")"
        case "$conn" in
          meta)   has_meta="true"   ;;
          tiktok) has_tiktok="true"  ;;
          google) has_google="true"  ;;
        esac
      fi
    done < <(yaml_keys "$client_file" ".connectors")

    # Modelos de monitoreo de pipeline siempre disponibles
    dbt_select="$dbt_select int_pipeline_daily_summary pipeline_monitoring"

    # int_unified_spend y sus marts requieren los 3 (meta + tiktok + google)
    if [ "$has_meta" = "true" ] && [ "$has_tiktok" = "true" ] && [ "$has_google" = "true" ]; then
      dbt_select="$dbt_select int_unified_spend ad_spend_summary campaign_performance"
      log "dbt: incluidos modelos de inversión publicitaria (meta+tiktok+google)"
    else
      log "dbt: modelos de inversión omitidos (faltan meta, tiktok o google)"
    fi

    log "dbt: seleccionados${dbt_select}"
    dbt_select="${dbt_select## }"
    dbt_vars='{"client_id": "'"${client_id}"'", "client_schema": "'"${client_schema_val}"'"}'
    if docker exec -w /app/src/dbt_project agency_pipeline \
         dbt run --select "${dbt_select}" \
                 --vars "${dbt_vars}" \
                 --profiles-dir .; then
      dbt_status="success"
      pipeline_finish_step "$step_id" "success"
    else
      dbt_status="failed"
      pipeline_finish_step "$step_id" "failed" "dbt run falló"
      send_telegram "⚠️ <b>Fallo dbt</b>
Cliente: <code>${client_id}</code>"
      log "ERROR: dbt falló para ${client_id}."
    fi
  fi

  # Finalizar run
  if [ "$connectors_failed" -gt 0 ]; then
    pipeline_finish_run "$run_id" "failed" "$connectors_ok" "$connectors_failed" "$dbt_status"
    overall_failed=$((overall_failed + 1))
  else
    pipeline_finish_run "$run_id" "success" "$connectors_ok" "$connectors_failed" "$dbt_status"
    overall_ok=$((overall_ok + 1))
  fi

  log "Cliente ${client_id}: ${connectors_ok}/${connectors_total} conectores OK, dbt: ${dbt_status}"
done

# 3. Resumen final
log "=== PIPELINE FINALIZADO: $overall_ok éxitos, $overall_failed fallos ==="
send_telegram "✅ <b>Pipeline Nocturno Completado</b>
Éxitos: <b>$overall_ok</b>
Fallos: <b>$overall_failed</b>
Hora: $(date '+%Y-%m-%d %H:%M:%S')"
