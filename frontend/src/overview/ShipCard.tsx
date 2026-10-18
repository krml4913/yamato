import { useState } from "react";
import { Link } from "react-router-dom";
import { ContextUse, Inventory, Orphans, Report } from "./Extras";
import { deadlineAt, fmtTime, fmtTokens, remaining } from "./format";
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

// showName=false は、ページの題が艦名を出しているとき (艦のページ) 枠の頭で二重にしない。
// children は状態の要点 (バッジと稼働の行) のすぐ下に出す。艦のページの操作の置き場。
export function ShipCard({ ship, now, waits, showName = true, children }: {
  ship: Ship; now: number; waits: Wait[]; showName?: boolean; children?: React.ReactNode;
}) {
  const g = ship.state;
  if (!g) {
    return (
      <section className={styles.card}>
        {showName && <div className={styles.head}><h2><ShipLink name={ship.name} /></h2></div>}
        <p className={status.red}>読めない: {ship.error ?? "?"}</p>
        {children}
      </section>
    );
  }
  const counts = Object.entries(g.counts).sort(([a], [b]) => (a < b ? -1 : 1));
  return (
    <section className={styles.card}>
      <div className={styles.head}>
        {showName && <h2><ShipLink name={ship.name} /></h2>}
        <Badges ship={ship} waits={waits} />
      </div>
      <p className={`${status.dim} ${styles.sub}`}>
        {[
          // 「未起動 (deadline なし)」のように残りが無い表示は、バッジが全文を出している。地の文で繰り返さない
          remaining(g.deadline) === g.deadline ? null : deadlineAt(g.deadline),
          g.hub_last ? `captain ${g.hub} の最終 ${fmtTime(g.hub_last)}` : null,
          `今日 ${fmtTokens(g.tokens)} tok`,
        ].filter((x) => x !== null).join(" / ")}
      </p>
      {children}
      <div className={styles.secTable}>
        <SeatTable ship={ship.name} seats={g.seats} now={now} />
      </div>
      <ShipBody g={g} now={now} counts={counts} />
      <Inventory g={g} />
      <ContextUse g={g} />
    </section>
  );
}

// 異常は直近 24 時間を上に出し、それより古いものは畳む (済んだものと今のものを混ぜない)。
// 24 時間以内でも 1 時間より前のものは赤を弱める (解決済みかもしれない)
const RECENT_SEC = 24 * 3600;
const FRESH_SEC = 3600;

// 種類名の短い日本語。無いものは種類名をそのまま主に出す
const KIND_LABEL: Record<string, string> = {
  force_stop: "強制停止",
  shift_failed: "シフトが失敗",
  launch_failed: "席の起動に失敗",
  permission_denied: "権限で拒否",
  spin_suspected: "送信の空回り",
  duplicate_suspected: "同じ送信の重複",
  captain_gap: "captain が長く止まっている",
  notify_failed: "通知に失敗",
  pr_open_failed: "PR の作成に失敗",
  pr_merge_failed: "PR の merge に失敗",
  pr_conflict: "PR が衝突",
  worktree_add_failed: "worktree の作成に失敗",
  worktree_rm_failed: "worktree の片付けに失敗",
};

type Anomaly = ShipState["anomalies"][number];

function AnomalyRow({ a, tone }: { a: Anomaly; tone: "fresh" | "stale" | "old" }) {
  const [open, setOpen] = useState(false);
  const label = KIND_LABEL[a.kind];
  const long = a.summary.length > 80 || a.summary.includes("\n");
  return (
    <li className={styles.anomaly}>
      <div className={styles.anomalyHead}>
        <span className={tone === "fresh" ? status.red : tone === "stale" ? styles.staleKind : styles.oldKind}>
          {label ?? a.kind}
        </span>
        {label && <span className={styles.rawKind}>{a.kind}</span>}
        <span className={styles.anomalyTime}>{fmtTime(a.ts)}</span>
      </div>
      {a.summary && (
        <div className={open ? styles.anomalyBody : `${styles.anomalyBody} ${styles.clamp}`}>{a.summary}</div>
      )}
      {long && (
        <button type="button" className={styles.more} aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "たたむ" : "全文を開く"}
        </button>
      )}
    </li>
  );
}

function Anomalies({ list, now }: { list: ShipState["anomalies"]; now: number }) {
  if (list.length === 0) return null;
  const recent = list.filter((a) => a.ts >= now - RECENT_SEC);
  const old = list.filter((a) => a.ts < now - RECENT_SEC);
  return (
    <div className={styles.sec}>
      <h3>直近の異常</h3>
      {recent.length > 0 ? (
        <ul className={styles.anomalies}>
          {recent.map((a, i) => (
            <AnomalyRow key={i} a={a} tone={a.ts >= now - FRESH_SEC ? "fresh" : "stale"} />
          ))}
        </ul>
      ) : (
        <p className={status.dim}>24 時間以内の異常は無い</p>
      )}
      {old.length > 0 && (
        <details className={styles.oldAnomalies}>
          <summary>24 時間より前の異常 ({old.length} 件)</summary>
          <ul className={styles.anomalies}>
            {old.map((a, i) => <AnomalyRow key={i} a={a} tone="old" />)}
          </ul>
        </details>
      )}
    </div>
  );
}

// 中身の量に合わせて段を組む。board・PR が空なら 1 行にして異常を全幅に、
// 異常が無ければ board を全幅に、孤児が無ければ 1 行にして日報を全幅にする
function ShipBody({ g, now, counts }: { g: ShipState; now: number; counts: [string, number][] }) {
  const boardEmpty = g.board.length === 0 && g.prs.length === 0 && counts.length === 0;
  const hasAnomalies = g.anomalies.length > 0;
  const boardSec = (
    <>
      <div className={styles.sec}>
        <h3>board</h3>
        <p>{counts.map(([k, n]) => <span className={styles.tag} key={k}>{k} {n}</span>)}</p>
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
        <div className={styles.sec}>
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
    </>
  );
  return (
    <>
      {boardEmpty ? (
        <div className={`${styles.sec} ${styles.emptyLine}`}>
          <h3>board</h3>
          <span className={status.dim}>空</span>
        </div>
      ) : null}
      {boardEmpty || !hasAnomalies ? (
        <>
          {!boardEmpty && boardSec}
          <Anomalies list={g.anomalies} now={now} />
        </>
      ) : (
        <div className={styles.cols}>
          <div className={styles.colMain}>{boardSec}</div>
          <div className={styles.colSide}><Anomalies list={g.anomalies} now={now} /></div>
        </div>
      )}
      {g.orphans.length === 0 ? (
        <>
          <div className={`${styles.sec} ${styles.emptyLine}`}>
            <h3>孤児</h3>
            <span className={status.dim}>無い (担当が落ちて active のまま残った項目)</span>
          </div>
          <Report g={g} />
        </>
      ) : (
        <div className={styles.cards}>
          <Report g={g} />
          <Orphans g={g} />
        </div>
      )}
    </>
  );
}
