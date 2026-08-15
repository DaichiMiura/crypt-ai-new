#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_dir="$repo_root/var/paper/EXP-2026-0012"
log_dir="$repo_root/logs/paper"
uv_bin="/home/miura/.local/bin/uv"

mkdir -p "$runtime_dir" "$log_dir"
exec 9>"$runtime_dir/daily.lock"
if ! flock -n 9; then
  echo "paper daily job is already running" >&2
  exit 1
fi

if [[ ! -x "$uv_bin" ]]; then
  echo "uv executable not found: $uv_bin" >&2
  exit 1
fi

cd "$repo_root"
export UV_CACHE_DIR=/tmp/crypt-ai-uv-cache
log_file="$log_dir/$(date -u +%Y-%m-%d).log"

{
  echo "paper daily start: $(date -u --iso-8601=seconds)"
  "$uv_bin" run python scripts/download_binance_btcjpy_daily.py
  "$uv_bin" run python scripts/run_exp_2026_0012_paper.py \
    --bars data/paper/BTCJPY-1d.csv
  echo "paper daily complete: $(date -u --iso-8601=seconds)"
} >>"$log_file" 2>&1
