{#
    A generic test asserting that every value in a column falls within bounds.

    This is deliberately a local macro rather than a dbt_utils dependency. The
    package would be the conventional choice, but `dbt deps` has to write
    `package-lock.yml` and a `dbt_packages/` directory into the project
    directory -- which is a read-only bind mount in the container -- and it
    pulls from GitHub at build time. Fifteen lines of Jinja removes a network
    dependency, a writable-mount requirement and a version to keep current.

    Usage in a schema.yml:

        columns:
          - name: revenue_cents
            data_tests:
              - accepted_range:
                  min_value: 0
                  inclusive: true

    A dbt test passes when it returns zero rows, so the query below selects the
    rows that VIOLATE the range.
#}
{% test accepted_range(model, column_name, min_value=none, max_value=none, inclusive=true) %}

with validation as (
    select {{ column_name }} as value_field
    from {{ model }}
    where {{ column_name }} is not null
)

select value_field
from validation
where
    1 = 0
    {% if min_value is not none %}
        {% if inclusive %}
            or value_field < {{ min_value }}
        {% else %}
            or value_field <= {{ min_value }}
        {% endif %}
    {% endif %}
    {% if max_value is not none %}
        {% if inclusive %}
            or value_field > {{ max_value }}
        {% else %}
            or value_field >= {{ max_value }}
        {% endif %}
    {% endif %}

{% endtest %}
