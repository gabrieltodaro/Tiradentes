import type { CPFRequest, PPEResponse } from "../types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const CHECK_CPF_ENDPOINT = `${API_BASE_URL}/api/v1/check-cpf`;

export class ApiClientError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
  }
}

function buildMockResponse(cpf: string): PPEResponse {
  // Local fallback keeps frontend development unblocked if backend is down.
  if (cpf.endsWith("00")) {
    return {
      nome: "JOAO DA SILVA (MOCK)",
      cargos: ["Deputado Federal", "Secretário Estadual"],
      fontes: [
        "Portal Dados Abertos",
        "Portal da Transparência (PEP)",
        "Tesouro Transparente"
      ],
      irregularidades: ["Citação em base de sanções administrativas (mock)."],
      is_pep: true
    };
  }

  return {
    nome: "",
    cargos: [],
    fontes: ["Portal Dados Abertos", "Portal da Transparência (PEP)"],
    irregularidades: [],
    is_pep: false
  };
}

export async function checkCpf(payload: CPFRequest): Promise<PPEResponse> {
  try {
    const response = await fetch(CHECK_CPF_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      // CORS is handled by backend config; frontend sends standard JSON.
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      let detail = "Erro ao consultar CPF.";

      try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body.detail === "string") {
          detail = body.detail;
        }
      } catch {
        // Keep default detail when response body is not JSON.
      }

      throw new ApiClientError(detail, response.status);
    }

    return (await response.json()) as PPEResponse;
  } catch (error) {
    if (error instanceof ApiClientError) {
      throw error;
    }

    return buildMockResponse(payload.cpf.replace(/\D/g, ""));
  }
}
