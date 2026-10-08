#!/usr/bin/env bash
# Constrói a imagem do executor isolado (somente biblioteca padrão do Python + runner).
# Variáveis: TRAMA_EXECUTOR_IMAGE (padrão trama-executor:1), BASE_IMAGE (padrão python:3.12-slim).
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGEM="${TRAMA_EXECUTOR_IMAGE:-trama-executor:1}"
BASE="${BASE_IMAGE:-python:3.12-slim}"
# --network none: o build não precisa de rede (nenhum pacote é instalado).
docker build --network none --build-arg "BASE_IMAGE=${BASE}" -t "${IMAGEM}" executor
echo "Imagem ${IMAGEM} pronta."
