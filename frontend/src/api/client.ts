import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export const api = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? "",
});

export type Schema<Name extends keyof components["schemas"]> =
  components["schemas"][Name];

export class ApiFailure extends Error {
  readonly code: string;

  constructor(message: string, code = "request_failed") {
    super(message);
    this.name = "ApiFailure";
    this.code = code;
  }
}

export function unwrap<T>(result: { data?: T; error?: unknown }): T {
  if (result.data !== undefined) return result.data;
  const envelope = result.error as
    { error?: { code?: string; message?: string } } | undefined;
  throw new ApiFailure(
    envelope?.error?.message ?? "Не удалось выполнить запрос",
    envelope?.error?.code,
  );
}

export async function uploadImport(
  input: Omit<
    Schema<"Body_upload_import_route_api_v1_imports_post">,
    "file"
  > & {
    file: File[];
  },
): Promise<Schema<"ImportUploadResponse">> {
  const form = new FormData();
  form.append("portfolio_id", input.portfolio_id);
  form.append("account_id", input.account_id);
  form.append("source_provider", input.source_provider);
  form.append("declared_format", input.declared_format);
  input.file.forEach((file) => form.append("file", file));
  const response = await fetch(
    `${import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1/imports`,
    {
      method: "POST",
      body: form,
    },
  );
  const payload = (await response.json()) as
    Schema<"ImportUploadResponse"> | Schema<"ErrorResponse">;
  if (!response.ok) {
    const error = payload as Schema<"ErrorResponse">;
    throw new ApiFailure(error.error.message, error.error.code);
  }
  return payload as Schema<"ImportUploadResponse">;
}
