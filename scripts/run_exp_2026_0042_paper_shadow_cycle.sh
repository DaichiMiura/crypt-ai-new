#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_dir="$repo_root/var/paper-shadow/EXP-2026-0042"
log_dir="$repo_root/logs/paper-shadow"
lock_file="$runtime_dir/cycle.lock"

mkdir -p "$runtime_dir" "$log_dir"
exec 9>"$lock_file"
if ! flock -n 9; then
  echo "paper/shadow cycle is already running" >&2
  exit 0
fi

log_file="$log_dir/$(date -u +%Y-%m-%d).log"
{
  echo "cycle start: $(date -u --iso-8601=seconds)"
  cd "$repo_root"
  PYTHONPATH=. UV_CACHE_DIR=/tmp/crypt-ai-uv-cache \
    /home/miura/.local/bin/uv run python \
    scripts/capture_exp_2026_0042_paper_shadow.py
  echo "cycle complete: $(date -u --iso-8601=seconds)"
} >>"$log_file" 2>&1
