import * as Accordion from "@radix-ui/react-accordion";
import {
  CheckCircledIcon,
  ChevronDownIcon,
  CrossCircledIcon,
  ReloadIcon
} from "@radix-ui/react-icons";
import * as Toast from "@radix-ui/react-toast";
import { useMutation } from "@tanstack/react-query";
import InputMask from "react-input-mask";
import type { FormEvent, InputHTMLAttributes } from "react";
import { useState } from "react";

import { ApiClientError, checkCpf } from "../api/client";
import type { PPEResponse } from "../types";

type ToastVariant = "success" | "error";

type ToastState = {
  open: boolean;
  title: string;
  description: string;
  variant: ToastVariant;
};

type ListAccordionItemProps = {
  title: string;
  value: string;
  items: string[];
  emptyLabel: string;
};

function cleanCpf(value: string): string {
  return value.replace(/\D/g, "");
}

function ListAccordionItem({
  title,
  value,
  items,
  emptyLabel
}: ListAccordionItemProps): JSX.Element {
  return (
    <Accordion.Item value={value} className="rounded-md border bg-white">
      <Accordion.Header>
        <Accordion.Trigger className="group flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium hover:bg-slate-50">
          <span>{title}</span>
          <ChevronDownIcon className="h-4 w-4 transition-transform group-data-[state=open]:rotate-180" />
        </Accordion.Trigger>
      </Accordion.Header>
      <Accordion.Content className="overflow-hidden data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down">
        <div className="p-4">
          <div className="overflow-hidden rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <th className="w-16 px-3 py-2 text-left font-semibold text-slate-600">
                    #
                  </th>
                  <th className="px-3 py-2 text-left font-semibold text-slate-600">
                    Valor
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.length > 0 ? (
                  items.map((item, index) => (
                    <tr key={`${value}-${index}`} className="border-t">
                      <td className="px-3 py-2 text-slate-500">{index + 1}</td>
                      <td className="px-3 py-2">{item}</td>
                    </tr>
                  ))
                ) : (
                  <tr className="border-t">
                    <td className="px-3 py-3 text-slate-500" colSpan={2}>
                      {emptyLabel}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </Accordion.Content>
    </Accordion.Item>
  );
}

export default function Home(): JSX.Element {
  const [cpfInput, setCpfInput] = useState("");
  const [result, setResult] = useState<PPEResponse | null>(null);
  const [toast, setToast] = useState<ToastState>({
    open: false,
    title: "",
    description: "",
    variant: "success"
  });

  const showToast = (
    variant: ToastVariant,
    title: string,
    description: string
  ): void => {
    setToast({ open: true, variant, title, description });
  };

  const mutation = useMutation({
    mutationFn: async (maskedCpf: string) => {
      const normalizedCpf = cleanCpf(maskedCpf);
      return checkCpf({ cpf: normalizedCpf });
    },
    onSuccess: (data) => {
      setResult(data);
      showToast(
        "success",
        "Consulta concluída",
        data.is_pep ? "PEP identificado para este CPF." : "Not a PEP."
      );
    },
    onError: (error: Error) => {
      setResult(null);

      if (error instanceof ApiClientError) {
        if (error.status === 400 || error.status === 422) {
          showToast("error", "Erro de validação", "Invalid CPF");
          return;
        }

        showToast("error", "Erro na API", error.message);
        return;
      }

      showToast("error", "Erro inesperado", "Falha ao consultar o CPF.");
    }
  });

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();

    const normalizedCpf = cleanCpf(cpfInput);
    if (normalizedCpf.length !== 11) {
      setResult(null);
      showToast("error", "Erro de validação", "Invalid CPF");
      return;
    }

    mutation.mutate(cpfInput);
  };

  const pepBadgeClasses = result?.is_pep
    ? "bg-emerald-100 text-emerald-800 border-emerald-200"
    : "bg-slate-100 text-slate-700 border-slate-200";

  return (
    <Toast.Provider swipeDirection="right">
      <main className="min-h-screen bg-slate-100 py-8">
        <section className="mx-auto w-full max-w-4xl px-4">
          <div className="rounded-lg border bg-white shadow-sm">
            <div className="border-b px-6 py-5">
              <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
                Tiradentes V1
              </h1>
              <p className="mt-1 text-sm text-slate-600">
                Verificação de Pessoa Politicamente Exposta (PEP) por CPF.
              </p>
            </div>

            <div className="px-6 py-5">
              <form
                onSubmit={handleSubmit}
                className="flex flex-col gap-4 sm:flex-row sm:items-end"
              >
                <div className="w-full space-y-2">
                  <label
                    htmlFor="cpf-input"
                    className="text-sm font-medium text-slate-700"
                  >
                    CPF
                  </label>
                  <InputMask
                    mask="999.999.999-99"
                    value={cpfInput}
                    maskChar={null}
                    onChange={(event) => setCpfInput(event.target.value)}
                  >
                    {(inputProps: InputHTMLAttributes<HTMLInputElement>) => (
                      <input
                        {...inputProps}
                        id="cpf-input"
                        type="text"
                        inputMode="numeric"
                        autoComplete="off"
                        placeholder="000.000.000-00"
                        className="h-10 w-full rounded-md border border-slate-300 px-3 text-sm shadow-sm outline-none ring-offset-2 placeholder:text-slate-400 focus:border-slate-400 focus:ring-2 focus:ring-slate-200"
                      />
                    )}
                  </InputMask>
                </div>

                <button
                  type="submit"
                  disabled={mutation.isPending}
                  className="inline-flex h-10 items-center justify-center rounded-md bg-slate-900 px-4 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {mutation.isPending ? (
                    <>
                      <ReloadIcon className="mr-2 h-4 w-4 animate-spin" />
                      Verifying...
                    </>
                  ) : (
                    "Verify"
                  )}
                </button>
              </form>
            </div>
          </div>

          {result && (
            <div className="mt-6 rounded-lg border bg-white p-6 shadow-sm">
              <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <h2 className="text-lg font-semibold text-slate-900">Resultado</h2>
                <span
                  className={`inline-flex w-fit items-center rounded-full border px-3 py-1 text-xs font-semibold ${pepBadgeClasses}`}
                >
                  {result.is_pep ? "PEP" : "Not a PEP"}
                </span>
              </div>

              <div className="mb-4 grid gap-3 rounded-md border bg-slate-50 p-4 sm:grid-cols-2">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Nome
                  </p>
                  <p className="mt-1 text-sm text-slate-900">
                    {result.nome || "Não identificado"}
                  </p>
                </div>
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Classificação
                  </p>
                  <p className="mt-1 text-sm text-slate-900">
                    {result.is_pep ? "Pessoa Politicamente Exposta" : "Não PEP"}
                  </p>
                </div>
              </div>

              {!result.is_pep && (
                <div className="mb-4 rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
                  Not a PEP.
                </div>
              )}

              <Accordion.Root type="multiple" className="space-y-3">
                <ListAccordionItem
                  title="Cargos"
                  value="cargos"
                  items={result.cargos}
                  emptyLabel="Nenhum cargo encontrado."
                />
                <ListAccordionItem
                  title="Fontes"
                  value="fontes"
                  items={result.fontes}
                  emptyLabel="Nenhuma fonte registrada."
                />
                <ListAccordionItem
                  title="Irregularidades"
                  value="irregularidades"
                  items={result.irregularidades}
                  emptyLabel="Nenhuma irregularidade encontrada."
                />
              </Accordion.Root>
            </div>
          )}
        </section>
      </main>

      <Toast.Root
        open={toast.open}
        onOpenChange={(open) => setToast((current) => ({ ...current, open }))}
        className={`w-[360px] rounded-md border p-4 shadow-lg ${
          toast.variant === "error"
            ? "border-red-200 bg-red-50 text-red-900"
            : "border-emerald-200 bg-emerald-50 text-emerald-900"
        }`}
      >
        <div className="flex items-start gap-2">
          {toast.variant === "error" ? (
            <CrossCircledIcon className="mt-0.5 h-4 w-4 shrink-0" />
          ) : (
            <CheckCircledIcon className="mt-0.5 h-4 w-4 shrink-0" />
          )}
          <div className="space-y-1">
            <Toast.Title className="text-sm font-semibold">{toast.title}</Toast.Title>
            <Toast.Description className="text-sm">{toast.description}</Toast.Description>
          </div>
        </div>
      </Toast.Root>
      <Toast.Viewport className="fixed right-0 top-0 z-50 flex max-w-[420px] flex-col gap-2 p-4 outline-none" />
    </Toast.Provider>
  );
}
