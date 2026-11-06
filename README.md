# atom-import — carga periódica de ISAD(G) no AtoM

Script Python (`atom_import.py`) que roda **na máquina do AtoM** e, de tempos em tempos
(via cron), importa para o AtoM os arquivos `*_isad.csv` que o backend Interopy
(`gerar-pacote-atom`) envia por **rsync**.

## Visão geral do fluxo

```
backend-jobs (worker)                          máquina do AtoM
─────────────────────                          ───────────────
processa item da fila
gera <slug>_isad.csv
rsync do CSV  ──────────────► ATOM_RSYNC_PATH = INCOMING_DIR
                                               │
                                  cron (*/10) → atom_import.py
                                               │  php symfony csv:import --update="match-and-update"
                                               │  (sucesso) → EXCLUI o arquivo
                                               │  (falha)   → move para ERROR_DIR
                                               └► php symfony cc + search:populate --update
```

O lado do backend é configurado pelas variáveis `ATOM_RSYNC_HOST`, `ATOM_RSYNC_PORT`,
`ATOM_RSYNC_USER`, `ATOM_RSYNC_PATH` e `ATOM_SSH_KEY_PATH` (ver `interopy-a2a-backend/.env`).
`ATOM_RSYNC_PATH` no backend **deve ser igual** a `INCOMING_DIR` aqui.

## Pré-requisitos

- Python 3 (somente biblioteca padrão — não há dependências externas).
- Usuário do sistema com permissão para rodar `php symfony` na raiz do AtoM (`ATOM_ROOT`).
- `php` e o `symfony` do AtoM funcionais (teste: `cd $ATOM_ROOT && php symfony csv:import --help`).

## Instalação

1. **Copie o script** para a máquina do AtoM, por exemplo em `/opt/atom-import/`:
   ```bash
   sudo mkdir -p /opt/atom-import
   sudo cp atom_import.py /opt/atom-import/
   sudo cp .env.example /opt/atom-import/.env
   sudo chmod +x /opt/atom-import/atom_import.py
   ```

2. **Crie os diretórios** e ajuste o dono para o usuário que rodará o cron e o rsync:
   ```bash
   sudo mkdir -p /var/atom-import/{incoming,work,error}
   sudo touch /var/log/atom-import.log
   sudo chown -R <ATOM_RSYNC_USER>:<grupo> /var/atom-import /var/log/atom-import.log
   ```

3. **Configure o acesso SSH do rsync** (do backend para esta máquina):
   - A chave privada usada pelo backend é a apontada por `ATOM_SSH_KEY_PATH` (`interopy-a2a-backend/.env`).
   - Coloque a **chave pública correspondente** no `~/.ssh/authorized_keys` do usuário
     `ATOM_RSYNC_USER` nesta máquina.
   - Garanta que esse usuário tenha permissão de escrita em `INCOMING_DIR`.

4. **Edite `/opt/atom-import/.env`** com os caminhos corretos (ver `.env.example`).
   Confirme que `INCOMING_DIR` é exatamente o `ATOM_RSYNC_PATH` configurado no backend.

5. **Teste manualmente** antes de agendar:
   ```bash
   # coloque um <slug>_isad.csv em INCOMING_DIR e rode:
   python3 /opt/atom-import/atom_import.py
   tail -n 50 /var/log/atom-import.log
   ```

## Agendamento (crontab)

Edite o crontab do usuário do AtoM (`crontab -e`) e adicione, por exemplo, a cada 10 minutos:

```cron
*/10 * * * * /usr/bin/python3 /opt/atom-import/atom_import.py >> /var/log/atom-import.log 2>&1
```

O script usa um **lock** (`flock`) — se uma execução ainda estiver rodando quando o cron
disparar de novo, a nova execução sai imediatamente sem sobrepor.

## Comportamento

- Processa apenas arquivos `*_isad.csv` cujo `mtime` seja mais antigo que `STABLE_SECONDS`
  (evita pegar um arquivo no meio de um rsync).
- Para cada arquivo: move para `WORK_DIR`, descobre o `legacyId` da descrição já existente
  (pela referência via API do AtoM de carga) e roda
  `php symfony csv:import --update="match-and-update" --skip-unmatched --no-confirmation <csv>`.
  **Sem `--default-parent-slug`**: só atualiza descrições localizadas — nunca cria nem
  re-parenta por um pai padrão (quem cria a descrição é o DIP do Archivematica).
- **Slug pelo nome do arquivo:** o AtoM não aceita slug via CLI (lê a coluna `slug` do
  próprio CSV). Antes de importar, o script sobrescreve a coluna `slug` de todas as linhas
  com o **nome do arquivo sem `_isad.csv`** (ex.: `processo-123_isad.csv` → `processo-123`).
  Se o CSV não puder ser lido/gravado ou não tiver linha de dados, o arquivo vai para `ERROR_DIR`.
- **Sucesso** (returncode 0): o arquivo é **excluído** (carga confirmada no AtoM).
- **Falha**: o arquivo é movido para `ERROR_DIR` e o lote continua nos demais.
- Ao final, se houve ao menos um sucesso e `RUN_INDEX=true`, roda `php symfony cc` e
  `php symfony search:populate --update` — atualização **incremental** do índice (não purga e
  reconstrói tudo), já que o `csv:import` por CLI não indexa por padrão.
- Código de saída do script: `1` se algum arquivo foi para `ERROR_DIR`, senão `0`.

## Opções relevantes do `csv:import` (AtoM 2.10)

| Flag | Efeito |
|------|--------|
| `--update="match-and-update"` | Atualiza descrições existentes (casadas) e insere novas. |
| `--skip-unmatched` | No modo update, ignora registros sem correspondência em vez de abortar. |
| `--no-confirmation` | Não pede confirmação de backup do banco (necessário em modo não interativo). |
| `--index` | Indexa durante o import (alternativa a rodar `search:populate` depois). |

> **Não usamos `--default-parent-slug`.** Ele só afeta a CRIAÇÃO de registros novos (pai
> padrão para linhas sem `qubitParentSlug`). Como aqui só ATUALIZAMOS descrições já criadas
> pelo DIP (com `--skip-unmatched`, nada é criado), o pai padrão não deve ser considerado.

Referência: https://www.accesstomemory.org/pt-br/docs/2.10/admin-manual/maintenance/cli-import-export/

## No ambiente do pacote `atom-am-36` (192.168.100.36)

Aqui o `atom-import` **não** é uma stack à parte: é o serviço `atom-import` da stack `saida` de `interopy-a2a-deploy/atom-am-36`
(contêiner `a2a-atom-import`). A imagem é construída a partir desta pasta (`docker build -t atom-import:<data> interopy-a2a-atom-import`),
`ATOM_IMPORT_VERSION` entra no `saida/.env`, e o serviço reaproveita do AtoM de saída o código, o banco, o índice, o cache e a pasta de
integração (`/data` = `INTEGRACAO_DIR`; `INCOMING_DIR=/data/atom-import/incoming` = `ATOM_RSYNC_PATH` do A2A). O entrypoint grava o
`date.timezone` do PHP CLI a partir de `ATOM_PHP_DATE_TIMEZONE`. Detalhes e passos em `interopy-a2a-deploy/atom-am-36/LEIA-ME.md`.
Teste único: `docker exec a2a-atom-import /opt/atom-import/entrypoint.sh once`.

O modelo abaixo (stack `atom-import-hom` na rede `atom-hom`) vale para o ambiente de homologação em que o AtoM é uma stack própria.

## Implantação com Docker / Portainer (stack `atom-import-hom`)

Arquivos: `Dockerfile`, `entrypoint.sh`, `docker-compose.yml`.

- Roda **na mesma VM** da stack `atom-hom`. A imagem é **independente** (`php:8.3-cli` + extensões do AtoM + Python 3,
  sem depender de `atom-worker`; o código do AtoM vem do volume `/opt/atom`) e executa o `atom_import.py` em loop a
  cada `INTERVAL_SECONDS` (substitui o cron; o `flock` evita sobreposição).
- Entra na rede externa `atom-hom` (acessa `percona`, `elasticsearch`, `gearmand`, `memcached`) e
  usa os mesmos volumes/variáveis `ATOM_*` do `atom_worker`; `ATOM_ROOT` = `/atom/src`
  (confirme onde está o arquivo `symfony` no host: `ls /opt/atom/symfony`).
- Recebimento: o `rsync_server` já existente grava em `/mnt/integracao` (host) = `/data` no rsync
  e `/integracao` neste container. O `ATOM_RSYNC_PATH` do backend-jobs deve ser
  `/data/atom-import/incoming` (ou ajuste `INCOMING_DIR` no compose). `work/`, `retry/`, `error/` e o log
  ficam em `/mnt/integracao/atom-import/`.
- **Portainer:** Stacks → Add stack → Repository, compose path `docker-compose.yml`;
  cadastre as variáveis (ver `.env.example`): `ATOM_MYSQL_DSN/USERNAME/PASSWORD` (iguais às da stack
  atom-hom), `ATOM_API_BASE_URL`, `ATOM_API_KEY`. A stack `atom-hom` precisa estar de pé (rede criada).
- Teste único: `docker exec atom-import-hom /opt/atom-import/entrypoint.sh once`. Logs: aba *Logs*.
