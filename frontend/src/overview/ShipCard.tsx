import { Extras } from "./Extras";
import { fmtTime, fmtTokens } from "./format";
import { SeatTable } from "./SeatTable";
import type { Ship } from "./types";
import status from "../status.module.css";
import styles from "./overview.module.css";

export function ShipCard({ ship, now }: { ship: Ship; now: number }) {
  const g = ship.state;
  if (!g) {
    return (
      <section className={styles.card}>
        <h2>{ship.name}</h2>
        <p className={status.red}>読めない: {ship.error ?? "?"}</p>
      </section>
    );
  }
  const counts = Object.entries(g.counts).sort(([a], [b]) => (a < b ? -1 : 1));
  return (
    <section className={styles.card}>
      <h2>{ship.name}</h2>
      <p className={status.dim}>
        {g.deadline}
        {g.hub_last ? ` / captain ${g.hub} の最終 ${fmtTime(g.hub_last)}` : ""}
        {` / 今日 ${fmtTokens(g.tokens)} tok`}
        {g.owner_unread ? ` / owner 宛て未読 ${g.owner_unread} 件` : ""}
      </p>
      <SeatTable seats={g.seats} now={now} />
      <h3>board</h3>
      <p>
        {counts.length ? counts.map(([k, n]) => <span className={styles.tag} key={k}>{k} {n}</span>) : "-"}
      </p>
      <ul>
        {g.board.map((b) => (
          <li key={b.id}>
            <b>{b.id}</b> [{b.state}] {b.title}{" "}
            <span className={status.dim}>
              {b.assignee ?? "-"}{b.blocked_on.length ? ` / blocked_on ${b.blocked_on.join(", ")}` : ""}
            </span>
          </li>
        ))}
      </ul>
      <Extras g={g} />
      {g.prs.length > 0 && (
        <>
          <h3>PR</h3>
          <ul>
            {g.prs.map((p) => (
              <li key={`${p.item}/${p.repo}/${p.number}`}>
                {p.link ? <a href={p.link}>#{p.number}</a> : `#${p.number}`}
                {g.multi_repo && ` [${p.repo}]`} <b>{p.item}</b> {p.title}{" "}
                <span className={status.dim}>({p.state})</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {g.anomalies.length > 0 && (
        <>
          <h3>直近の異常</h3>
          <ul>
            {g.anomalies.map((a, i) => (
              <li key={i}><span className={status.red}>{a.kind}</span> {fmtTime(a.ts)} {a.summary}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
