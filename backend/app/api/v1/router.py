"""API routes for MVP CPF checks."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from app.agents.graph import cpf_pep_graph
from app.api.v1.schemas import CPFRequest, PPEResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _is_valid_cpf(cpf: str) -> bool:
    """Validate CPF using official checksum rules."""
    if len(cpf) != 11 or not cpf.isdigit():
        return False

    # Reject repeated sequences such as 11111111111.
    if cpf == cpf[0] * 11:
        return False

    first_sum = sum(int(cpf[i]) * (10 - i) for i in range(9))
    first_digit = (first_sum * 10) % 11
    first_digit = 0 if first_digit == 10 else first_digit
    if first_digit != int(cpf[9]):
        return False

    second_sum = sum(int(cpf[i]) * (11 - i) for i in range(10))
    second_digit = (second_sum * 10) % 11
    second_digit = 0 if second_digit == 10 else second_digit
    return second_digit == int(cpf[10])


def _invalid_cpf_response() -> PPEResponse:
    """Return deterministic response for invalid CPF input."""
    return PPEResponse(
        nome="",
        cargos=[],
        fontes=[],
        irregularidades=["CPF inválido com base na regra de checksum."],
        is_pep=False,
    )


@router.post("/check-cpf", response_model=PPEResponse)
async def check_cpf(payload: CPFRequest) -> PPEResponse:
    """
    Validate CPF and execute the initial LangGraph pipeline.

    For invalid CPF checksum, the endpoint returns a deterministic mock response.
    """
    if not _is_valid_cpf(payload.cpf):
        return _invalid_cpf_response()

    try:
        state = await cpf_pep_graph.ainvoke({"cpf": payload.cpf})
        response_payload = state.get("response")
    except Exception as exc:
        logger.exception("Failed to execute PEP graph for CPF %s", payload.cpf)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao processar consulta de CPF.",
        ) from exc

    if not isinstance(response_payload, dict):
        logger.error("Graph returned invalid payload type: %s", type(response_payload))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Resposta inválida do pipeline de verificação.",
        )

    try:
        return PPEResponse(**response_payload)
    except Exception as exc:
        logger.exception("Malformed graph response payload: %s", response_payload)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Formato de resposta inválido.",
        ) from exc
