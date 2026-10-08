#!/usr/bin/env bash
# Prepara tudo: ambiente Python, dependências e build do frontend, imagem do executor isolado.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Python 3.11 ou mais novo é necessário (encontrado: $("$PY" --version 2>&1))."; exit 1; }
command -v node >/dev/null || { echo "Node.js 18+ é necessário para compilar o frontend."; exit 1; }

echo "→ Backend (Python)"
"$PY" -m venv backend/.venv
backend/.venv/bin/pip install --quiet --upgrade pip
backend/.venv/bin/pip install --quiet -r backend/requirements-dev.txt

echo "→ Frontend (Node)"
(cd frontend && npm ci --no-audit --no-fund && npm run build)

echo "→ Executor isolado (Docker)"
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  ./scripts/build-executor.sh
else
  echo "  Docker não está disponível. A Trama funciona, mas blocos com código Python ficam desabilitados"
  echo "  até você instalar/iniciar o Docker e rodar ./scripts/build-executor.sh."
fi

echo
echo "Pronto. Inicie com:  ./scripts/start.sh   (depois abra http://127.0.0.1:8000)"
