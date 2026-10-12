#!/usr/bin/env bash
# Fetch every third-party corpus used by the paper.
#
# Corpora are NOT redistributed with this repository (see ../PROVENANCE.md);
# this script is the only supported way to obtain them. Sources whose licence
# requires acknowledgement print the licence text and refuse to download until
# --accept-license is given.
#
# Usage:
#   ./scripts/fetch_corpora.sh            # fetch all, honouring licence gates
#   ./scripts/fetch_corpora.sh nottingham # fetch one source
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-venv/bin/python}"
if [ ! -x "$PY" ]; then
  PY=python3
fi

echo "== available sources and licences =="
"$PY" -m score_learning list-sources

if [ "$#" -gt 0 ]; then
  for src in "$@"; do
    echo "== fetching $src =="
    "$PY" -m score_learning fetch "$src" --accept-license
  done
else
  echo "== fetching all =="
  "$PY" -m score_learning fetch --all --accept-license
fi

echo "== done; manifests written to scores/corpus/*/manifest.json =="
