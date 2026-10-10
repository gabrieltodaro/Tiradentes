"""Test stubs for crawler tools."""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.tools import ToolException

from app.agents.tools import crawler

PEP_CSV = """CPF;Nome;Descrição Função;Nome Órgão;Data Início Exercício
12345678900;JOAO DA SILVA;Deputado Federal;CAMARA DOS DEPUTADOS;2023-01-01
"""

TSE_CANDIDATOS_CSV = """NR_CPF_CANDIDATO;NM_CANDIDATO;NM_URNA_CANDIDATO;DS_CARGO;SG_PARTIDO;SG_UF;ANO_ELEICAO;DS_SITUACAO_CANDIDATURA
12345678900;JOAO DA SILVA;JOAO SILVA;DEPUTADO FEDERAL;ABC;DF;2024;APTO
"""

TSE_BENS_CSV = """NR_CPF_CANDIDATO;DS_BEM_CANDIDATO;DS_TIPO_BEM_CANDIDATO;VR_BEM_CANDIDATO;ANO_ELEICAO
12345678900;APARTAMENTO;IMOVEL;500000,00;2024
"""

CEIS_CSV = """CPF_CNPJ;Nome Sancionado;Órgão Sancionador;Categoria Sanção;Data Início Sanção;Data Fim Sanção
12345678900;JOAO DA SILVA;CGU;DECLARACAO INIDONEIDADE;2023-01-10;2025-01-10
"""

CNEP_CSV = """CPF_CNPJ;Nome Sancionado;Órgão Sancionador;Categoria Sanção;Data Início Sanção;Data Fim Sanção
12345678900;JOAO DA SILVA;CGU;MULTA;2024-02-01;2026-02-01
"""


def test_fetch_pep_csv_returns_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_download() -> tuple[bytes, str, str]:
        return PEP_CSV.encode("utf-8"), "202601", "https://example.test/202601_PEP.csv"

    monkeypatch.setattr(crawler, "_download_latest_pep_bytes", fake_download)

    result = asyncio.run(crawler.fetch_pep_csv.ainvoke({"cpf": "123.456.789-00"}))
    assert result["found"] is True
    assert result["entries"]
    assert result["entries"][0]["nome"] == "JOAO DA SILVA"
    assert result["entries"][0]["funcao"] == "Deputado Federal"
    assert result["period"] == "202601"


def test_fetch_tse_candidatos_returns_candidaturas_and_bens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_download_tse_dataset(
        dataset: str,
        year: int,
    ) -> tuple[bytes, str]:
        if dataset == "candidatos":
            return (
                TSE_CANDIDATOS_CSV.encode("utf-8"),
                f"https://example.test/consulta_cand_{year}.csv",
            )
        return (
            TSE_BENS_CSV.encode("utf-8"),
            f"https://example.test/bem_candidato_{year}.csv",
        )

    monkeypatch.setattr(crawler, "_recent_tse_years", lambda: [2024])
    monkeypatch.setattr(crawler, "_download_tse_dataset", fake_download_tse_dataset)

    result = asyncio.run(crawler.fetch_tse_candidatos.ainvoke({"cpf": "12345678900"}))

    assert result["found"] is True
    assert len(result["candidaturas"]) == 1
    assert len(result["bens"]) == 1
    assert result["candidaturas"][0]["cargo"] == "DEPUTADO FEDERAL"


def test_fetch_cgu_sancoes_returns_ceis_and_cnep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_download(dataset: str) -> tuple[bytes, str]:
        if dataset == "ceisp":
            return CEIS_CSV.encode("utf-8"), "https://example.test/ceis.csv"
        return CNEP_CSV.encode("utf-8"), "https://example.test/cnep.csv"

    monkeypatch.setattr(crawler, "_download_cgu_sancoes_dataset", fake_download)

    result = asyncio.run(crawler.fetch_cgu_sancoes.ainvoke({"cpf": "12345678900"}))

    assert result["found"] is True
    assert len(result["ceisp"]) == 1
    assert len(result["cnep"]) == 1
    assert result["ceisp"][0]["tipo"] == "CEIS"
    assert result["cnep"][0]["tipo"] == "CNEP"


def test_fetch_dou_nomeacoes_stub() -> None:
    result = asyncio.run(crawler.fetch_dou_nomeacoes.ainvoke({"cpf": "12345678900"}))
    assert result["found"] is False
    assert result["nomeacoes"] == []


def test_fetch_pep_csv_raises_for_invalid_cpf() -> None:
    with pytest.raises(ToolException):
        asyncio.run(crawler.fetch_pep_csv.ainvoke({"cpf": "123"}))
