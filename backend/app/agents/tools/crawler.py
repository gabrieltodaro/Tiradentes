"""Crawler tools for multi-source PEP lookup in the MVP."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import aiofiles
import httpx
import pandas as pd
from dateutil.relativedelta import relativedelta
from langchain_core.tools import ToolException, tool
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 45
RETRY_ATTEMPTS = 3
LOOKBACK_MONTHS = 8
CSV_CHUNK_SIZE = 100_000

CSV_ENCODINGS = ("utf-8-sig", "latin-1", "cp1252")
CSV_SEPARATORS = (";", ",")

SOURCE_PEP = "Portal da Transparência (PEP)"
SOURCE_TSE = "TSE Dados Abertos (Candidaturas e Bens)"
SOURCE_CGU_SANCOES = "CGU CEIS/CNEP"
SOURCE_DOU = "Diário Oficial da União (stub)"

BASE_PEP_URL = "https://portaldatransparencia.gov.br/download-de-dados/pep"
BASE_CEIS_URL = "https://portaldatransparencia.gov.br/download-de-dados/ceis"
BASE_CNEP_URL = "https://portaldatransparencia.gov.br/download-de-dados/cnep"

TSE_CANDIDATOS_URLS = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_{year}_BRASIL.csv",
    "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_{year}_BRASIL.zip",
)
TSE_BENS_URLS = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/bem_candidato/bem_candidato_{year}_BRASIL.csv",
    "https://cdn.tse.jus.br/estatistica/sead/odsele/bem_candidato/bem_candidato_{year}_BRASIL.zip",
)

# Local file cache for downloads. TTL/refresh logic can be centralized later.
CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "sources"


class PEPEntry(BaseModel):
    cpf: str = ""
    nome: str = ""
    funcao: str = ""
    orgao: str = ""
    data_inicio: str = ""
    data_fim_exercicio: str = ""
    data_fim_carencia: str = ""


class TSECandidatura(BaseModel):
    cpf: str = ""
    nome: str = ""
    nome_urna: str = ""
    cargo: str = ""
    partido: str = ""
    uf: str = ""
    ano_eleicao: str = ""
    situacao: str = ""


class TSEBem(BaseModel):
    cpf: str = ""
    descricao: str = ""
    tipo: str = ""
    valor: str = ""
    ano_eleicao: str = ""


class SancaoEntry(BaseModel):
    cpf: str = ""
    nome: str = ""
    orgao: str = ""
    categoria: str = ""
    data_inicio: str = ""
    data_fim: str = ""
    tipo: str = ""


class PEPFetchResult(BaseModel):
    source: str = SOURCE_PEP
    found: bool = False
    period: str | None = None
    url: str | None = None
    entries: list[PEPEntry] = Field(default_factory=list)
    error: str | None = None


class TSEFetchResult(BaseModel):
    source: str = SOURCE_TSE
    found: bool = False
    candidaturas: list[TSECandidatura] = Field(default_factory=list)
    bens: list[TSEBem] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    error: str | None = None


class CGUSancoesResult(BaseModel):
    source: str = SOURCE_CGU_SANCOES
    found: bool = False
    ceisp: list[SancaoEntry] = Field(default_factory=list)
    cnep: list[SancaoEntry] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    error: str | None = None


class DOUFetchResult(BaseModel):
    source: str = SOURCE_DOU
    found: bool = False
    nomeacoes: list[dict[str, str]] = Field(default_factory=list)
    error: str | None = None


def clean_cpf(cpf: str) -> str:
    """Normalize CPF to digits only for exact joins across sources."""
    return re.sub(r"\D", "", cpf or "")


def normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_only.upper().split())


def _normalize_column_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(name))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return "".join(char for char in ascii_only.upper() if char.isalnum())


def _pick_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized_map = {_normalize_column_name(column): column for column in columns}
    for alias in aliases:
        candidate = normalized_map.get(_normalize_column_name(alias))
        if candidate:
            return candidate
    return None


def _recent_periods(months: int = LOOKBACK_MONTHS) -> list[str]:
    current = datetime.now(tz=timezone.utc).date().replace(day=1)
    return [
        (current - relativedelta(months=offset)).strftime("%Y%m")
        for offset in range(months)
    ]


def _recent_tse_years() -> list[int]:
    # Hardcoded known open-election datasets for MVP; update manually later.
    return [2024, 2022, 2020]


def _candidate_urls(base_url: str, basename: str) -> list[str]:
    return [f"{base_url}/{basename}", f"{base_url}/{basename[:6]}/{basename}"]


async def _read_cached_bytes(cache_key: str) -> bytes | None:
    path = CACHE_DIR / cache_key
    if not path.exists():
        return None

    async with aiofiles.open(path, "rb") as file:
        content = await file.read()
    return content or None


async def _write_cached_bytes(cache_key: str, content: bytes) -> None:
    path = CACHE_DIR / cache_key
    path.parent.mkdir(parents=True, exist_ok=True)

    async with aiofiles.open(path, "wb") as file:
        await file.write(content)


@retry(
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError, ToolException)),
    reraise=True,
)
async def _download_url_with_retry(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url, follow_redirects=True)
    response.raise_for_status()
    if not response.content:
        raise ToolException(f"Empty payload from {url}")
    return response.content


async def _download_first_available(urls: list[str], cache_key: str) -> tuple[bytes, str]:
    cached = await _read_cached_bytes(cache_key)
    if cached is not None:
        logger.info("Using cached download for %s", cache_key)
        return cached, f"cache://{cache_key}"

    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=timeout, http2=True) as client:
        for url in urls:
            try:
                content = await _download_url_with_retry(client, url)
                await _write_cached_bytes(cache_key, content)
                logger.info("Downloaded source file from %s", url)
                return content, url
            except Exception as exc:  # noqa: BLE001 - capture and try next URL.
                logger.warning("Download failed for %s: %s", url, exc)
                errors.append(f"{url}: {exc}")

    message = "; ".join(errors[-3:]) if errors else "no URL candidates"
    raise ToolException(f"No working download URL found ({message})")


def _extract_csv_from_zip(raw_content: bytes, preferred_name: str | None = None) -> bytes:
    if not raw_content.startswith(b"PK"):
        return raw_content

    with zipfile.ZipFile(io.BytesIO(raw_content)) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ToolException("ZIP source does not contain CSV files.")

        if preferred_name:
            for name in csv_names:
                if preferred_name.lower() in name.lower():
                    with archive.open(name) as csv_file:
                        return csv_file.read()

        with archive.open(csv_names[0]) as csv_file:
            return csv_file.read()


def _find_rows_by_cpf(
    csv_content: bytes,
    normalized_cpf: str,
    cpf_aliases: tuple[str, ...],
) -> pd.DataFrame:
    parse_errors: list[str] = []

    for encoding in CSV_ENCODINGS:
        try:
            text = csv_content.decode(encoding)
        except UnicodeDecodeError as exc:
            parse_errors.append(f"encoding={encoding}: {exc}")
            continue

        for separator in CSV_SEPARATORS:
            try:
                reader = pd.read_csv(
                    io.StringIO(text),
                    sep=separator,
                    dtype=str,
                    chunksize=CSV_CHUNK_SIZE,
                    low_memory=False,
                )

                matched_chunks: list[pd.DataFrame] = []
                seen_columns: list[str] = []
                cpf_column: str | None = None

                for chunk in reader:
                    chunk = chunk.fillna("")
                    if not seen_columns:
                        seen_columns = [str(column) for column in chunk.columns]
                        cpf_column = _pick_column(seen_columns, cpf_aliases)
                        if not cpf_column:
                            raise ValueError(
                                "CSV parsed but CPF column could not be identified."
                            )

                    cpf_series = (
                        chunk[cpf_column]
                        .astype(str)
                        .str.replace(r"\D", "", regex=True)
                        .str.strip()
                    )
                    selected = chunk.loc[cpf_series == normalized_cpf]
                    if not selected.empty:
                        matched_chunks.append(selected.copy())

                if matched_chunks:
                    return pd.concat(matched_chunks, ignore_index=True)

                return pd.DataFrame(columns=seen_columns)
            except (pd.errors.ParserError, ValueError) as exc:
                parse_errors.append(f"encoding={encoding} sep={separator}: {exc}")

    errors = "; ".join(parse_errors[-3:]) if parse_errors else "parse failure"
    raise ToolException(f"Unable to parse CSV payload ({errors})")


def _value(row: dict[str, Any], columns: list[str], aliases: tuple[str, ...]) -> str:
    column = _pick_column(columns, aliases)
    if not column:
        return ""
    return str(row.get(column, "")).strip()


async def _download_latest_pep_bytes() -> tuple[bytes, str, str]:
    errors: list[str] = []

    for period in _recent_periods():
        for suffix in ("PEP.csv", "Dirigentes.csv"):
            basename = f"{period}_{suffix}"
            urls = _candidate_urls(BASE_PEP_URL, basename)

            try:
                content, url = await _download_first_available(
                    urls=urls,
                    cache_key=f"pep/{basename}",
                )
                return _extract_csv_from_zip(content, preferred_name="PEP"), period, url
            except ToolException as exc:
                errors.append(str(exc))

    message = "; ".join(errors[-3:]) if errors else "no periods attempted"
    raise ToolException(f"Unable to download PEP CSV ({message})")


async def _download_tse_dataset(
    dataset: Literal["candidatos", "bens"],
    year: int,
) -> tuple[bytes, str]:
    templates = TSE_CANDIDATOS_URLS if dataset == "candidatos" else TSE_BENS_URLS
    urls = [template.format(year=year) for template in templates]

    content, url = await _download_first_available(
        urls=urls,
        cache_key=f"tse/{dataset}_{year}.bin",
    )
    preferred_name = "consulta_cand" if dataset == "candidatos" else "bem_candidato"
    return _extract_csv_from_zip(content, preferred_name=preferred_name), url


async def _download_cgu_sancoes_dataset(
    dataset: Literal["ceisp", "cnep"],
) -> tuple[bytes, str]:
    base_url = BASE_CEIS_URL if dataset == "ceisp" else BASE_CNEP_URL
    suffix = "CEIS.csv" if dataset == "ceisp" else "CNEP.csv"
    errors: list[str] = []

    for period in _recent_periods():
        basename = f"{period}_{suffix}"
        zipped_name = basename.replace(".csv", ".zip")
        urls = _candidate_urls(base_url, basename) + _candidate_urls(base_url, zipped_name)

        try:
            content, url = await _download_first_available(
                urls=urls,
                cache_key=f"sancoes/{dataset}_{period}.bin",
            )
            preferred_name = "CEIS" if dataset == "ceisp" else "CNEP"
            return _extract_csv_from_zip(content, preferred_name=preferred_name), url
        except ToolException as exc:
            errors.append(str(exc))

    message = "; ".join(errors[-3:]) if errors else "no periods attempted"
    raise ToolException(f"Unable to download {dataset.upper()} dataset ({message})")


def _parse_pep_entries(rows: pd.DataFrame, normalized_cpf: str) -> list[PEPEntry]:
    entries: list[PEPEntry] = []
    columns = [str(column) for column in rows.columns]

    for row in rows.to_dict(orient="records"):
        funcao = _value(
            row,
            columns,
            ("Descrição Função", "Descricao Funcao", "DS_FUNCAO", "FUNCAO"),
        )
        entries.append(
            PEPEntry(
                cpf=normalized_cpf,
                nome=normalize_name(_value(row, columns, ("Nome", "NM_PESSOA"))),
                funcao=funcao,
                orgao=_value(row, columns, ("Nome Órgão", "Nome Orgao", "Orgao")),
                data_inicio=_value(
                    row, columns, ("Data Início Exercício", "Data Inicio Exercicio")
                ),
                data_fim_exercicio=_value(
                    row, columns, ("Data Fim Exercício", "Data Fim Exercicio")
                ),
                data_fim_carencia=_value(
                    row, columns, ("Data Fim Carência", "Data Fim Carencia")
                ),
            )
        )

    return entries


def _parse_tse_candidaturas(rows: pd.DataFrame, normalized_cpf: str) -> list[TSECandidatura]:
    candidaturas: list[TSECandidatura] = []
    columns = [str(column) for column in rows.columns]

    for row in rows.to_dict(orient="records"):
        candidaturas.append(
            TSECandidatura(
                cpf=normalized_cpf,
                nome=normalize_name(_value(row, columns, ("NM_CANDIDATO", "NOME_CANDIDATO"))),
                nome_urna=normalize_name(
                    _value(row, columns, ("NM_URNA_CANDIDATO", "NOME_URNA"))
                ),
                cargo=_value(row, columns, ("DS_CARGO", "CARGO")),
                partido=_value(row, columns, ("SG_PARTIDO", "PARTIDO")),
                uf=_value(row, columns, ("SG_UF", "UF")),
                ano_eleicao=_value(row, columns, ("ANO_ELEICAO", "NR_ANO_ELEICAO")),
                situacao=_value(
                    row,
                    columns,
                    ("DS_SITUACAO_CANDIDATURA", "SITUACAO_CANDIDATURA"),
                ),
            )
        )

    return candidaturas


def _parse_tse_bens(rows: pd.DataFrame, normalized_cpf: str) -> list[TSEBem]:
    bens: list[TSEBem] = []
    columns = [str(column) for column in rows.columns]

    for row in rows.to_dict(orient="records"):
        bens.append(
            TSEBem(
                cpf=normalized_cpf,
                descricao=_value(row, columns, ("DS_BEM_CANDIDATO", "DESCRICAO_BEM")),
                tipo=_value(
                    row,
                    columns,
                    ("DS_TIPO_BEM_CANDIDATO", "DS_GRUPO_BEM_CANDIDATO", "TIPO_BEM"),
                ),
                valor=_value(row, columns, ("VR_BEM_CANDIDATO", "VALOR_BEM")),
                ano_eleicao=_value(row, columns, ("ANO_ELEICAO", "NR_ANO_ELEICAO")),
            )
        )

    return bens


def _parse_sancoes(
    rows: pd.DataFrame,
    normalized_cpf: str,
    tipo: Literal["CEIS", "CNEP"],
) -> list[SancaoEntry]:
    entries: list[SancaoEntry] = []
    columns = [str(column) for column in rows.columns]

    for row in rows.to_dict(orient="records"):
        entries.append(
            SancaoEntry(
                cpf=normalized_cpf,
                nome=normalize_name(
                    _value(
                        row,
                        columns,
                        (
                            "Nome Sancionado",
                            "NOME_SANCIONADO",
                            "Nome Pessoa Física Sancionada",
                        ),
                    )
                ),
                orgao=_value(
                    row,
                    columns,
                    ("Órgão Sancionador", "Orgao Sancionador", "NOME_ORGAO"),
                ),
                categoria=_value(
                    row,
                    columns,
                    ("Categoria Sanção", "Categoria Sancao", "TIPO_SANCAO"),
                ),
                data_inicio=_value(
                    row,
                    columns,
                    ("Data Início Sanção", "Data Inicio Sancao", "DATA_INICIO_SANCAO"),
                ),
                data_fim=_value(
                    row, columns, ("Data Fim Sanção", "Data Fim Sancao", "DATA_FIM_SANCAO")
                ),
                tipo=tipo,
            )
        )

    return entries


@tool
async def fetch_pep_csv(cpf: str) -> dict[str, Any]:
    """Fetch and search CGU PEP CSV by CPF."""
    normalized_cpf = clean_cpf(cpf)
    if len(normalized_cpf) != 11:
        raise ToolException("CPF must contain exactly 11 digits.")

    try:
        csv_content, period, url = await _download_latest_pep_bytes()
        rows = _find_rows_by_cpf(csv_content, normalized_cpf, ("CPF",))
        entries = _parse_pep_entries(rows, normalized_cpf)
        result = PEPFetchResult(
            found=bool(entries),
            period=period,
            url=url,
            entries=entries,
        )
        return result.model_dump(mode="json")
    except ToolException:
        raise
    except Exception as exc:  # noqa: BLE001 - tool should always return ToolException.
        logger.exception("Unexpected error while fetching PEP CSV: %s", exc)
        raise ToolException("Unexpected failure while processing PEP CSV.") from exc


@tool
async def fetch_tse_candidatos(cpf: str) -> dict[str, Any]:
    """Fetch TSE candidaturas and bens datasets and filter by CPF."""
    normalized_cpf = clean_cpf(cpf)
    if len(normalized_cpf) != 11:
        raise ToolException("CPF must contain exactly 11 digits.")

    candidaturas: list[TSECandidatura] = []
    bens: list[TSEBem] = []
    urls_used: list[str] = []
    errors: list[str] = []
    successful_downloads = 0

    for year in _recent_tse_years():
        cand_result, bens_result = await asyncio.gather(
            _download_tse_dataset("candidatos", year),
            _download_tse_dataset("bens", year),
            return_exceptions=True,
        )

        if isinstance(cand_result, Exception):
            errors.append(f"candidatos/{year}: {cand_result}")
        else:
            successful_downloads += 1
            cand_content, cand_url = cand_result
            rows = _find_rows_by_cpf(
                cand_content,
                normalized_cpf,
                ("NR_CPF_CANDIDATO", "CPF_CANDIDATO", "CPF"),
            )
            candidaturas.extend(_parse_tse_candidaturas(rows, normalized_cpf))
            urls_used.append(cand_url)

        if isinstance(bens_result, Exception):
            errors.append(f"bens/{year}: {bens_result}")
        else:
            successful_downloads += 1
            bens_content, bens_url = bens_result
            rows = _find_rows_by_cpf(
                bens_content,
                normalized_cpf,
                ("NR_CPF_CANDIDATO", "CPF_CANDIDATO", "CPF"),
            )
            bens.extend(_parse_tse_bens(rows, normalized_cpf))
            urls_used.append(bens_url)

    if successful_downloads == 0:
        message = "; ".join(errors[-3:]) if errors else "no download succeeded"
        raise ToolException(f"Unable to fetch TSE datasets ({message})")

    result = TSEFetchResult(
        found=bool(candidaturas or bens),
        candidaturas=candidaturas,
        bens=bens,
        urls=urls_used,
        error="; ".join(errors[-2:]) if errors else None,
    )
    return result.model_dump(mode="json")


@tool
async def fetch_cgu_sancoes(cpf: str) -> dict[str, Any]:
    """Fetch CEIS/CNEP open data and filter sanction entries by CPF."""
    normalized_cpf = clean_cpf(cpf)
    if len(normalized_cpf) != 11:
        raise ToolException("CPF must contain exactly 11 digits.")

    ceis_result, cnep_result = await asyncio.gather(
        _download_cgu_sancoes_dataset("ceisp"),
        _download_cgu_sancoes_dataset("cnep"),
        return_exceptions=True,
    )

    ceisp: list[SancaoEntry] = []
    cnep: list[SancaoEntry] = []
    urls_used: list[str] = []
    errors: list[str] = []
    successful_downloads = 0

    if isinstance(ceis_result, Exception):
        errors.append(f"CEIS: {ceis_result}")
    else:
        successful_downloads += 1
        ceis_content, ceis_url = ceis_result
        ceis_rows = _find_rows_by_cpf(
            ceis_content,
            normalized_cpf,
            ("CPF_CNPJ", "CPF_CNPJ_SANCIONADO", "CPF"),
        )
        ceisp.extend(_parse_sancoes(ceis_rows, normalized_cpf, "CEIS"))
        urls_used.append(ceis_url)

    if isinstance(cnep_result, Exception):
        errors.append(f"CNEP: {cnep_result}")
    else:
        successful_downloads += 1
        cnep_content, cnep_url = cnep_result
        cnep_rows = _find_rows_by_cpf(
            cnep_content,
            normalized_cpf,
            ("CPF_CNPJ", "CPF_CNPJ_SANCIONADO", "CPF"),
        )
        cnep.extend(_parse_sancoes(cnep_rows, normalized_cpf, "CNEP"))
        urls_used.append(cnep_url)

    if successful_downloads == 0:
        message = "; ".join(errors[-3:]) if errors else "no download succeeded"
        raise ToolException(f"Unable to fetch CEIS/CNEP datasets ({message})")

    result = CGUSancoesResult(
        found=bool(ceisp or cnep),
        ceisp=ceisp,
        cnep=cnep,
        urls=urls_used,
        error="; ".join(errors[-2:]) if errors else None,
    )
    return result.model_dump(mode="json")


@tool
async def fetch_dou_nomeacoes(cpf: str) -> dict[str, Any]:
    """
    Stub for DOU crawl.

    Kept as tool boundary now so a real crawler can replace implementation later.
    """
    normalized_cpf = clean_cpf(cpf)
    if len(normalized_cpf) != 11:
        raise ToolException("CPF must contain exactly 11 digits.")

    result = DOUFetchResult(
        found=False,
        nomeacoes=[],
        error="DOU crawler is not implemented in this MVP step.",
    )
    return result.model_dump(mode="json")


__all__ = [
    "CGUSancoesResult",
    "DOUFetchResult",
    "PEPEntry",
    "PEPFetchResult",
    "SancaoEntry",
    "TSEBem",
    "TSECandidatura",
    "TSEFetchResult",
    "clean_cpf",
    "fetch_cgu_sancoes",
    "fetch_dou_nomeacoes",
    "fetch_pep_csv",
    "fetch_tse_candidatos",
    "normalize_name",
]
