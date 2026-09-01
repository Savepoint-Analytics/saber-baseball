{# Resolve a bronze dataset glob for read_parquet, switching local vs MinIO via env. #}
{% macro bronze_path(dataset) %}
    {%- set backend = env_var('SABER_STORAGE_BACKEND', 'local') -%}
    {%- if backend == 'minio' -%}
        {%- set prefix = env_var('MINIO_PREFIX', '') | trim('/') -%}
        {%- if prefix -%}
        's3://{{ env_var("MINIO_BUCKET", "saber-bronze") }}/{{ prefix }}/{{ dataset }}/**/*.parquet'
        {%- else -%}
        's3://{{ env_var("MINIO_BUCKET", "saber-bronze") }}/{{ dataset }}/**/*.parquet'
        {%- endif -%}
    {%- else -%}
        '{{ env_var("SABER_DATA_DIR", "../data") }}/bronze/{{ dataset }}/**/*.parquet'
    {%- endif -%}
{% endmacro %}
