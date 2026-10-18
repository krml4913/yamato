import { Link } from "react-router-dom";
import { Misc, Report } from "./Extras";
import { fmtTime, fmtTokens, remaining } from "./format";
import { SeatTable } from "./SeatTable";
import type { Ship, ShipState, Wait } from "./types";
import status from "../status.module.css";
import styles from "./overview.module.css";

type BadgeKind = "ok" | "warn" | "red" | "dim";
const KIND_CLASS: Record<BadgeKind, string> = {
  ok: styles.badgeOk, warn: styles.badgeWarn, red: styles.badgeRed, dim: styles.badgeDim,
};

export function ShipLink({ name }: { name: string }) {
  return <Link className={styles.shipLink} to={`/ship/${encodeURIComponent(name)}`}>{name}</Link>;
}

function Badge({ kind, children }: { kind?: BadgeKind; children: React.ReactNode }) {
  return <span className={`${styles.badge} ${kind ? KIND_CLASS[kind] : ""}`}>{children}</span>;
}

// 艦の要点のバッジ。0 件・無いものは出さない (出すのは稼働・残り時間と、board の件数)
function Badges({ ship, waits }: { ship: Ship; waits: Wait[] }) {
  const g = ship.state;
  if (!g) return null;
  const alive = g.seats.filter((s) => s.alive).length;
  const myWaits = waits.filter((w) => w.ship === ship.name).length;
  return (
    <>
      {alive > 0
        ? <Badge kind="ok">稼働 {alive}/{g.seats.length}</Badge>
        : <Badge kind="dim">停止</Badge>}
      <Badge kind="dim">{remaining(g.deadline)}</Badge>
      {myWaits > 0 && <Badge kind="red">判断待ち {myWaits}</Badge>}
      {g.orphans.length > 0 && <Badge kind="warn">孤児 {g.orphans.length}</Badge>}
      {g.owner_unread > 0 && <Badge kind="warn">owner 宛て未読 {g.owner_unread}</Badge>}
      {(g.counts.active ?? 0) > 0 && <Badge>active {g.counts.active}</Badge>}
      {(g.counts.open ?? 0) > 0 && <Badge>open {g.counts.open}</Badge>}
    </>
  );
}

export function ShipCard({ ship, now, waits }: { ship: Ship; now: number; waits: Wait[] }) {
  const g = ship.state;
  if (!g) {
    return (
      <section className={styles.card}>
        <div className={styles.head}><h2><ShipLink name={ship.name} /></h2></div>
        <p className={status.red}>読めない: {ship.error ?? "?"}</p>
      </section>
    );
  }
  const counts = Object.entries(g.counts).sort(([a], [b]) => (a < b ? -1 : 1));
  return (
    <section className={styles.card}>
      <div className={styles.head}>
        <h2><ShipLink name={ship.name} /></h2>
        <Badges ship={ship} waits={waits} />
      </div>
      <p className={`${status.dim} ${styles.sub}`}>
        {g.deadline}
        {g.hub_last ? ` / captain ${g.hub} の最終 ${fmtTime(g.hub_last)}` : ""}
        {` / 今日 ${fmtTokens(g.tokens)} tok`}
      </p>
      <div className={styles.secTable}>
        <SeatTable ship={ship.name} seats={g.seats} now={now} />
      </div>
      <div className={styles.secBoard}>
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
      </div>
      {g.prs.length > 0 && (
        <div className={styles.secPr}>
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
        </div>
      )}
      <Anomalies list={g.anomalies} now={now} />
      <div className={styles.secReport}><Report g={g} /></div>
      <div className={styles.secMisc}><Misc g={g} /></div>
    </section>
  );
}

// 異常は直近 24 時間を上に赤で出し、それより古いものは畳む (済んだものと今のものを混ぜない)
const RECENT_SEC = 24 * 3600;

function Anomalies({ list, now }: { list: ShipState["anomalies"]; now: number }) {
  if (list.length === 0) return null;
  const recent = list.filter((a) => a.ts >= now - RECENT_SEC);
  const old = list.filter((a) => a.ts < now - RECENT_SEC);
  return (
    <div className={styles.secPr}>
      <h3>直近の異常</h3>
      {recent.length > 0 ? (
        <ul>
          {recent.map((a, i) => (
            <li key={i}><span className={status.red}>{a.kind}</span> {fmtTime(a.ts)} {a.summary}</li>
          ))}
        </ul>
      ) : (
        <p className={status.dim}>24 時間以内の異常は無い</p>
      )}
      {old.length > 0 && (
        <details className={styles.oldAnomalies}>
          <summary>24 時間より前の異常 ({old.length} 件)</summary>
          <ul>
            {old.map((a, i) => (
              <li key={i}>{a.kind} {fmtTime(a.ts)} {a.summary}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
