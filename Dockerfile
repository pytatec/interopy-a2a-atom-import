# atom-import v2: carga periódica de ISAD(G) no AtoM, sobre a IMAGEM DO AtoM do Interopy (interopy-a2a-atom).
#
# Por que mudou: antes o `php symfony` rodava sobre uma árvore do AtoM montada do host (/opt/atom-*). Com o código do AtoM dentro da
# imagem (interopy-a2a-atom) essa árvore deixou de existir; esta imagem parte da própria imagem do AtoM, então o `symfony`, as
# extensões do PHP e a versão do AtoM são exatamente os do AtoM que ela atende. Por isso a versão desta imagem acompanha a do AtoM.
# A trava de versão do PHP (falha crítica de 21/10/2026) está no Dockerfile do AtoM. A versão independente, para árvore montada, é o
# Dockerfile.legado.
#
# O entrypoint do AtoM (docker/a2a-entrypoint.sh) roda antes: gera a configuração do AtoM a partir das variáveis ATOM_* e só então
# executa o loop do atom_import.py. O estado do AtoM (/atom/state) NÃO precisa de volume aqui: o csv:import não usa o tema.
ARG ATOM_IMAGE=ghcr.io/pytatec/interopy-a2a-atom:v1.0.0
FROM ${ATOM_IMAGE}

# python3 para o atom_import.py; tzdata para o TZ do contêiner valer nos logs (a imagem do AtoM é Alpine sem tzdata)
RUN apk add --no-cache python3 tzdata \
 && printf 'apc.enable_cli=1\nmemory_limit=-1\n' > /usr/local/etc/php/conf.d/atom-cli.ini

COPY atom_import.py entrypoint.sh /opt/atom-import/
RUN chmod +x /opt/atom-import/atom_import.py /opt/atom-import/entrypoint.sh

# o `php symfony` roda na raiz do AtoM
ENV PYTHONUNBUFFERED=1 \
    INTERVAL_SECONDS=600 \
    ATOM_ROOT=/atom/src
WORKDIR /atom/src

# ENTRYPOINT do AtoM (gera a configuração) + comando padrão deste serviço
ENTRYPOINT ["docker/a2a-entrypoint.sh"]
CMD ["/opt/atom-import/entrypoint.sh", "loop"]
