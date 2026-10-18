import { fmtSpan } from "./format";
import type { Ship } from "./types";

// 動いている艦 = 生きている席が 1 つでもある艦。読めない艦 (state が無い) は気づかせるために動いている側に置く。
export function isRunning(ship: Ship): boolean {
  return !ship.state || ship.state.seats.some((s) => s.alive);
}

// 止まっている艦の 1 行の要約 (名前の後ろ)。いちばん新しい席の動きと、board の件数。
export function stoppedSummary(ship: Ship, now: number): string {
  const g = ship.state;
  if (!g) return "";
  const last = Math.max(0, ...g.seats.map((s) => s.last ?? 0));
  const counts = Object.entries(g.counts).sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, n]) => `${k} ${n}`);
  return [last ? `最後の動き ${fmtSpan(now - last)}前` : "動きの記録なし", counts.length ? counts.join(" / ") : "board 空"].join(" / ");
}
