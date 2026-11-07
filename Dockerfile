# atom-import v2: carga periódica de ISAD(G) no AtoM, sobre a imagem OFICIAL do AtoM (ghcr.io/pytatec/atom).
#
# O `php symfony` roda sobre o código que está DENTRO da imagem do AtoM (sem árvore montada do host), então o `symfony`, as extensões do PHP
# e a versão do AtoM são exatamente os do AtoM que ela atende. A versão desta imagem acompanha a do AtoM em uso. A versão independente, para
# árvore montada, é o Dockerfile.legado.
#
# No compose do deploy, o entrypoint é o do Interopy (montado em /a2a pelo instalador): gera a configuração do AtoM a partir das variáveis ATOM_*
# (com o patch de índice e prefixo) e só então executa o loop do atom_import.py. O estado do AtoM (/atom/state) NÃO precisa de volume aqui.
ARG ATOM_IMAGE=ghcr.io/pytatec/atom:v2.10.2
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

# entrypoint oficial do AtoM (gera a configuração) + comando padrão deste serviço; o compose do deploy troca o entrypoint pelo do Interopy
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["/opt/atom-import/entrypoint.sh", "loop"]
