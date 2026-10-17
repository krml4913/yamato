import type { paths } from "./schema";

export type Overview =
  paths["/api/v1/overview"]["get"]["responses"]["200"]["content"]["application/json"];

export async function getOverview(): Promise<Overview> {
  const res = await fetch("/api/v1/overview");
  if (!res.ok) throw new Error(`GET /api/v1/overview: ${res.status}`);
  return res.json();
}

type EditPaths = paths["/api/v1/edit/preview"]["post"];
export type EditRequest = EditPaths["requestBody"]["content"]["application/json"];
export type EditPreview = EditPaths["responses"]["200"]["content"]["application/json"];
export type EditCommitRequest =
  paths["/api/v1/edit/commit"]["post"]["requestBody"]["content"]["application/json"];
export type EditCommit =
  paths["/api/v1/edit/commit"]["post"]["responses"]["200"]["content"]["application/json"];

/** dashboard --edit で起動したときだけ、サーバーが index.html の meta に埋める。無ければ読むだけ。 */
export function editToken(): string | null {
  return document.querySelector('meta[name="yamato-token"]')?.getAttribute("content") || null;
}

export class EditApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function postEdit<T>(op: "preview" | "commit", body: object): Promise<T> {
  const token = editToken();
  if (!token) throw new EditApiError(0, "forbidden", "読むだけで起動している (--edit が無い)");
  const res = await fetch(`/api/v1/edit/${op}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Yamato-Token": token },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const e = data?.error;
    throw new EditApiError(res.status, e?.code ?? "internal", e?.message ?? `POST ${op}: ${res.status}`);
  }
  return data as T;
}

export const previewEdit = (req: EditRequest) => postEdit<EditPreview>("preview", req);
export const commitEdit = (req: EditCommitRequest) => postEdit<EditCommit>("commit", req);
