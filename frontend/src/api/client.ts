import type { paths } from "./schema";

export type Overview =
  paths["/api/v1/overview"]["get"]["responses"]["200"]["content"]["application/json"];

export async function getOverview(): Promise<Overview> {
  const res = await fetch("/api/v1/overview");
  if (!res.ok) throw new Error(`GET /api/v1/overview: ${res.status}`);
  return res.json();
}
