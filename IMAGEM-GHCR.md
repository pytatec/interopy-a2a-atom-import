# Imagem do atom-import (GHCR)

`ghcr.io/pytatec/interopy-a2a-atom-import:<versão>`: PHP CLI com as extensões do AtoM e Python 3, que aplica o ISAD(G) (`*_isad.csv` enviados pelo backend do A2A por rsync) nas descrições que o DIP do Archivematica criou, com `php symfony csv:import --update=match-and-update`. O código do AtoM e a configuração são montados em tempo de execução, então a imagem não leva segredo nem host.

> **v2.0.0 (base mudou):** a imagem agora é construída **sobre a imagem do AtoM** (`ghcr.io/pytatec/interopy-a2a-atom`), escolhida por
> `--build-arg ATOM_IMAGE=ghcr.io/pytatec/interopy-a2a-atom:vX.Y.Z`. O código do AtoM vem dentro dela (nada de árvore montada), e a
> trava de versão do PHP descrita abaixo passa a viver no Dockerfile do AtoM. Para construir é preciso ter acesso de leitura ao pacote
> `interopy-a2a-atom` (`docker login ghcr.io`; no GitHub Actions, dar acesso ao repositório nas configurações do pacote). A versão
> independente é o `Dockerfile.legado` (tags `v1.x`). Ao subir o AtoM para uma versão nova, publique um atom-import novo com
> `ATOM_IMAGE` apontando para ela.

## Publicar uma versão

O GitHub Actions (`.github/workflows/imagem.yml`) constrói e publica a imagem **somente quando uma tag é enviada**:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Formato aceito: `vX.Y.Z` ou `vX.Y.Z-rc.1`; qualquer outro é recusado, inclusive `latest`. Se a versão já existe no registro, o build falha: tags não são sobrescritas. Para corrigir uma versão, publique outra. Não é preciso criar segredo: o workflow usa o `GITHUB_TOKEN` deste repositório. A versão deste componente é independente da do backend e da do frontend do A2A.

Na primeira publicação o GitHub cria o pacote como privado. Em *Package settings*, conecte ao repositório e dê leitura a quem vai instalar (a conta pessoal `pytatec` só convida pessoas, com papel *Read*; veja `imagens/LEIA-ME.md` no repositório `interopy-a2a-deploy`).

## Trava de versão do PHP

O PHP publicou em 21/10/2026 duas falhas críticas de execução remota (CVE-2026-109618 e CVE-2026-109619). Versões corrigidas: 8.5.12, 8.4.27, **8.3.36** e 8.2.35. O `Dockerfile` parte de `php:8.3-cli-bookworm`, uma tag que se move, e **recusa o build se o PHP da base estiver abaixo de `PHP_MIN` (8.3.36)**. Se a base ainda não tiver sido atualizada no Docker Hub, o build falha com `PHP x.y.z abaixo do mínimo`, em vez de publicar uma imagem vulnerável. Ao mudar `PHP_VERSION`, ajuste também `PHP_MIN`. O resumo de cada execução do Actions mostra o `php -v` da imagem publicada.

## Usar nos compose

Nos compose do repositório `interopy-a2a-deploy`, o serviço `atom-import` escolhe a imagem por variáveis da stack:

| stack | padrão | variáveis |
|---|---|---|
| `ufsm-atom/saida` (UFSM) | **imagem do GHCR** | `ATOM_IMPORT_VERSION=v1.0.0` (a tag `vX.Y.Z` do repositório do atom-import) |
| `docker-compose.yml` deste repositório (stack avulsa `atom-import-hom`) | **imagem do GHCR** | `ATOM_IMPORT_VERSION=vX.Y.Z` (obrigatória). Para construir localmente: `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build` |
| `atom-am-36/saida` (laboratório `.36`) | imagem local, construída no host (`atom-import:<data>`) | para usar o GHCR: `ATOM_IMPORT_IMAGE=ghcr.io/pytatec/interopy-a2a-atom-import`, `ATOM_IMPORT_VERSION=vX.Y.Z` e `ATOM_IMPORT_PULL_POLICY=missing` |

Na UFSM, para voltar a uma imagem construída no host: `ATOM_IMPORT_IMAGE=atom-import`, `ATOM_IMPORT_VERSION=<data>` e `ATOM_IMPORT_PULL_POLICY=never`.

**Ao migrar uma stack que já roda com uma imagem local**, troque `ATOM_IMPORT_VERSION` (por exemplo `20261020`) para a tag do GHCR (`v1.0.0`) **ao mesmo tempo** que atualiza o compose. Se o compose novo for aplicado com a versão antiga, o Compose tenta baixar `ghcr.io/pytatec/interopy-a2a-atom-import:20261020`, que não existe, e o import não sobe.

Os pacotes são privados: o servidor precisa de `docker login ghcr.io` com um token `read:packages`. Depois de trocar a versão, confira com `docker exec <contêiner-do-import> php -v | head -1` (deve mostrar PHP 8.3.36 ou mais novo).

## Testar o build localmente

```bash
docker build -t atom-import:teste .
docker run --rm --entrypoint php atom-import:teste -v | head -1                  # PHP 8.3.36 ou mais novo
docker build --build-arg PHP_MIN=9.9.9 -t atom-import:neg .                      # deve FALHAR na trava
```
