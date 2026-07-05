#!/usr/bin/env bash
set -euo pipefail

cmd="${1:-inspect-frozen}"
shift || true

case "${cmd}" in
  preflight)
    exec /opt/ralfuzz/docker/demo/scripts/00_preflight.sh "$@"
    ;;
  inspect-frozen)
    exec /opt/ralfuzz/docker/demo/scripts/20_inspect_frozen.sh "$@"
    ;;
  run-live)
    exec /opt/ralfuzz/docker/demo/scripts/30_run_live.sh "$@"
    ;;
  bash)
    exec /bin/bash "$@"
    ;;
  *)
    echo "Unknown command: ${cmd}" >&2
    echo "Usage: <image> [preflight|inspect-frozen|run-live|bash]" >&2
    exit 1
    ;;
esac
