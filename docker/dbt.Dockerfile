# =============================================================================
# dbt, running against DuckDB with Postgres attached read-only.
#
# Kept out of the service images on purpose: dbt drags in a large dependency
# tree that has no business inside a container serving HTTP traffic, and it
# runs on demand (`make dbt-build`) rather than continuously.
# =============================================================================
FROM python:3.12-slim-bookworm

# git is needed by `dbt deps` to install packages from GitHub.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# dbt-duckdb pulls in dbt-core and duckdb itself. Pinned so a dbt release
# cannot silently change how the models compile.
RUN pip install --no-cache-dir \
        "dbt-core>=1.9,<2.0" \
        "dbt-duckdb>=1.9,<2.0" \
        "duckdb>=1.1"

RUN groupadd --system --gid 1001 dbt \
    && useradd --system --uid 1001 --gid dbt --create-home dbt

# The project is bind-mounted here by compose, so editing a model on your
# machine takes effect on the next run with no rebuild.
WORKDIR /dbt

# profiles.yml lives beside the project rather than in ~/.dbt, so the whole
# configuration is version-controlled and identical for everyone.
ENV DBT_PROFILES_DIR=/dbt

# dbt's three writable outputs are moved OUT of the bind mount.
#
# The mount is owned by whoever owns the files on the host, which is almost
# never uid 1001. dbt would fail to create `logs/`, `target/` and
# `dbt_packages/` inside it -- and it fails *silently*, exiting non-zero with
# no message at all, because it cannot open its own log file to report the
# problem. That is a genuinely baffling half hour the first time.
#
# Writing them to a container-local directory sidesteps the whole issue.
# Nothing of value is lost: all three are build artefacts, and all three are
# gitignored anyway.
ENV DBT_LOG_PATH=/home/dbt/run/logs \
    DBT_TARGET_PATH=/home/dbt/run/target \
    DBT_PACKAGES_INSTALL_PATH=/home/dbt/run/dbt_packages

# The marts file is written here; compose mounts the same volume the analytics
# service reads from. Both containers run as uid 1001 so they can share it.
RUN mkdir -p /data /home/dbt/run \
    && chown -R dbt:dbt /data /home/dbt
USER dbt

ENTRYPOINT ["dbt"]
CMD ["--help"]
