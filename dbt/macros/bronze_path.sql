{# Resolve a bronze dataset glob for read_parquet, switching local vs MinIO via env. #}
{% macro bronze_path(dataset) %}
    {%- set backend = env_var('SABER_STORAGE_BACKEND', 'local') -%}
    {%- if backend == 'minio' -%}
        's3://{{ env_var("MINIO_BUCKET", "saber-bronze") }}/{{ dataset }}/**/*.parquet'
    {%- else -%}
        '{{ env_var("SABER_DATA_DIR", "../data") }}/bronze/{{ dataset }}/**/*.parquet'
    {%- endif -%}
{% endmacro %}
