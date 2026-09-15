{# Season -> era bucket. The run environment moves (the 2019 ball, the 2023 rule
   changes), so era-bucketed marts (gd_run_expectancy, gd_base_transitions, ...)
   are keyed on this. Any model that JOINS to one of them on `era` must derive the
   bucket the same way, or the join silently drops rows -- which is why it is a
   macro and not a case expression copied into each model. #}
{% macro era(season) %}
    case
        when {{ season }} <= 2018 then '2015-2018'
        when {{ season }} <= 2022 then '2019-2022'
        else '2023-2026'
    end
{% endmacro %}
