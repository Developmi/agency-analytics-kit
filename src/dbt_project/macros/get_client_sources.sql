{% macro get_client_sources(client_id) %}
  {% set source_list = [] %}
  {% set clients_dir = var('clients_dir', '/app/clients') %}
  {% set client_file = clients_dir ~ '/' ~ client_id ~ '.yml' %}

  {% if modules.os.path.exists(client_file) %}
    {% set file_content = modules.yaml.safe_load(modules.open(client_file).read()) %}
    {% for connector, config in file_content.connectors.items() %}
      {% if config.get('enabled', false) %}
        {% do source_list.append('raw_' ~ connector) %}
      {% endif %}
    {% endfor %}
  {% endif %}

  {{ return(source_list) }}
{% endmacro %}
