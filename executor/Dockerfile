# Imagem do executor isolado da Trama.
# Contém apenas a biblioteca padrão do Python + runner.py. Nenhum pacote extra,
# nenhum segredo, nenhum arquivo do host.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# Reduz a superfície: o código do usuário não precisa de pip/setuptools.
RUN python -m pip uninstall -y pip setuptools wheel >/dev/null 2>&1 || true

COPY runner.py /opt/trama/runner.py
RUN chmod 0555 /opt/trama && chmod 0444 /opt/trama/runner.py

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    HOME=/tmp

# Usuário sem privilégios (nobody). O host ainda reforça --user, --read-only,
# --network none, --cap-drop ALL e limites de recursos em cada execução.
USER 65534:65534
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "/opt/trama/runner.py"]
