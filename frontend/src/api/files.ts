import type { paths } from "./schema";

export type ShipFile =
  paths["/api/v1/ships/{name}/files/{file}"]["get"]["responses"]["200"]["content"]["application/json"];

// file は艦フォルダからの相対 path (roles/impl.md)。URL では 1 語にエンコードする。
export async function getShipFile(name: string, file: string): Promise<ShipFile> {
  const res = await fetch(`/api/v1/ships/${encodeURIComponent(name)}/files/${encodeURIComponent(file)}`);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.error?.message ?? `GET files: ${res.status}`);
  }
  return res.json();
}

export function shipFilePath(name: string, file: string): string {
  return `/ship/${encodeURIComponent(name)}/files/${encodeURIComponent(file)}`;
}
