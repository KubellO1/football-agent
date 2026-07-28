#!/bin/sh
set -eu

# PostgreSQL 官方镜像仅在数据卷首次初始化时执行本脚本。
test_database="football_test"

if [ "${POSTGRES_DB:-}" = "$test_database" ]; then
    exit 0
fi

database_exists="$(
    psql \
        --username "$POSTGRES_USER" \
        --dbname postgres \
        --tuples-only \
        --no-align \
        --command "SELECT 1 FROM pg_database WHERE datname = '$test_database'"
)"

if [ "$database_exists" != "1" ]; then
    createdb --username "$POSTGRES_USER" "$test_database"
    echo "Created isolated integration-test database: $test_database"
fi
