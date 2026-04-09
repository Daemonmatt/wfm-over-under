#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Default 8511 if 8501 is already used by another Streamlit app; override: PORT=8501 ./run.sh
exec python3 -m streamlit run app.py --server.port "${PORT:-8511}" "$@"
