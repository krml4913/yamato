import { useState } from "react";
import { editToken } from "../api/client";
import { OpPanel, type OpSpec } from "../ops/OpPanel";
import { fmtSpan, fmtTime, fmtTokens } from "./format";
import type { Seat } from "./types";
import status from "../status.module.css";
import table from "../table.module.css";

const HELP = "赤 = すぐ手が要る (詰まり・API エラー) / 黄 = 様子見 (動いていない・担当が無い)";

// 席の操作。--edit で起動したとき (index.html にトークンがあるとき) だけ入口を出す
function seatOp(ship: string, s: Seat, kind: "seat-stop" | "rotate"): OpSpec {
  return kind === "seat-stop"
    ? {
        op: "seat-stop", ship, params: { seat: s.name }, title: `席 ${s.name} の終業を指示する`, confirmLabel: "終業を指示する",
        detail: "席の終業を受け付ける。引き継ぎ (handoff.md) が今回のシフトで書かれていなければ断られる。受け付ければ数秒後にセッションが止まる。",
      }
    : {
        op: "rotate", ship, params: { seat: s.name }, title: `席 ${s.name} に入れ替えの印を立てる`, confirmLabel: "印を立てる",
        detail: "止まっている persistent の席の次のシフトを、resume でなく新しいシフトにする。生きている席・persistent でない席は断られる。",
      };
}

export function SeatTable({ ship, seats, now }: { ship: string; seats: Seat[]; now: number }) {
  const [pending, setPending] = useState<OpSpec | null>(null);
  const canOp = editToken() !== null;
  return (
    <>
      <p className={status.dim}>
        要対応の列: <span className={status.red}>赤</span> = すぐ手が要る (詰まり・API エラー) /{" "}
        <span className={status.warn}>黄</span> = 様子見 (動いていない・担当が無い)。内部の言葉は文に重ねたとき (hover) に出る
      </p>
      <table className={table.table}>
        <thead>
          <tr>
            <th>席</th><th>生存</th><th>status</th><th>最終</th><th>担当 (active)</th>
            <th>未読</th><th title={HELP}>要対応</th><th>今日 tok</th><th>作業ログの最後</th>{canOp && <th>操作</th>}
          </tr>
        </thead>
        <tbody>
          {seats.map((s) => (
            <SeatRow key={s.name} s={s} now={now} onOp={canOp ? (k) => setPending(seatOp(ship, s, k)) : null} />
          ))}
        </tbody>
      </table>
      {pending && <OpPanel spec={pending} onClose={() => setPending(null)} />}
    </>
  );
}

function SeatRow({ s, now, onOp }: { s: Seat; now: number; onOp: ((k: "seat-stop" | "rotate") => void) | null }) {
  const active = [...s.active, ...s.reviewing.map((i) => `${i} (レビュー)`)].join(", ") || "-";
  return (
    <tr>
      <td><b>{s.name}</b> <span className={status.dim}>{s.shift}</span></td>
      <td>{s.alive ? <span className={status.ok}>pid {s.pid}</span> : <span className={status.dim}>止</span>}</td>
      <td>{s.status ?? "-"}{s.waiting_for && <> <span className={status.warn}>{s.waiting_for}</span></>}</td>
      <td>
        {s.last ? <>{fmtTime(s.last)} <span className={status.dim}>({fmtSpan(now - s.last)}前)</span></> : "-"}
      </td>
      <td>{active}</td>
      <td>{s.unread || "-"}</td>
      <td>
        {s.flags.map((f, i) => (
          <div key={i}><span className={f.level === "red" ? status.red : status.warn} title={f.detail}>{f.text}</span></div>
        ))}
      </td>
      <td>
        {fmtTokens(s.tokens)}
        {s.shift === "headless" && (
          // max_budget_usd は -p の 1 回ごとの上限。他の席には出さない
          <><br /><span className={status.dim}>
            前回 {s.last_cost == null ? "-" : `$${s.last_cost.toFixed(2)}`} / 上限 {s.budget ? `$${s.budget}` : "-"}
          </span></>
        )}
      </td>
      <td className={status.dim}>{s.log.slice(0, 160)}</td>
      {onOp && (
        <td>
          {s.alive && <button onClick={() => onOp("seat-stop")} aria-label={`${s.name} の終業を指示`}>終業</button>}
          {!s.alive && s.shift === "persistent" && (
            <button onClick={() => onOp("rotate")} aria-label={`${s.name} に入れ替えの印`}>入れ替え</button>
          )}
        </td>
      )}
    </tr>
  );
}
