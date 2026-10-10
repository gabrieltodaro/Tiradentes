"""LangGraph definition for multi-source CPF aggregation before verification."""

from __future__ import annotations

import ast
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, TypedDict

import aiofiles
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, ToolException
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from app.agents.tools.crawler import (
    fetch_cgu_sancoes,
    fetch_dou_nomeacoes,
    fetch_pep_csv,
    fetch_tse_candidatos,
    normalize_name,
)

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "cpf-cache"
CACHE_TTL = timedelta(hours=24)

TOOL_REGISTRY: dict[str, BaseTool] = {
    "fetch_pep_csv": fetch_pep_csv,
    "fetch_tse_candidatos": fetch_tse_candidatos,
    "fetch_cgu_sancoes": fetch_cgu_sancoes,
    "fetch_dou_nomeacoes": fetch_dou_nomeacoes,
}

# Bound tool node for LangGraph-native tool execution.
TOOLS_NODE = ToolNode(list(TOOL_REGISTRY.values()))

SOURCE_TOOL_MAP = {
    "pep": "fetch_pep_csv",
    "tse": "fetch_tse_candidatos",
    "sancoes": "fetch_cgu_sancoes",
    "dou": "fetch_dou_nomeacoes",
}

SOURCE_FALLBACK_NAME = {
    "pep": "Portal da Transparência (PEP)",
    "tse": "TSE Dados Abertos (Candidaturas e Bens)",
    "sancoes": "CGU CEIS/CNEP",
    "dou": "Diário Oficial da União (stub)",
}


def _merge_dicts(
    current: dict[str, Any] | None, incoming: dict[str, Any] | None
) -> dict[str, Any]:
    merged = dict(current or {})
    merged.update(incoming or {})
    return merged


def _merge_lists(current: list[str] | None, incoming: list[str] | None) -> list[str]:
    return [*(current or []), *(incoming or [])]


class GraphState(TypedDict, total=False):
    """Shared graph state. Reducers support fan-out crawl joins."""

    cpf: str
    valid: bool
    cache_hit: bool
    cache_response: dict[str, Any]
    sources_results: Annotated[dict[str, dict[str, Any]], _merge_dicts]
    source_errors: Annotated[list[str], _merge_lists]
    response: dict[str, Any]


def _is_valid_cpf(cpf: str) -> bool:
    if len(cpf) != 11 or not cpf.isdigit():
        return False
    if cpf == cpf[0] * 11:
        return False

    first_sum = sum(int(cpf[index]) * (10 - index) for index in range(9))
    first_digit = (first_sum * 10) % 11
    first_digit = 0 if first_digit == 10 else first_digit
    if first_digit != int(cpf[9]):
        return False

    second_sum = sum(int(cpf[index]) * (11 - index) for index in range(10))
    second_digit = (second_sum * 10) % 11
    second_digit = 0 if second_digit == 10 else second_digit
    return second_digit == int(cpf[10])


def _normalize_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "nome": str(payload.get("nome", "") or ""),
        "cargos": list(payload.get("cargos", []) or []),
        "fontes": list(payload.get("fontes", []) or []),
        "irregularidades": list(payload.get("irregularidades", []) or []),
        "sancoes": list(payload.get("sancoes", []) or []),
        "bens": list(payload.get("bens", []) or []),
        "is_pep": bool(payload.get("is_pep", False)),
    }


def _parse_tool_message_content(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content

    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        try:
            parsed = ast.literal_eval(content)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, SyntaxError):
            pass

    raise ToolException("ToolNode returned an unsupported content payload.")


async def _run_tool(tool_name: str, cpf: str) -> dict[str, Any]:
    """
    Execute one tool through ToolNode.

    Falls back to direct tool invocation to keep MVP resilient if ToolNode format
    changes with dependency versions.
    """
    tool = TOOL_REGISTRY[tool_name]

    tool_call = {
        "name": tool_name,
        "args": {"cpf": cpf},
        "id": f"{tool_name}-{cpf[-4:]}",
        "type": "tool_call",
    }
    message = AIMessage(content="", tool_calls=[tool_call])

    try:
        result = await TOOLS_NODE.ainvoke({"messages": [message]})
        messages = result.get("messages", [])
        if not messages:
            raise ToolException("ToolNode returned no tool messages.")

        last_message = messages[-1]
        if not isinstance(last_message, ToolMessage):
            raise ToolException("ToolNode did not emit a ToolMessage.")
        return _parse_tool_message_content(last_message.content)
    except Exception as exc:  # noqa: BLE001 - fallback below.
        logger.warning("ToolNode call failed for %s: %s. Falling back to direct tool.", tool_name, exc)
        direct_payload = await tool.ainvoke({"cpf": cpf})
        if not isinstance(direct_payload, dict):
            raise ToolException(f"Direct tool payload is invalid for {tool_name}.")
        return direct_payload


async def validate_cpf_node(state: GraphState) -> GraphState:
    return {"valid": _is_valid_cpf(state.get("cpf", ""))}


async def check_cache_node(state: GraphState) -> GraphState:
    cpf = state.get("cpf", "")
    cache_file = CACHE_DIR / f"{cpf}.json"
    if not cache_file.exists():
        return {"cache_hit": False, "cache_response": {}}

    try:
        async with aiofiles.open(cache_file, "r", encoding="utf-8") as file:
            raw = await file.read()
        payload = json.loads(raw)
        created_at = datetime.fromisoformat(payload.get("created_at"))
        if datetime.now(tz=timezone.utc) - created_at > CACHE_TTL:
            return {"cache_hit": False, "cache_response": {}}

        return {
            "cache_hit": True,
            "cache_response": payload.get("response", {}),
            "sources_results": payload.get("sources_results", {}),
        }
    except Exception as exc:  # noqa: BLE001 - cache miss fallback.
        logger.warning("Failed to read cache for CPF %s: %s", cpf, exc)
        return {"cache_hit": False, "cache_response": {}}


async def dispatch_parallel_crawls_node(_: GraphState) -> GraphState:
    """Fan-out dispatcher node. Successors execute in parallel."""
    return {}


async def _crawl_source(state: GraphState, source_key: str) -> GraphState:
    cpf = state.get("cpf", "")
    tool_name = SOURCE_TOOL_MAP[source_key]
    fallback_source_name = SOURCE_FALLBACK_NAME[source_key]

    try:
        payload = await _run_tool(tool_name, cpf)
        payload.setdefault("source", fallback_source_name)
        payload.setdefault("found", False)
        return {"sources_results": {source_key: payload}}
    except Exception as exc:  # noqa: BLE001 - source failures should not break graph.
        logger.warning("Source %s failed for CPF %s: %s", source_key, cpf, exc)
        return {
            "sources_results": {
                source_key: {
                    "source": fallback_source_name,
                    "found": False,
                    "error": str(exc),
                }
            },
            "source_errors": [f"{source_key}: {exc}"],
        }


async def crawl_pep_node(state: GraphState) -> GraphState:
    return await _crawl_source(state, "pep")


async def crawl_tse_node(state: GraphState) -> GraphState:
    return await _crawl_source(state, "tse")


async def crawl_sancoes_node(state: GraphState) -> GraphState:
    return await _crawl_source(state, "sancoes")


async def crawl_dou_node(state: GraphState) -> GraphState:
    return await _crawl_source(state, "dou")


async def aggregate_sources_node(state: GraphState) -> GraphState:
    if not state.get("valid", False):
        return {
            "response": _normalize_response(
                {
                    "nome": "",
                    "cargos": [],
                    "fontes": [],
                    "irregularidades": ["CPF inválido com base na regra de checksum."],
                    "sancoes": [],
                    "bens": [],
                    "is_pep": False,
                }
            )
        }

    if state.get("cache_hit", False) and state.get("cache_response"):
        return {"response": _normalize_response(state.get("cache_response", {}))}

    sources_results = state.get("sources_results", {})
    pep = sources_results.get("pep", {})
    tse = sources_results.get("tse", {})
    sancoes_source = sources_results.get("sancoes", {})
    dou = sources_results.get("dou", {})

    names: list[str] = []
    cargos: set[str] = set()
    fontes: list[str] = []
    bens: list[dict[str, Any]] = list(tse.get("bens", []) or [])
    sancoes: list[dict[str, Any]] = []
    irregularidades: list[str] = []

    for source in (pep, tse, sancoes_source, dou):
        source_name = str(source.get("source", "")).strip()
        if source_name:
            fontes.append(source_name)

    for entry in pep.get("entries", []) or []:
        nome = normalize_name(str(entry.get("nome", "")))
        if nome:
            names.append(nome)
        funcao = str(entry.get("funcao", "")).strip()
        if funcao:
            cargos.add(funcao)

    for candidatura in tse.get("candidaturas", []) or []:
        nome = normalize_name(str(candidatura.get("nome", "")))
        if nome:
            names.append(nome)
        cargo = str(candidatura.get("cargo", "")).strip()
        if cargo:
            cargos.add(cargo)

    for entry in sancoes_source.get("ceisp", []) or []:
        normalized = dict(entry)
        normalized["tipo"] = "CEIS"
        sancoes.append(normalized)
    for entry in sancoes_source.get("cnep", []) or []:
        normalized = dict(entry)
        normalized["tipo"] = "CNEP"
        sancoes.append(normalized)

    if sancoes:
        irregularidades.append(f"{len(sancoes)} registro(s) de sanções CEIS/CNEP.")
    if dou.get("nomeacoes"):
        irregularidades.append("Foram encontradas menções em DOU.")
    if state.get("source_errors"):
        irregularidades.append("Algumas fontes falharam e foram ignoradas nesta consulta.")

    nome = ""
    filtered_names = [candidate for candidate in names if candidate]
    if filtered_names:
        nome = Counter(filtered_names).most_common(1)[0][0]

    is_pep = bool(pep.get("found")) or bool(tse.get("found")) or bool(
        tse.get("candidaturas")
    )

    response = _normalize_response(
        {
            "nome": nome,
            "cargos": sorted(cargos),
            "fontes": sorted(set(fontes)),
            "irregularidades": irregularidades,
            "sancoes": sancoes,
            "bens": bens,
            "is_pep": is_pep,
        }
    )
    return {"response": response}


async def save_to_db_node(state: GraphState) -> GraphState:
    """
    Persist response in file cache (24h TTL).

    This is a temporary cache stub until Redis/Mongo persistence lands.
    """
    if not state.get("valid", False):
        return {}

    cpf = state.get("cpf", "")
    response = state.get("response")
    if not cpf or not isinstance(response, dict):
        return {}

    payload = {
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "response": response,
        "sources_results": state.get("sources_results", {}),
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{cpf}.json"

    try:
        async with aiofiles.open(cache_file, "w", encoding="utf-8") as file:
            await file.write(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 - cache write failures should not break flow.
        logger.warning("Failed to persist cache for CPF %s: %s", cpf, exc)

    return {}


def _route_after_validate(state: GraphState) -> str:
    return "check_cache" if state.get("valid", False) else "aggregate_sources"


def _route_after_cache(state: GraphState) -> str:
    return "aggregate_sources" if state.get("cache_hit", False) else "dispatch_parallel_crawls"


builder = StateGraph(GraphState)
builder.add_node("validate_cpf", validate_cpf_node)
builder.add_node("check_cache", check_cache_node)
builder.add_node("dispatch_parallel_crawls", dispatch_parallel_crawls_node)
builder.add_node("crawl_pep", crawl_pep_node)
builder.add_node("crawl_tse", crawl_tse_node)
builder.add_node("crawl_sancoes", crawl_sancoes_node)
builder.add_node("crawl_dou", crawl_dou_node)
builder.add_node("aggregate_sources", aggregate_sources_node)
builder.add_node("save_to_db", save_to_db_node)

builder.set_entry_point("validate_cpf")
builder.add_conditional_edges(
    "validate_cpf",
    _route_after_validate,
    {"check_cache": "check_cache", "aggregate_sources": "aggregate_sources"},
)
builder.add_conditional_edges(
    "check_cache",
    _route_after_cache,
    {
        "dispatch_parallel_crawls": "dispatch_parallel_crawls",
        "aggregate_sources": "aggregate_sources",
    },
)

# Fan-out parallel source crawls.
builder.add_edge("dispatch_parallel_crawls", "crawl_pep")
builder.add_edge("dispatch_parallel_crawls", "crawl_tse")
builder.add_edge("dispatch_parallel_crawls", "crawl_sancoes")
builder.add_edge("dispatch_parallel_crawls", "crawl_dou")

# Fan-in aggregation once all source nodes complete.
builder.add_edge("crawl_pep", "aggregate_sources")
builder.add_edge("crawl_tse", "aggregate_sources")
builder.add_edge("crawl_sancoes", "aggregate_sources")
builder.add_edge("crawl_dou", "aggregate_sources")

builder.add_edge("aggregate_sources", "save_to_db")
builder.add_edge("save_to_db", END)

cpf_pep_graph = builder.compile()
