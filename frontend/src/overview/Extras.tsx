import { CONTEXT_NEAR } from "./format";
import type { Seat, ShipState } from "./types";
import status from "../status.module.css";
import styles from "./overview.module.css";

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

// 前回の日報。「一言」「明日」は見出しにして地の文から分ける
export function Report({ g }: { g: ShipState }) {
  const r = g.report;
  return (
    <div className={styles.sec}>
      <h3>前回の日報{r && <span className={styles.secNote}>{r.date}</span>}</h3>
      {r ? (
        <>
          {r.word && <><h4 className={styles.label}>一言</h4><p className={styles.pre}>{r.word}</p></>}
          {r.tomorrow && <><h4 className={styles.label}>明日</h4><p className={styles.pre}>{r.tomorrow}</p></>}
        </>
      ) : (
        <p className={status.dim}>まだ無い</p>
      )}
    </div>
  );
}

export function Orphans({ g }: { g: ShipState }) {
  return (
    <div className={styles.sec}>
      <h3>孤児 (担当が落ちて active のまま)</h3>
      {g.orphans.length ? (
        <ul>
          {g.orphans.map((o) => (
            <li key={o.id}><b>{o.id}</b> {o.title} <span className={status.dim}>{o.why}</span></li>
          ))}
        </ul>
      ) : (
        <p className={status.dim}>無い</p>
      )}
    </div>
  );
}

// memory の棚卸し。普段は見ないので畳む。目安に達した役割があれば畳んでいても要約に出す
export function Inventory({ g }: { g: ShipState }) {
  const due = g.memory.filter((m) => m.due).map((m) => m.role ?? "knowledge");
  const total = g.memory.reduce((n, m) => n + m.count, 0);
  return (
    <details className={`${styles.sec} ${styles.fold}`}>
      <summary>
        <span className={styles.foldTitle}>memory の棚卸し</span>
        <span className={styles.foldSum}>
          候補 {total} 件
          {due.length > 0 && <> / <span className={status.warn}>棚卸しの目安に達した: {due.join(", ")}</span></>}
        </span>
      </summary>
      <ul>
        {g.memory.map((m) => (
          <li key={m.role ?? "knowledge"}>
            {m.role ?? "knowledge"}: 候補 {m.count} 件 /{" "}
            {m.days == null ? "前回の反映なし" : `前回の反映から ${m.days} 日`}
            {m.due && <> <span className={status.warn}>棚卸しの目安に達した</span></>}
          </li>
        ))}
      </ul>
    </details>
  );
}

// 席の文脈量 (rotate の閾値に対して)。普段は見ないので畳む。閾値に近い席は畳んでいても要約に出す
export function ContextUse({ g }: { g: ShipState }) {
  const near = g.seats.filter((s) => s.context?.limit && s.context.tokens / s.context.limit >= CONTEXT_NEAR);
  return (
    <details className={`${styles.sec} ${styles.fold}`}>
      <summary>
        <span className={styles.foldTitle}>席の文脈量 (rotate の閾値に対して)</span>
        <span className={styles.foldSum}>
          {near.length > 0
            ? <span className={status.warn}>rotate が近い: {near.map((s) => s.name).join(", ")}</span>
            : "近い席は無い"}
        </span>
      </summary>
      <ul>
        {g.seats.map((s) => (
          <li key={s.name}><b>{s.name}</b> <Ctx ctx={s.context} /></li>
        ))}
      </ul>
    </details>
  );
}
