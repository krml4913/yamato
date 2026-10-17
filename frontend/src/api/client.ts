import type { paths } from "./schema";

export type ShipList =
  paths["/api/v1/ships"]["get"]["responses"]["200"]["content"]["application/json"];

export async function listShips(): Promise<ShipList> {
  const res = await fetch("/api/v1/ships");
  if (!res.ok) throw new Error(`GET /api/v1/ships: ${res.status}`);
  return res.json();
}
