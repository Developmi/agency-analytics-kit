{% macro generate_schema_name(custom_schema_name, node) -%}
  {%- if custom_schema_name is not none -%}
    {# D2/B-R1: observability models carry an explicit schema (staging/public)
       in their config() and pass through unchanged. #}
    {{ custom_schema_name | trim }}
  {%- else -%}
    {# D2/B-R1: tenant models (no schema config) route to client_<id>; a missing
       client_id resolves to the dbt_project.yml default 'default' -> client_default
       (fail-fast, never a shared schema). #}
    client_{{ var('client_id') }}
  {%- endif -%}
{%- endmacro %}
