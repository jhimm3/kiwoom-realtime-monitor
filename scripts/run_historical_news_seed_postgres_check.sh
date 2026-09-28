#!/bin/sh
# Copy review files into container /tmp and run only in the dedicated test DB.
set -eu

docker_bin=/usr/local/bin/docker
export_file=/tmp/kiwoom-export-historical-news-seed-20260927.py
test_file=/tmp/kiwoom-test-historical-news-seed-20260927.py
container=kiwoom-monitor-server-1
destination=/tmp/news_seed_test

test -s "$export_file"
test -s "$test_file"
sudo -v
sudo "$docker_bin" exec "$container" mkdir -p "$destination/scripts"
sudo "$docker_bin" cp "$export_file" "$container:$destination/scripts/export_historical_news_seed.py"
sudo "$docker_bin" cp "$test_file" "$container:$destination/test_historical_news_seed_postgres.py"
sudo "$docker_bin" exec -e PYTHONPATH="$destination" "$container" \
    python -m unittest -v test_historical_news_seed_postgres
