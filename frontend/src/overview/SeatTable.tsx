import { fmtSpan, fmtTime, fmtTokens } from "./format";
import type { Seat } from "./types";

const HELP = "赤 = すぐ手が要る (詰まり・API エラー) / 黄 = 様子見 (動いていない・担当が無い)";

export function SeatTable({ seats, now }: { seats: Seat[]; now: number }) {
  return (
    <>
      <p className="dim">
        要対応の列: <span className="red">赤</span> = すぐ手が要る (詰まり・API エラー) /{" "}
        <span className="warn">黄</span> = 様子見 (動いていない・担当が無い)。内部の言葉は文に重ねたとき (hover) に出る
      </p>
      <table>
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
      <td><b>{s.name}</b> <span className="dim">{s.shift}</span></td>
      <td>{s.alive ? <span className="ok">pid {s.pid}</span> : <span className="dim">止</span>}</td>
      <td>{s.status ?? "-"}{s.waiting_for && <> <span className="warn">{s.waiting_for}</span></>}</td>
      <td>
        {s.last ? <>{fmtTime(s.last)} <span className="dim">({fmtSpan(now - s.last)}前)</span></> : "-"}
      </td>
      <td>{active}</td>
      <td>{s.unread || "-"}</td>
      <td>
        {s.flags.map((f, i) => (
          <div key={i}><span className={f.level === "red" ? "red" : "warn"} title={f.detail}>{f.text}</span></div>
        ))}
      </td>
      <td>
        {fmtTokens(s.tokens)}
        {s.shift === "headless" && (
          // max_budget_usd は -p の 1 回ごとの上限。他の席には出さない
          <><br /><span className="dim">
            前回 {s.last_cost == null ? "-" : `$${s.last_cost.toFixed(2)}`} / 上限 {s.budget ? `$${s.budget}` : "-"}
          </span></>
        )}
      </td>
      <td className="dim">{s.log.slice(0, 160)}</td>
    </tr>
  );
}
