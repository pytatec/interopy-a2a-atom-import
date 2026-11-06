#!/usr/bin/env python3
"""
atom_import.py — Carga periódica (cron) de descrições ISAD(G) no AtoM.

Roda NA MÁQUINA DO ATOM. Varre o diretório de entrada (onde o backend Interopy
deposita os CSVs ISAD via rsync — nomeados pelo objeto digital, ex.: `logo_2.png.csv`),
executa a importação CSV do AtoM
(`php symfony csv:import`) para cada arquivo e, APÓS confirmar a carga com sucesso,
EXCLUI o arquivo. Em caso de falha, o arquivo é movido para o diretório de erro.

Antes de importar, o CSV é ajustado para o formato de ATUALIZAÇÃO do AtoM
(`--update=match-and-update`). Como a API REST do AtoM não expõe o id interno, o
`legacyId` da descrição já existente é descoberto AQUI, na máquina do AtoM, via
`csv:export --single-slug=<slug>` (que grava o id na coluna `legacyId`).

O slug que o Archivematica gera no AtoM de carga vem do título/arquivo e NÃO é confiável
(colide entre itens de mesmo título), então o localizador PRIMÁRIO é o
`reference_code`/`identifier` (único por descrição): (1) localiza a descrição pela referência
na API REST do AtoM de carga (`ATOM_API_BASE_URL`; `sq0=<ref>&sf0=<campo>`, confirmando o
casamento pelo detalhe), pega o slug real e faz o `csv:export` dele; (2) como fallback
(captura==carga na MESMA instância, ou API desligada) tenta pelo slug do CSV. Sem legacyId o
match-and-update criaria uma duplicata — então o arquivo NÃO é importado (nunca cria; o DIP é
quem cria a descrição).
Como o DIP costuma concluir minutos depois do rsync do ISAD, quando o legacyId ainda não é
encontrado o arquivo espera em `RETRY_DIR` e é tentado de novo a cada execução, até achar
ou exceder `RETRY_MAX_AGE_SECONDS` (aí vai para `ERROR_DIR`). Uma vez localizado:
  - o `legacyId` descoberto é injetado (casa a descrição existente);
  - as colunas `referenceCode` e `slug` são REMOVIDAS (não devem existir no update);
  - `parentId` é esvaziado (a hierarquia é resolvida por `qubitParentSlug`);
  - `digitalObjectURI` e `digitalObjectChecksum` são esvaziados (no update não se
    reanexa/rebaixa o objeto digital);
  - `qubitParentSlug` (slug do pai) e as demais colunas são preservados.

Não depende de bibliotecas externas — apenas a biblioteca padrão do Python 3.

Configuração: variáveis de ambiente, opcionalmente carregadas de um arquivo `.env`
ao lado deste script (ver `.env.example`).
"""

from __future__ import annotations

import csv
import datetime as _dt
import fcntl
import json
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Configuração
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent


def _carregar_dotenv(caminho: Path) -> None:
    """Carrega um .env simples (KEY=VALUE) no os.environ, sem sobrescrever o que já existe."""
    if not caminho.is_file():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        chave = chave.strip()
        valor = valor.strip()
        # Remove comentário inline (espaço/tab + #) em valores NÃO aspados — evita, p.ex.,
        # que "URL   # comentário" vire uma URL com espaços/`#` (InvalidURL no urllib).
        if valor[:1] not in ("'", '"'):
            for sep in (" #", "\t#"):
                pos = valor.find(sep)
                if pos != -1:
                    valor = valor[:pos].rstrip()
        valor = valor.strip('"').strip("'")
        os.environ.setdefault(chave, valor)


_carregar_dotenv(SCRIPT_DIR / ".env")


def _env(nome: str, padrao: str = "") -> str:
    return os.environ.get(nome, padrao).strip()


def _env_bool(nome: str, padrao: bool) -> bool:
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in ("1", "true", "yes", "on", "sim")


# Raiz da instalação do AtoM (onde existe o `symfony` e roda `php symfony`).
ATOM_ROOT = _env("ATOM_ROOT", "/usr/share/nginx/atom")
# Diretório de entrada — o MESMO apontado por ATOM_RSYNC_PATH no backend.
INCOMING_DIR = Path(_env("INCOMING_DIR", "/var/atom-import/incoming"))
# Diretório de trabalho (arquivo em processamento) e de erro.
WORK_DIR = Path(_env("WORK_DIR", "/var/atom-import/work"))
ERROR_DIR = Path(_env("ERROR_DIR", "/var/atom-import/error"))
# Fila de reprocessamento: quando a descrição ainda não existe no AtoM de carga (o DIP do
# Archivematica termina minutos depois do rsync do ISAD), o arquivo espera aqui e é tentado
# de novo nas próximas execuções, até achar ou exceder RETRY_MAX_AGE_SECONDS.
RETRY_DIR = Path(_env("RETRY_DIR", "/var/atom-import/retry"))
LOG_FILE = Path(_env("LOG_FILE", "/var/log/atom-import.log"))
LOCK_FILE = Path(_env("LOCK_FILE", "/var/atom-import/.lock"))

PHP_BIN = _env("PHP_BIN", "php")
# match-and-update (default) atualiza descrições existentes e insere novas.
UPDATE_MODE = _env("UPDATE_MODE", "match-and-update")

# API REST do AtoM de CARGA (esta máquina) — usada para localizar a descrição pelo
# reference_code/identifier quando o slug do CSV (origem/captura) não existe aqui: o
# Archivematica cria a descrição no AtoM de carga com um slug DIFERENTE (`<uuid>-arquivo`),
# então o csv:export pelo slug de origem falha. Vazio = desliga a busca por API.
# .split("#") + remoção de espaços: robustez extra contra comentário inline/espaços
# na URL (o urllib rejeita host com espaços/control chars).
ATOM_API_BASE_URL = "".join(
    _env("ATOM_API_BASE_URL", "").split("#", 1)[0].split()
).rstrip("/")
ATOM_API_KEY = _env("ATOM_API_KEY", "")
# Campos do AtoM tentados na busca boolean (sf0), em ordem. Casa-se pelo reference_code
# (código completo, único → campo `referenceCode`); `identifier` é secundário e usa a FOLHA
# do código (ex.: `40`), pois o AtoM guarda só a folha nesse campo. Aceita `ATOM_MATCH_FIELD`
# (singular) por compatibilidade. Lista separada por vírgula.
ATOM_MATCH_FIELDS = [
    c.strip()
    for c in (
        _env("ATOM_MATCH_FIELDS", "")
        or _env("ATOM_MATCH_FIELD", "")
        or "referenceCode,identifier"
    ).split(",")
    if c.strip()
]
# Verificar o certificado TLS ao chamar a API do AtoM (false p/ self-signed).
ATOM_API_VERIFY_TLS = _env_bool("ATOM_API_VERIFY_TLS", True)
# Após o lote, limpar cache e repopular o índice (csv:import não indexa por padrão).
RUN_INDEX = _env_bool("RUN_INDEX", True)
# Só processa arquivos cujo mtime seja mais antigo que isto (evita pegar arquivo no
# meio de um rsync em andamento).
STABLE_SECONDS = int(_env("STABLE_SECONDS", "60") or "60")
# Timeout (segundos) por execução de `php symfony`.
COMMAND_TIMEOUT = int(_env("COMMAND_TIMEOUT", "1800") or "1800")
# Idade máxima (segundos) de um arquivo aguardando o DIP antes de desistir (→ ERROR_DIR).
# Padrão 6h — tempo folgado para o Archivematica concluir e o DIP aparecer no AtoM de carga.
RETRY_MAX_AGE_SECONDS = int(_env("RETRY_MAX_AGE_SECONDS", "21600") or "21600")


# ─────────────────────────────────────────────────────────────────────────────
# Utilitários
# ─────────────────────────────────────────────────────────────────────────────


def log(mensagem: str) -> None:
    carimbo = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linha = f"[{carimbo}] {mensagem}"
    print(linha, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(linha + "\n")
    except OSError as exc:  # logging não deve derrubar o processo
        print(f"[{carimbo}] AVISO: falha ao escrever no log: {exc}", flush=True)


def _idade_arquivo(arquivo: Path) -> float | None:
    """Idade do arquivo em segundos (agora - mtime); None se não puder ler o stat.

    O `shutil.move` preserva o mtime, então a idade reflete quando o ISAD foi gerado/
    recebido — base para decidir por quanto tempo esperar o DIP do Archivematica.
    """
    try:
        return _dt.datetime.now().timestamp() - arquivo.stat().st_mtime
    except OSError:
        return None


def _arquivo_estavel(arquivo: Path) -> bool:
    """True se o arquivo não foi modificado nos últimos STABLE_SECONDS (rsync concluído)."""
    idade = _idade_arquivo(arquivo)
    return idade is not None and idade >= STABLE_SECONDS


def _mover_para(arquivo: Path, destino_dir: Path) -> None:
    """Move `arquivo` para `destino_dir`, logando (sem levantar) em caso de falha."""
    try:
        shutil.move(str(arquivo), str(destino_dir / arquivo.name))
    except OSError as exc:
        log(f"ERRO: falha ao mover {arquivo.name} para {destino_dir}: {exc}")


def _rodar(comando: list[str]) -> subprocess.CompletedProcess:
    log(f"$ (cwd={ATOM_ROOT}) {' '.join(comando)}")
    proc = subprocess.run(
        comando,
        cwd=ATOM_ROOT,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT,
        check=False,
    )
    if proc.stdout:
        log(f"stdout:\n{proc.stdout.rstrip()}")
    if proc.stderr:
        log(f"stderr:\n{proc.stderr.rstrip()}")
    log(f"returncode: {proc.returncode}")
    return proc


# Colunas que NÃO devem existir no CSV de atualização.
# `referenceCode`/`slug`: o AtoM casa a descrição pelo legacyId e o slug do objeto não
# muda no update — mantê-los pode conflitar com a descrição existente.
COLUNAS_REMOVER = ("referenceCode", "slug")
# Colunas cujo valor deve ser esvaziado no CSV de atualização.
# `parentId`: a hierarquia é resolvida por `qubitParentSlug`.
# `digitalObjectURI`/`digitalObjectChecksum`: no update não se reanexa o objeto digital.
COLUNAS_ESVAZIAR = ("parentId", "digitalObjectURI", "digitalObjectChecksum")


def _slug_alvo(arquivo: Path) -> str:
    """Slug do AtoM a atualizar: valor da coluna `slug` do CSV; fallback = nome do arquivo.

    O backend grava o slug (folha) do AtoM na coluna `slug`; usamos esse valor para
    localizar a descrição existente. Este é apenas um FALLBACK (o localizador primário é a
    referência via API). Se a coluna estiver ausente/vazia, cai para o nome do arquivo sem
    a extensão. Obs.: o backend passou a nomear o ISAD pelo objeto digital (ex.:
    `logo_2.png.csv`), então o nome do arquivo NÃO é mais um slug — por isso a coluna `slug`
    é a fonte confiável. (Arquivos antigos `*_isad.csv` ainda têm o `_isad` removido.)
    """
    try:
        with arquivo.open("r", encoding="utf-8", newline="") as fh:
            leitor = csv.reader(fh)
            cabecalho = next(leitor, [])
            primeira = next(leitor, [])
    except OSError as exc:
        log(f"ERRO: falha ao ler {arquivo.name} para obter o slug: {exc}")
        primeira = cabecalho = []

    if "slug" in cabecalho:
        idx = cabecalho.index("slug")
        if idx < len(primeira) and primeira[idx].strip():
            return primeira[idx].strip()

    nome = arquivo.stem  # remove .csv
    if nome.endswith("_isad"):  # compat. com arquivos antigos `<slug>_isad.csv`
        nome = nome[: -len("_isad")]
    return nome.strip()


def _obter_legacy_id(slug: str) -> str | None:
    """Descobre o legacyId (id interno do AtoM) da descrição já existente com este slug.

    A API REST do AtoM NÃO expõe o id numérico; como este script roda NA MÁQUINA DO
    ATOM, usamos `csv:export --single-slug=<slug>` (que grava o id na coluna `legacyId`
    do CSV exportado) para obtê-lo. Sem o legacyId, `--update=match-and-update` não casa
    a descrição e o AtoM cria uma duplicata. Retorna None se a descrição não existir ou
    o export falhar.
    """
    export_dir = WORK_DIR / (".export-" + slug.replace("/", "_"))
    shutil.rmtree(export_dir, ignore_errors=True)
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log(f"ERRO: falha ao criar dir de export para slug '{slug}': {exc}")
        return None

    comando = [
        PHP_BIN,
        "symfony",
        "csv:export",
        f"--single-slug={slug}",
        str(export_dir),
    ]
    try:
        proc = _rodar(comando)
        if proc.returncode != 0:
            saida = f"{proc.stdout or ''}\n{proc.stderr or ''}"
            if "not found" in saida.lower():
                # Esperado quando o slug de origem não existe no AtoM de carga.
                log(f"Slug '{slug}' não encontrado no AtoM de carga (csv:export).")
            else:
                log(f"ERRO: csv:export retornou {proc.returncode} para slug '{slug}'.")
            return None

        legacy: str | None = None
        for csv_exp in sorted(export_dir.rglob("*.csv")):
            try:
                with csv_exp.open("r", encoding="utf-8", newline="") as fh:
                    linhas = list(csv.reader(fh))
            except OSError:
                continue
            if len(linhas) < 2:
                continue
            cabecalho = linhas[0]
            if "legacyId" not in cabecalho:
                continue
            i_legacy = cabecalho.index("legacyId")
            i_slug = cabecalho.index("slug") if "slug" in cabecalho else -1
            for linha in linhas[1:]:
                # o export pode trazer descendentes: casa pela coluna slug quando existir
                if 0 <= i_slug < len(linha) and linha[i_slug].strip() and linha[i_slug].strip() != slug:
                    continue
                if i_legacy < len(linha) and linha[i_legacy].strip():
                    legacy = linha[i_legacy].strip()
                    break
            if legacy:
                break

        if not legacy:
            log(f"AVISO: legacyId não encontrado no export do slug '{slug}'.")
        return legacy
    except subprocess.TimeoutExpired:
        log(f"ERRO: csv:export excedeu o timeout de {COMMAND_TIMEOUT}s para slug '{slug}'.")
        return None
    except OSError as exc:
        log(f"ERRO: falha ao executar csv:export para slug '{slug}': {exc}")
        return None
    finally:
        shutil.rmtree(export_dir, ignore_errors=True)


def _ler_referencia_isad(arquivo: Path) -> tuple[str, str]:
    """Lê o `referenceCode` e o `identifier` da 1ª linha de dados do CSV ISAD."""
    try:
        with arquivo.open("r", encoding="utf-8", newline="") as fh:
            leitor = csv.reader(fh)
            cabecalho = next(leitor, [])
            primeira = next(leitor, [])
    except OSError as exc:
        log(f"ERRO: falha ao ler referência de {arquivo.name}: {exc}")
        return "", ""

    def campo(nome: str) -> str:
        if nome in cabecalho:
            i = cabecalho.index(nome)
            if i < len(primeira):
                return primeira[i].strip()
        return ""

    return campo("referenceCode"), campo("identifier")


def _buscar_slug_por_referencia(ref_completo: str) -> str | None:
    """Localiza no AtoM de CARGA (API REST) a descrição pelo reference_code e devolve o
    slug real (que o Archivematica gerou, ex.: `<uuid>-arquivo`).

    Busca POR CAMPO com o valor adequado a cada um: `referenceCode` usa o código COMPLETO
    (`BR DFPPT DFPPT-1-3CM-40`); `identifier` usa a FOLHA (último segmento, ex.: `40`) —
    pois no AtoM o campo `identifier` guarda só a folha. A confirmação é SEMPRE pelo
    reference_code completo (único). Retorna o 1º casamento não-ambíguo, ou None.
    """
    if not ATOM_API_BASE_URL or not ref_completo:
        return None
    folha = ref_completo.rsplit("-", 1)[-1].strip()
    for campo in ATOM_MATCH_FIELDS:
        valor_busca = folha if campo == "identifier" else ref_completo
        if not valor_busca:
            continue
        slug = _buscar_por_campo(valor_busca, campo, ref_completo)
        if slug:
            return slug
    return None


def _get_api_json(caminho: str):
    """GET JSON na API do AtoM de carga. `caminho` é relativo (ex.: `/api/informationobjects?...`).

    Retorna o dict/list parseado ou None em caso de erro (rede/HTTP/JSON).
    """
    url = f"{ATOM_API_BASE_URL}{caminho}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if ATOM_API_KEY:
        req.add_header("REST-API-Key", ATOM_API_KEY)

    contexto = None
    if url.lower().startswith("https") and not ATOM_API_VERIFY_TLS:
        contexto = ssl.create_default_context()
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=30, context=contexto) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        log(f"ERRO: falha na API do AtoM de carga ({caminho}): {exc}")
        return None


def _referencia_confere(detalhe: dict, referencia: str) -> bool:
    """True se algum campo de identificação do detalhe bate EXATO com `referencia`."""
    for campo in ("reference_code", "description_identifier", "identifier"):
        valor = detalhe.get(campo)
        if isinstance(valor, str) and valor.strip() == referencia:
            return True
    return False


def _buscar_por_campo(
    valor_busca: str, campo: str, valor_confirmacao: str
) -> str | None:
    """Busca boolean (`sq0=valor_busca&sf0=campo`) na API do AtoM de carga.

    O valor DE BUSCA depende do campo (código completo p/ `referenceCode`, folha p/
    `identifier`), mas a CONFIRMAÇÃO é sempre pelo `valor_confirmacao` (o reference_code
    completo, único) — via detalhe (`GET /api/informationobjects/{slug}`), pois a busca
    boolean pode ser fuzzy e a listagem não devolve o `identifier`.
    """
    params = urllib.parse.urlencode({"sq0": valor_busca, "sf0": campo, "limit": "50"})
    dados = _get_api_json(f"/api/informationobjects?{params}")
    if dados is None:
        return None

    resultados = dados.get("results") or []
    if not resultados:
        log(f"AVISO: nenhuma descrição no AtoM de carga para {campo}='{valor_busca}'.")
        return None

    # 1) casamento rápido: reference_code EXATO (=valor_confirmacao) já na listagem.
    exatos = [
        r.get("slug")
        for r in resultados
        if str(r.get("reference_code", "")).strip() == valor_confirmacao and r.get("slug")
    ]
    # 2) senão, confirma pelo detalhe (limitado a 10 candidatos para não estourar chamadas).
    if not exatos:
        for r in resultados[:10]:
            slug = r.get("slug")
            if not slug:
                continue
            detalhe = _get_api_json(f"/api/informationobjects/{urllib.parse.quote(str(slug))}")
            if isinstance(detalhe, dict) and _referencia_confere(detalhe, valor_confirmacao):
                exatos.append(slug)

    exatos = list(dict.fromkeys(exatos))  # dedup preservando ordem
    if len(exatos) == 1:
        log(
            f"Descrição localizada no AtoM de carga por {campo}='{valor_busca}' "
            f"(reference_code '{valor_confirmacao}'): slug '{exatos[0]}'."
        )
        return exatos[0]
    if len(exatos) > 1:
        log(
            f"AVISO: múltiplas descrições casam reference_code='{valor_confirmacao}' no AtoM "
            f"de carga ({exatos}); abortando por ambiguidade."
        )
        return None

    # Nenhum confirmou EXATO: se a busca retornou um único resultado, confia (o AtoM já filtrou
    # pelo campo `sf0`); caso contrário, ignora este campo (evita casar o item errado).
    if len(resultados) == 1 and resultados[0].get("slug"):
        slug = resultados[0]["slug"]
        log(
            f"Descrição localizada no AtoM de carga por {campo}='{valor_busca}' "
            f"(único resultado): slug '{slug}'."
        )
        return slug
    log(
        f"AVISO: {len(resultados)} resultado(s) para {campo}='{valor_busca}', nenhum confere "
        f"reference_code='{valor_confirmacao}'; ignorando este campo."
    )
    return None


def _descobrir_legacy_id(arquivo: Path) -> str | None:
    """Descobre o legacyId da descrição a atualizar no AtoM de carga.

    Localizador PRIMÁRIO = `reference_code`/`identifier` (único por descrição): o slug que o
    Archivematica gera no AtoM de carga vem do título/arquivo e NÃO é confiável (colide entre
    itens de mesmo título). Por isso:
    1) localiza pela referência na API do AtoM de carga (`_buscar_slug_por_referencia` → slug
       real → `_obter_legacy_id`);
    2) só como fallback (captura==carga na MESMA instância, ou API desligada) tenta pelo slug
       do CSV via `csv:export --single-slug`.
    """
    referencia, identifier = _ler_referencia_isad(arquivo)
    referencia = referencia or identifier
    slug_carga = _buscar_slug_por_referencia(referencia)
    if slug_carga:
        legacy = _obter_legacy_id(slug_carga)
        if legacy:
            return legacy

    # Fallback: slug do CSV (só resolve quando a descrição existe com esse slug na carga).
    slug = _slug_alvo(arquivo)
    if slug:
        return _obter_legacy_id(slug)
    return None


def _preparar_csv_para_update(arquivo: Path, legacy_id: str) -> bool:
    """Ajusta o CSV para o formato de atualização (`match-and-update`) do AtoM.

    - injeta o `legacyId` (id interno do AtoM) para que a descrição existente seja casada;
    - remove as colunas `referenceCode` e `slug` (não devem existir no update);
    - esvazia `parentId`, `digitalObjectURI` e `digitalObjectChecksum`;
    - preserva `qubitParentSlug` (slug do pai) e as demais colunas.

    Retorna False se o CSV não puder ser lido/gravado ou não tiver linha de dados.
    """
    try:
        with arquivo.open("r", encoding="utf-8", newline="") as fh:
            linhas = list(csv.reader(fh))
    except OSError as exc:
        log(f"ERRO: falha ao ler {arquivo.name} para preparar update: {exc}")
        return False

    if len(linhas) < 2:
        log(f"ERRO: {arquivo.name} sem linha de dados (esperado cabeçalho + 1 linha).")
        return False

    cabecalho = linhas[0]
    # Garante a coluna legacyId (o backend a emite, mas ficamos robustos se faltar).
    if "legacyId" not in cabecalho:
        cabecalho.insert(0, "legacyId")
        for linha in linhas[1:]:
            linha.insert(0, "")

    # Valores forçados por nome de coluna (legacyId injetado; campos de update esvaziados).
    forcar = {nome: "" for nome in COLUNAS_ESVAZIAR}
    forcar["legacyId"] = legacy_id

    idx_remover = {i for i, nome in enumerate(cabecalho) if nome in COLUNAS_REMOVER}
    idx_forcar = {i: forcar[nome] for i, nome in enumerate(cabecalho) if nome in forcar}
    idx_manter = [i for i in range(len(cabecalho)) if i not in idx_remover]

    novas_linhas = [[cabecalho[i] for i in idx_manter]]
    for linha in linhas[1:]:
        # normaliza linhas irregulares ao tamanho do cabeçalho
        while len(linha) < len(cabecalho):
            linha.append("")
        novas_linhas.append(
            [idx_forcar[i] if i in idx_forcar else linha[i] for i in idx_manter]
        )

    tmp = arquivo.with_name(arquivo.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            csv.writer(fh, lineterminator="\n").writerows(novas_linhas)
        tmp.replace(arquivo)  # troca atômica
    except OSError as exc:
        log(f"ERRO: falha ao gravar CSV preparado {arquivo.name}: {exc}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    removidas = [cabecalho[i] for i in sorted(idx_remover)]
    esvaziadas = [nome for nome in COLUNAS_ESVAZIAR if nome in cabecalho]
    log(
        f"CSV preparado para update em {arquivo.name} "
        f"(legacyId={legacy_id}; removidas: {removidas or '—'}; esvaziadas: {esvaziadas or '—'})."
    )
    return True


def _importar_csv(arquivo: Path) -> bool:
    comando = [PHP_BIN, "symfony", "csv:import"]
    if UPDATE_MODE:
        comando.append(f'--update={UPDATE_MODE}')
    # Pula registros que não casam (no modo update) em vez de criá-los: SÓ atualizamos
    # descrições já existentes (o DIP do Archivematica é quem cria). Sem parent padrão
    # (`--default-parent-slug`) — um registro só é tocado se for localizado; nada é criado
    # nem re-parentado por um pai default.
    comando.append("--skip-unmatched")
    # Modo não interativo: csv:import pede confirmação de backup do banco.
    comando.append("--no-confirmation")
    comando.append(str(arquivo))

    try:
        proc = _rodar(comando)
    except subprocess.TimeoutExpired:
        log(f"ERRO: csv:import excedeu o timeout de {COMMAND_TIMEOUT}s para {arquivo.name}")
        return False
    except OSError as exc:
        log(f"ERRO: falha ao executar csv:import para {arquivo.name}: {exc}")
        return False
    return proc.returncode == 0


def _reindexar() -> None:
    log("Atualizando índice do AtoM (cache clear + search:populate --update)...")
    try:
        _rodar([PHP_BIN, "symfony", "cc"])
        # --update: atualização incremental do índice (não purga/reconstrói tudo).
        _rodar([PHP_BIN, "symfony", "search:populate", "--update"])
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(f"AVISO: reindexação falhou: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Fluxo principal
# ─────────────────────────────────────────────────────────────────────────────


def processar() -> int:
    for d in (INCOMING_DIR, WORK_DIR, ERROR_DIR, RETRY_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # Novos arquivos (INCOMING_DIR, sujeitos à checagem de estabilidade) + reprocessos
    # pendentes do DIP (RETRY_DIR, já estáveis). O backend nomeia o ISAD pelo objeto
    # digital (ex.: `logo_2.png.csv`); só o rsync do backend deposita CSVs aqui, então
    # varremos todo `*.csv` (inclui os antigos `*_isad.csv`).
    novos = [a for a in INCOMING_DIR.glob("*.csv") if a.is_file()]
    reprocessos = [a for a in RETRY_DIR.glob("*.csv") if a.is_file()]
    candidatos = sorted(novos + reprocessos, key=lambda a: a.stat().st_mtime)

    if not candidatos:
        log("Nenhum arquivo *.csv para processar.")
        return 0

    importados = 0
    com_erro = 0
    houve_sucesso = False

    for arquivo in candidatos:
        # Estabilidade só se aplica aos NOVOS (rsync pode estar em curso); os de RETRY_DIR
        # já estão estáveis.
        if arquivo.parent == INCOMING_DIR and not _arquivo_estavel(arquivo):
            log(f"Pulando {arquivo.name} (modificado há < {STABLE_SECONDS}s — rsync em andamento?).")
            continue

        # Move para o diretório de trabalho antes de importar.
        em_trabalho = WORK_DIR / arquivo.name
        try:
            shutil.move(str(arquivo), str(em_trabalho))
        except OSError as exc:
            log(f"ERRO: não foi possível mover {arquivo.name} para work: {exc}")
            com_erro += 1
            continue

        # Descobre o legacyId da descrição já existente no AtoM de carga (pelo slug do
        # CSV e, se falhar, pela referência via API). Sem ele, match-and-update criaria
        # uma duplicata — então NÃO importamos.
        legacy_id = _descobrir_legacy_id(em_trabalho)
        if not legacy_id:
            # A descrição pode ainda não existir porque o DIP do Archivematica não concluiu.
            # Enquanto o arquivo for mais novo que RETRY_MAX_AGE_SECONDS, espera em RETRY_DIR
            # e é tentado de novo nas próximas execuções; depois disso, desiste (→ ERROR_DIR).
            idade = _idade_arquivo(em_trabalho)
            if idade is not None and idade < RETRY_MAX_AGE_SECONDS:
                _mover_para(em_trabalho, RETRY_DIR)
                log(
                    f"PENDENTE: legacyId não localizado para {em_trabalho.name} "
                    f"(idade {int(idade)}s < {RETRY_MAX_AGE_SECONDS}s) — aguardando o DIP do "
                    f"Archivematica; em RETRY_DIR para nova tentativa."
                )
            else:
                _mover_para(em_trabalho, ERROR_DIR)
                log(
                    f"ERRO: legacyId não localizado para {em_trabalho.name} após "
                    f"~{RETRY_MAX_AGE_SECONDS}s de espera — desistindo (movido para {ERROR_DIR})."
                )
                com_erro += 1
            continue

        # Ajusta o CSV para o formato de atualização (injeta legacyId, remove
        # referenceCode/slug, esvazia parentId e os campos de objeto digital).
        if not _preparar_csv_para_update(em_trabalho, legacy_id):
            destino_erro = ERROR_DIR / em_trabalho.name
            try:
                shutil.move(str(em_trabalho), str(destino_erro))
            except OSError as exc:
                log(f"ERRO: falha ao mover {em_trabalho.name} para error: {exc}")
            log(
                f"ERRO: não foi possível preparar {em_trabalho.name} para update "
                f"(movido para {ERROR_DIR})."
            )
            com_erro += 1
            continue

        log(f"Importando {em_trabalho.name}...")
        sucesso = _importar_csv(em_trabalho)

        if sucesso:
            # Confirmada a carga no AtoM → EXCLUIR o arquivo.
            try:
                em_trabalho.unlink()
                log(f"OK: {em_trabalho.name} importado e excluído.")
            except OSError as exc:
                log(f"AVISO: importado, mas falha ao excluir {em_trabalho.name}: {exc}")
            importados += 1
            houve_sucesso = True
        else:
            destino_erro = ERROR_DIR / em_trabalho.name
            try:
                shutil.move(str(em_trabalho), str(destino_erro))
            except OSError as exc:
                log(f"ERRO: falha ao mover {em_trabalho.name} para error: {exc}")
            log(f"ERRO: importação falhou para {em_trabalho.name} (movido para {ERROR_DIR}).")
            com_erro += 1

    if houve_sucesso and RUN_INDEX:
        _reindexar()

    log(f"Concluído. Importados: {importados}, com erro: {com_erro}.")
    return 1 if com_erro else 0


def main() -> int:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = LOCK_FILE.open("w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("Outra execução já está em andamento (lock). Saindo.")
        return 0

    try:
        return processar()
    finally:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


if __name__ == "__main__":
    sys.exit(main())
