# atom-import — carga periódica de ISAD(G) no AtoM.
#
# Imagem INDEPENDENTE (não depende de atom-worker): PHP CLI + extensões do AtoM + Python 3.
# O script executa `php symfony csv:import|csv:export|search:populate`; o CÓDIGO do AtoM
# (/opt/atom) e a config são montados em runtime. O PHP deve ser compatível com o AtoM
# (2.10 → PHP 8.3).
ARG PHP_VERSION=8.3
FROM php:${PHP_VERSION}-cli-bookworm

# Trava de versão: em 21/10/2026 o PHP publicou duas falhas críticas de execução remota (CVE-2026-109618 e
# CVE-2026-109619); as versões corrigidas são 8.5.12, 8.4.27, 8.3.36 e 8.2.35. A tag php:8.3-cli-bookworm anda
# sozinha (a imagem 20261020 saiu com 8.3.35 e a 20261023 com 8.3.36), então o build QUEBRA se a base estiver
# abaixo do mínimo, em vez de publicar uma imagem vulnerável. Ao mudar PHP_VERSION, ajuste PHP_MIN.
ARG PHP_MIN=8.3.36
RUN php -r 'exit(version_compare(PHP_VERSION, $argv[1], ">=") ? 0 : 1);' "$PHP_MIN" \
 || { echo "erro: PHP $(php -r 'echo PHP_VERSION;') abaixo do mínimo $PHP_MIN (falha crítica de 21/10/2026)" >&2; exit 1; }

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 tzdata ca-certificates \
      libxslt1-dev libicu-dev libzip-dev libpng-dev libjpeg-dev libfreetype6-dev zlib1g-dev \
 && docker-php-ext-configure gd --with-freetype --with-jpeg \
 && docker-php-ext-install -j"$(nproc)" pdo_mysql mysqli xsl intl opcache zip gd calendar \
 && pecl install apcu \
 && yes '' | pecl install memcache \
 && docker-php-ext-enable apcu memcache \
 && printf 'apc.enable_cli=1\nmemory_limit=-1\n' > /usr/local/etc/php/conf.d/atom-cli.ini \
 && rm -rf /var/lib/apt/lists/* /tmp/pear

WORKDIR /opt/atom-import
COPY atom_import.py entrypoint.sh ./
RUN chmod +x atom_import.py entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    INTERVAL_SECONDS=600

ENTRYPOINT ["/opt/atom-import/entrypoint.sh"]
CMD ["loop"]
