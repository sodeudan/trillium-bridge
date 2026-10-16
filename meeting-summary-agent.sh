#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

exec "$script_dir/.venv/bin/python" \
  "$script_dir/meeting-summary/import_transcript.py" \
  "$HOME/meeting-transcripts" "$@"
