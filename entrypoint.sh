#!/bin/sh
# Substitui o cron: executa o atom_import.py a cada INTERVAL_SECONDS.
# O próprio script usa flock, então não há sobreposição de execuções.
# Uso: `loop` (padrão) | `once` (executa uma vez e sai) | qualquer outro comando.
set -eu

# O PHP CLI usa UTC por padrão; o AtoM web usa ATOM_PHP_DATE_TIMEZONE. Sem isto, as datas gravadas pelo csv:import ficam em outro fuso.
if [ -n "${ATOM_PHP_DATE_TIMEZONE:-}" ]; then
  printf 'date.timezone=%s\n' "${ATOM_PHP_DATE_TIMEZONE}" > /usr/local/etc/php/conf.d/zz-atom-timezone.ini
fi

mkdir -p "${INCOMING_DIR}" "${WORK_DIR}" "${ERROR_DIR}" "${RETRY_DIR}" \
         "$(dirname "${LOG_FILE}")" "$(dirname "${LOCK_FILE}")"

case "${1:-loop}" in
  once)
    exec python3 /opt/atom-import/atom_import.py ;;
  loop)
    echo "atom-import: executando a cada ${INTERVAL_SECONDS}s"
    while true; do
      python3 /opt/atom-import/atom_import.py || echo "atom-import: execução terminou com código $?"
      sleep "${INTERVAL_SECONDS}"
    done ;;
  *)
    exec "$@" ;;
esac
