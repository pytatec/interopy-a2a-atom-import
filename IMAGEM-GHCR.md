# Imagem do atom-import (GHCR)

`ghcr.io/pytatec/interopy-a2a-atom-import:<versão>`: PHP CLI com as extensões do AtoM e Python 3, que aplica o ISAD(G) (`*_isad.csv` enviados pelo backend do A2A por rsync) nas descrições que o DIP do Archivematica criou, com `php symfony csv:import --update=match-and-update`. O código do AtoM e a configuração são montados em tempo de execução, então a imagem não leva segredo nem host.

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

O padrão continua sendo a imagem local (`atom-import:<data>`, construída no host). Para usar a do GHCR, nas variáveis da stack `atom-saida`:

```
ATOM_IMPORT_IMAGE=ghcr.io/pytatec/interopy-a2a-atom-import
ATOM_IMPORT_VERSION=v1.0.0
ATOM_IMPORT_PULL_POLICY=missing
```

Isso vale para as stacks de saída do repositório `interopy-a2a-deploy` (`ufsm-atom/saida` e `atom-am-36/saida`). Sem essas variáveis, nada muda. Os pacotes são privados: o servidor precisa de `docker login ghcr.io` com um token `read:packages`. Depois de trocar a versão, confira com `docker exec <contêiner-do-import> php -v | head -1`.

## Testar o build localmente

```bash
docker build -t atom-import:teste .
docker run --rm --entrypoint php atom-import:teste -v | head -1                  # PHP 8.3.36 ou mais novo
docker build --build-arg PHP_MIN=9.9.9 -t atom-import:neg .                      # deve FALHAR na trava
```
