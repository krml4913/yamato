import type { paths } from "./schema";

export type Overview =
  paths["/api/v1/overview"]["get"]["responses"]["200"]["content"]["application/json"];

export async function getOverview(): Promise<Overview> {
  const res = await fetch("/api/v1/overview");
  if (!res.ok) throw new Error(`GET /api/v1/overview: ${res.status}`);
  return res.json();
}

export type ShipConfig =
  paths["/api/v1/ships/{name}/config"]["get"]["responses"]["200"]["content"]["application/json"];

export async function getShipConfig(name: string): Promise<ShipConfig> {
  const res = await fetch(`/api/v1/ships/${encodeURIComponent(name)}/config`);
  if (!res.ok) {
    let message = `${res.status}`;
    try {
      message = (await res.json()).error.message;
    } catch {
      /* 本文が JSON でなければ status だけ */
    }
    throw new Error(message);
  }
  return res.json();
}

export type Fleet = paths["/api/v1/fleet"]["get"]["responses"]["200"]["content"]["application/json"];

export async function getFleet(): Promise<Fleet> {
  const res = await fetch("/api/v1/fleet");
  if (!res.ok) {
    let message = `${res.status}`;
    try {
      message = (await res.json()).error.message;
    } catch {
      /* 本文が JSON でなければ status だけ */
    }
    throw new Error(message);
  }
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
    public detail: string | null = null,
  ) {
    super(message);
  }
}

async function postJson<T>(url: string, body: object): Promise<T> {
  const token = editToken();
  if (!token) throw new EditApiError(0, "forbidden", "読むだけで起動している (--edit が無い)");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Yamato-Token": token },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const e = data?.error;
    throw new EditApiError(res.status, e?.code ?? "internal", e?.message ?? `POST ${url}: ${res.status}`, e?.detail ?? null);
  }
  return data as T;
}

const postEdit = <T>(op: "preview" | "commit", body: object) => postJson<T>(`/api/v1/edit/${op}`, body);

export const previewEdit = (req: EditRequest) => postEdit<EditPreview>("preview", req);
export const commitEdit = (req: EditCommitRequest) => postEdit<EditCommit>("commit", req);

export type OpRequest = paths["/api/v1/ops/{op}"]["post"]["requestBody"]["content"]["application/json"];
export type OpResult = paths["/api/v1/ops/{op}"]["post"]["responses"]["200"]["content"]["application/json"];

/** 操作 (yamato の CLI を 1 回呼ぶ)。CLI が失敗しても resolve し、exit_code と output を見せる。 */
export const runOp = (op: string, req: OpRequest) =>
  postJson<OpResult>(`/api/v1/ops/${encodeURIComponent(op)}`, req);
