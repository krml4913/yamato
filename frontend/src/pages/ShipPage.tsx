import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { editToken, getShipConfig, type ShipConfig } from "../api/client";
import { OpPanel, type OpSpec } from "../ops/OpPanel";
import styles from "./ship.module.css";

// team.yaml の roles から席名を作る (count: 1 は役割名、n なら <役割>-1..n)。
// 取れない・読めないときは null を返し、呼び出し側が自由入力に戻す。
export function seatNames(config: ShipConfig): string[] | null {
  const roles = (config.fields ?? []).find((f) => f.key === "roles")?.value;
  if (typeof roles !== "object" || roles === null || Array.isArray(roles)) return null;
  const names: string[] = [];
  for (const [role, spec] of Object.entries(roles as Record<string, unknown>)) {
    const raw = typeof spec === "object" && spec !== null ? (spec as { count?: unknown }).count : undefined;
    const count = raw === undefined ? 1 : raw;
    if (typeof count !== "number" || !Number.isInteger(count) || count < 1) return null;
    if (count === 1) names.push(role);
    else for (let i = 1; i <= count; i++) names.push(`${role}-${i}`);
  }
  return names.length > 0 ? names : null;
}

// 艦の操作。--edit で起動したとき (index.html にトークンがあるとき) だけ入口を出す。
// どれも確認画面 (OpPanel) を経てから yamato の CLI を呼ぶ。
function ShipOps({ ship }: { ship: string }) {
  const [pending, setPending] = useState<OpSpec | null>(null);
  const [forSpan, setForSpan] = useState("");
  const [seats, setSeats] = useState("");
  const [span, setSpan] = useState("");
  const [seat, setSeat] = useState("");
  const [message, setMessage] = useState("");
  const [seatList, setSeatList] = useState<string[] | null>(null);

  useEffect(() => {
    let live = true;
    getShipConfig(ship).then(
      (c) => live && setSeatList(seatNames(c)),
      () => live && setSeatList(null),
    );
    return () => {
      live = false;
    };
  }, [ship]);

  const ask = (spec: Omit<OpSpec, "ship">) => setPending({ ship, ...spec });
  return (
    <div role="group" aria-label="艦の操作">
      <h2>艦の操作</h2>
      <div className={styles.row}>
        <label>稼働時間 <input value={forSpan} onChange={(e) => setForSpan(e.target.value)} placeholder="例 3h (省くと team.yaml の既定)" /></label>
        <label>一緒に起こす席 <input value={seats} onChange={(e) => setSeats(e.target.value)} placeholder="例 impl,review" /></label>
        <button onClick={() => ask({
          op: "up", params: { for: forSpan, seats }, title: `艦 ${ship} を起動する`, confirmLabel: "起動する",
          detail: "captain の席を起こし、稼働時間の上限を設定する (yamato up)。",
        })}>up</button>
      </div>
      <div className={styles.row}>
        <label>延ばす時間 <input value={span} onChange={(e) => setSpan(e.target.value)} placeholder="例 1h" /></label>
        <button onClick={() => ask({
          op: "extend", params: { span }, title: `艦 ${ship} の deadline を延ばす`, confirmLabel: "延ばす",
          detail: "稼働時間の上限を延ばす (yamato extend)。起動していない艦は断られる。",
        })}>extend</button>
      </div>
      <div className={styles.row}>
        <button onClick={() => ask({
          op: "down", params: {}, title: `艦 ${ship} を終業させる`, confirmLabel: "終業を指示する",
          detail: "動いている席に引き継ぎを書かせて止める (yamato down)。猶予を過ぎても残る席は強制停止される。",
        })}>down</button>
        <button className={styles.danger} onClick={() => ask({
          op: "down-force", params: {}, title: `艦 ${ship} を強制停止する (down --force)`, confirmLabel: "強制停止する",
          detail: "猶予なしで全席を今すぐ強制停止する。席は引き継ぎを書けず、作業の途中で切れる。",
        })}>down --force</button>
        <button className={styles.danger} onClick={() => ask({
          op: "halt", params: {}, title: `艦 ${ship} を緊急停止する (halt)`, confirmLabel: "緊急停止する",
          detail: "猶予なしで全席を強制停止する緊急停止 (yamato halt)。席は引き継ぎを書けず、作業の途中で切れる。",
        })}>halt</button>
      </div>
      <h2>席に送る</h2>
      <div className={styles.row}>
        <label>席 {seatList ? (
          <select value={seat} onChange={(e) => setSeat(e.target.value)}>
            <option value="">(選ぶ)</option>
            {seatList.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        ) : (
          <input value={seat} onChange={(e) => setSeat(e.target.value)} placeholder="例 pm" />
        )}</label>
        <label>本文 <textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={3} cols={48} /></label>
        <button onClick={() => ask({
          op: "send", params: { seat, message }, title: `席 ${seat || "(未入力)"} に送る`, confirmLabel: "送る",
          detail: `owner として席にメッセージを送る (yamato send --from owner)。止まっている席は起きる。本文: ${message}`,
        })}>send</button>
      </div>
      {pending && <OpPanel key={pending.op + pending.title} spec={pending} onClose={() => setPending(null)} />}
    </div>
  );
}

export function ShipPage() {
  const { name } = useParams();
  const ship = name ?? "";
  return (
    <>
      <h1>艦: {name}</h1>
      <p><Link to={`/ship/${encodeURIComponent(ship)}/config`}>設定</Link></p>
      {editToken() !== null && <ShipOps key={ship} ship={ship} />}
    </>
  );
}
