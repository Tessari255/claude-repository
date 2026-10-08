#!/usr/bin/env bash
# Inicia a Trama (API + frontend compilado) em http://127.0.0.1:8000.
# Use UM processo: o histórico de execuções em andamento é reconciliado na inicialização.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -x backend/.venv/bin/python ] || { echo "Rode primeiro: ./scripts/setup.sh"; exit 1; }
[ -f frontend/dist/index.html ] || (cd frontend && npm run build)

HOST="${TRAMA_HOST:-127.0.0.1}"   # por padrão só aceita conexões locais (não há autenticação)
PORT="${TRAMA_PORT:-8000}"
cd backend
exec .venv/bin/python -m uvicorn app.main:criar_app --factory --host "$HOST" --port "$PORT"
