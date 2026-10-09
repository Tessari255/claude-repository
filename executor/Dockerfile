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
# `timeout` roda como PID 1 do contêiner: é um vigia que o código do usuário não consegue encerrar e que mata
# tudo se o host (a API) morrer no meio da execução. O host passa os segundos e o comando (constantes, nunca
# dados do usuário): `docker run ... IMAGEM <segundos> python -I /opt/trama/runner.py`.
# No pool aquecido o host troca o ENTRYPOINT por `bash` (script fixo em sandbox/executor.py): o contêiner espera o trabalho
# parado e, quando ele chega, vira este mesmo `timeout` por `exec`, então o vigia do trabalho conta desde o recebimento.
ENTRYPOINT ["timeout", "-s", "KILL"]
