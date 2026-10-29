#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec sudo /usr/local/bin/docker compose up -d
