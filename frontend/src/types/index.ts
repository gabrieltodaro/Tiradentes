export type CPFRequest = {
  cpf: string;
};

export type PPEResponse = {
  nome: string;
  cargos: string[];
  fontes: string[];
  irregularidades: string[];
  is_pep: boolean;
};
