#!/usr/bin/env sh
set -eu

# Cloud Run stores runtime values as environment variables.  Streamlit reads
# deployment settings from secrets.toml, so build that file only inside the
# running container; no credential is committed to Git.
python - <<'PY'
import json
import os
from pathlib import Path

keys = (
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "OPENAI_API_KEY",
    "PAYUNI_SANDBOX_ENABLED",
    "NEWEBPAY_LIVE_ENABLED",
    "NEWEBPAY_WALLETS_ENABLED",
    "V12_ACCEPTANCE_MODE",
    "SITE_URL",
)
target = Path(".streamlit/secrets.toml")
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(
    "\n".join(
        f"{key} = {json.dumps(os.environ[key])}"
        for key in keys
        if os.environ.get(key)
    ) + "\n",
    encoding="utf-8",
)
PY

exec streamlit run app.py \
  --server.address=0.0.0.0 \
  --server.port="${PORT:-8080}" \
  --server.headless=true
