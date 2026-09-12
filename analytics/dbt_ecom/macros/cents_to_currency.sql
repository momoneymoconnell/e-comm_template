{#
    Format an integer minor-unit amount as a decimal.

    Money is stored and aggregated as integer cents throughout this project -
    see ecom_shared.schemas for why floats are not an option. This macro exists
    for the last step only: presenting a figure in a BI tool that wants
    12.34 rather than 1234.

    Never use it inside an aggregation. Dividing before summing reintroduces
    exactly the floating-point rounding that integer storage exists to avoid.

    Usage:
        select {{ cents_to_currency('revenue_cents') }} as revenue
#}
{% macro cents_to_currency(column_name) -%}
    round(cast({{ column_name }} as double) / 100.0, 2)
{%- endmacro %}
