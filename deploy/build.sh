#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# The build runs on separate compute; reserve the small heap cap for serving.
export NODE_OPTIONS=--max-old-space-size=2048
python --version
node --version
python -m pip install -r backend/requirements.txt
(cd backend && python -m pytest -q)
python -m pytest deploy/test_deployment.py -q
cd frontend
npm ci --include=dev
npm run lint
npm run typecheck
npm run build
