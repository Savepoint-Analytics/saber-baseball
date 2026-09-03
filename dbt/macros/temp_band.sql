{# Temperature band used by gd_weather_factor and anything joining to it. Kept in a
   macro so the two cannot drift apart -- a mismatch here silently joins nothing and
   every game quietly gets a neutral weather factor. #}
{% macro temp_band(alias) %}
    case
        when {{ alias }}.is_indoor then 0
        when {{ alias }}.weather_temp_f < 50 then 40
        when {{ alias }}.weather_temp_f < 60 then 50
        when {{ alias }}.weather_temp_f < 70 then 60
        when {{ alias }}.weather_temp_f < 80 then 70
        when {{ alias }}.weather_temp_f < 90 then 80
        else 90
    end
{% endmacro %}
