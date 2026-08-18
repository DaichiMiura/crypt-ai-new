#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

runtime_dir="$repo_root/var/run/DATA-2026-0010"
mkdir -p "$runtime_dir"
chmod 0700 "$runtime_dir"
exec 9>"$runtime_dir/collector.lock"
if ! flock -n 9; then
  echo "DATA-2026-0010 collector is already running" >&2
  exit 75
fi

minimum_free_bytes=21474836480
available_bytes="$(df -B1 --output=avail "$repo_root" | tail -n 1 | tr -d ' ')"
if [[ ! "$available_bytes" =~ ^[0-9]+$ ]] || (( available_bytes < minimum_free_bytes )); then
  echo "DATA-2026-0010 requires at least 20 GiB free" >&2
  exit 1
fi

expected_collector_sha256="f42097587d689d43d495dadcb12964c22c13e37bb6f008a02937eb25356af871"
expected_lock_sha256="47e5c858e3773bd3442628f19e701d4d88633f4cff43bfb3c545cd579155c279"
actual_collector_sha256="$(sha256sum scripts/collect_zoomex_realtime_microstructure.py | cut -d ' ' -f 1)"
actual_lock_sha256="$(sha256sum uv.lock | cut -d ' ' -f 1)"
if [[ "$actual_collector_sha256" != "$expected_collector_sha256" ]] || \
   [[ "$actual_lock_sha256" != "$expected_lock_sha256" ]]; then
  echo "DATA-2026-0010 fixed collector artifacts do not match" >&2
  exit 1
fi

exec uv run --frozen python scripts/collect_zoomex_realtime_microstructure.py \
  --output-root data/raw/DATA-2026-0010 \
  --duration-seconds 86400
