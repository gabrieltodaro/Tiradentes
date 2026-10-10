"""Pydantic schemas for API v1 CPF/PEP flows."""

from typing import Any

from pydantic import BaseModel, Field, field_validator


class CPFRequest(BaseModel):
    """Input payload for CPF verification."""

    cpf: str = Field(
        ...,
        description="CPF number. Punctuation is accepted and normalized to 11 digits.",
        examples=["12345678909", "123.456.789-09"],
    )

    @field_validator("cpf")
    @classmethod
    def normalize_cpf(cls, value: str) -> str:
        """Keep only digits so the backend can run official checksum validation."""
        digits = "".join(char for char in value if char.isdigit())
        if len(digits) != 11:
            raise ValueError("cpf must contain exactly 11 digits")
        return digits


class PPEResponse(BaseModel):
    """Aggregated response returned after running the verification graph."""

    nome: str = Field(default="", description="Full name found for the CPF.")
    cargos: list[str] = Field(
        default_factory=list,
        description="Public positions/cargos associated with the profile.",
    )
    fontes: list[str] = Field(
        default_factory=list,
        description="Public sources used during collection/verification.",
    )
    irregularidades: list[str] = Field(
        default_factory=list,
        description="Potential issues found in sanctions/cross-checks.",
    )
    sancoes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Sanctions records consolidated from CEIS/CNEP.",
    )
    bens: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Declared assets from TSE candidate disclosures.",
    )
    is_pep: bool = Field(
        default=False,
        description="Whether the record is classified as a politically exposed person.",
    )
