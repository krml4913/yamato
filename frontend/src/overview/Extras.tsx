import { CONTEXT_NEAR } from "./format";
import type { Seat, ShipState } from "./types";
import status from "../status.module.css";

function Ctx({ ctx }: { ctx: Seat["context"] }) {
  if (!ctx) return <>-</>;
  const k = `${Math.floor(ctx.tokens / 1000)}k`;
  if (!ctx.limit) return <>{k}</>;
  const ratio = ctx.tokens / ctx.limit;
  return (
    <>
      {k} / {Math.floor(ctx.limit / 1000)}k ({Math.floor(ratio * 100)}%)
      {ratio >= CONTEXT_NEAR && <> <span className={status.warn}>rotate が近い</span></>}
    </>
  );
}

// 艦の枠の 2 段目: 前回の日報・孤児・memory の棚卸しの目安・文脈量。
export function Extras({ g }: { g: ShipState }) {
  const r = g.report;
  return (
    <>
      <h3>前回の日報</h3>
      {r ? (
        <p style={{ whiteSpace: "pre-wrap" }}>
          <b>{r.date}</b>
          {r.word && <><br />一言: {r.word}</>}
          {r.tomorrow && <><br />明日: {r.tomorrow}</>}
        </p>
      ) : (
        <p className={status.dim}>まだ無い</p>
      )}
      <h3>孤児 (担当が落ちて active のまま)</h3>
      {g.orphans.length ? (
        <ul>
          {g.orphans.map((o) => (
            <li key={o.id}><b>{o.id}</b> {o.title} <span className={status.dim}>{o.why}</span></li>
          ))}
        </ul>
      ) : (
        <p className={status.ok}>無い</p>
      )}
      <h3>memory の棚卸し</h3>
      <ul>
        {g.memory.map((m) => (
          <li key={m.role ?? "knowledge"}>
            {m.role ?? "knowledge"}: 候補 {m.count} 件 /{" "}
            {m.days == null ? "前回の反映なし" : `前回の反映から ${m.days} 日`}
            {m.due && <> <span className={status.warn}>棚卸しの目安に達した</span></>}
          </li>
        ))}
      </ul>
      <h3>席の文脈量 (rotate の閾値に対して)</h3>
      <ul>
        {g.seats.map((s) => (
          <li key={s.name}><b>{s.name}</b> <Ctx ctx={s.context} /></li>
        ))}
      </ul>
    </>
  );
}
