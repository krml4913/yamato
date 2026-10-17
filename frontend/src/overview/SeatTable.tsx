import { fmtSpan, fmtTime, fmtTokens } from "./format";
import type { Seat } from "./types";
import status from "../status.module.css";
import table from "../table.module.css";

const HELP = "赤 = すぐ手が要る (詰まり・API エラー) / 黄 = 様子見 (動いていない・担当が無い)";

export function SeatTable({ seats, now }: { seats: Seat[]; now: number }) {
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
            <th>未読</th><th title={HELP}>要対応</th><th>今日 tok</th><th>作業ログの最後</th>
          </tr>
        </thead>
        <tbody>
          {seats.map((s) => (
            <SeatRow key={s.name} s={s} now={now} />
          ))}
        </tbody>
      </table>
    </>
  );
}

function SeatRow({ s, now }: { s: Seat; now: number }) {
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
    </tr>
  );
}
