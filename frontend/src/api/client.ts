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
