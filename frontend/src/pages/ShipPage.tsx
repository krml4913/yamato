import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { shipFilePath } from "../api/files";
import { editToken, getOverview, getShipConfig, type Overview, type ShipConfig } from "../api/client";
import { ShipCard } from "../overview/ShipCard";
import { isRunning } from "../overview/summary";
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
function ShipOps({ ship, running }: { ship: string; running: boolean | null }) {
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
  // 動いている艦では up、止まっている艦では down・extend を目立たせない (押せるが、今は要らない側)
  const quiet = (hide: boolean | null) => (hide ? styles.quiet : undefined);
  return (
    <>
      <div className={styles.section} role="group" aria-label="メッセージを送る">
        <h3>席に送る</h3>
        <div className={styles.row}>
          <label>席 {seatList ? (
            <select value={seat} onChange={(e) => setSeat(e.target.value)}>
              <option value="">(選ぶ)</option>
              {seatList.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          ) : (
            <input value={seat} onChange={(e) => setSeat(e.target.value)} placeholder="例 pm" />
          )}</label>
          <label className={styles.sendBody}>本文 <textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={3} cols={48} /></label>
          <button onClick={() => ask({
            op: "send", params: { seat, message }, title: `席 ${seat || "(未入力)"} に送る`, confirmLabel: "送る",
            detail: `owner として席にメッセージを送る (yamato send --from owner)。止まっている席は起きる。本文: ${message}`,
          })}>send</button>
        </div>
      </div>
      <div className={styles.section} role="group" aria-label="艦の操作">
        <h3>艦の操作</h3>
        <p className={styles.runStateRow}>
          <span className={`${styles.runState} ${running ? styles.runStateOn : ""}`} role="status">
            {running === null ? "艦が動いているかは読めなかった" : running ? "いま動いている" : "いま止まっている"}
          </span>
        </p>
        <div className={styles.usual} role="group" aria-label="普段の操作">
          <h4>普段の操作</h4>
          <div className={styles.row}>
            <label>稼働時間 <input className={styles.wide} value={forSpan} onChange={(e) => setForSpan(e.target.value)} placeholder="例 3h (省くと既定)" /></label>
            <label>一緒に起こす席 <input className={styles.wide} value={seats} onChange={(e) => setSeats(e.target.value)} placeholder="例 impl,review" /></label>
            <button className={quiet(running === true)} onClick={() => ask({
              op: "up", params: { for: forSpan, seats }, title: `艦 ${ship} を起動する`, confirmLabel: "起動する",
              detail: "captain の席を起こし、稼働時間の上限を設定する (yamato up)。",
            })}>up</button>
          </div>
          <div className={styles.row}>
            <label>延ばす時間 <input className={styles.wide} value={span} onChange={(e) => setSpan(e.target.value)} placeholder="例 1h" /></label>
            <button className={quiet(running === false)} onClick={() => ask({
              op: "extend", params: { span }, title: `艦 ${ship} の deadline を延ばす`, confirmLabel: "延ばす",
              detail: "稼働時間の上限を延ばす (yamato extend)。起動していない艦は断られる。",
            })}>extend</button>
          </div>
          <div className={styles.row}>
            <button className={quiet(running === false)} onClick={() => ask({
              op: "down", params: {}, title: `艦 ${ship} を終業させる`, confirmLabel: "終業を指示する",
              detail: "動いている席に引き継ぎを書かせて止める (yamato down)。猶予を過ぎても残る席は強制停止される。",
            })}>down</button>
            <span className={styles.hint}>席に引き継ぎを書かせてから止める</span>
          </div>
        </div>
        <fieldset className={styles.dangerZone}>
          <legend>危ない操作</legend>
          <p className={styles.hint}>どちらも猶予なしで全席を止める。席は引き継ぎを書けず、作業の途中で切れる。</p>
          <div className={styles.dangerItem}>
            <button className={styles.danger} onClick={() => ask({
              op: "down-force", params: {}, title: `艦 ${ship} を強制停止する (down --force)`, confirmLabel: "強制停止する",
              detail: "終業 (down) の強制版。猶予を待たず、動いている席を今すぐ止める。席は引き継ぎを書けず、作業の途中で切れる。止めた理由は down-force として記録に残る。",
            })}>down --force</button>
            <span className={styles.hint}>down の猶予を待たずに終わらせる。</span>
          </div>
          <div className={styles.dangerItem}>
            <button className={styles.danger} onClick={() => ask({
              op: "halt", params: {}, title: `艦 ${ship} を緊急停止する (halt)`, confirmLabel: "緊急停止する",
              detail: "緊急停止 (yamato halt)。down --force と同じ強制停止で、猶予なしに全席を止める。席は引き継ぎを書けず、作業の途中で切れる。止めた理由は halt として記録に残る。",
            })}>halt</button>
            <span className={styles.hint}>down --force と同じ強制停止。違うのは記録に残る理由が halt になる点だけ。</span>
          </div>
        </fieldset>
      </div>
      {pending && <OpPanel key={pending.op + pending.title} spec={pending} onClose={() => setPending(null)} />}
    </>
  );
}

type MdFile = NonNullable<ShipConfig["files"]>[number];

// md の表示名。roles/pm.md → pm、charter.md → charter、knowledge.md → knowledge
export function mdName(file: string): string {
  const base = file.split("/").pop() ?? file;
  return base.replace(/\.md$/, "");
}

// ShipConfig.files に出る md を、役割・charter・knowledge に分けて名前で出す。
// _ で始まる役割は yamato 内部の役割なので小さく別に出す。取れなければ何も出さない。
function ShipFiles({ ship }: { ship: string }) {
  const [files, setFiles] = useState<MdFile[]>([]);
  useEffect(() => {
    let live = true;
    getShipConfig(ship).then(
      (c) => live && setFiles(c.files ?? []),
      () => live && setFiles([]),
    );
    return () => {
      live = false;
    };
  }, [ship]);
  if (files.length === 0) return null;
  const link = (f: MdFile) => <Link to={shipFilePath(ship, f.file)} title={f.file}>{mdName(f.file)}</Link>;
  const roles = files.filter((f) => f.kind === "role");
  const normal = roles.filter((f) => !mdName(f.file).startsWith("_"));
  const internal = roles.filter((f) => mdName(f.file).startsWith("_"));
  const others = files.filter((f) => f.kind !== "role");
  return (
    <div className={styles.section} role="group" aria-label="役割と方針のファイル">
      <h2>役割と方針のファイル</h2>
      {normal.length > 0 && (
        <div className={styles.mdGroup}>
          <h3>役割ごとの指示</h3>
          <ul className={styles.mdList}>{normal.map((f) => <li key={f.file}>{link(f)}</li>)}</ul>
        </div>
      )}
      {others.length > 0 && (
        <div className={styles.mdGroup}>
          <h3>艦の方針と共有の知見 (charter・knowledge)</h3>
          <ul className={styles.mdList}>{others.map((f) => <li key={f.file}>{link(f)}</li>)}</ul>
        </div>
      )}
      {internal.length > 0 && (
        <div className={`${styles.mdGroup} ${styles.mdInternal}`}>
          <h3>内部の役割</h3>
          <ul className={styles.mdList}>{internal.map((f) => <li key={f.file}>{link(f)}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

// その艦の状態・席・board・PR・日報。一覧の ShipCard をそのまま使い、取れなければ何も出さない。
function ShipStatus({ ship, onRunning, children }: { ship: string; onRunning: (r: boolean | null) => void; children?: React.ReactNode }) {
  const [data, setData] = useState<Overview | null>(null);
  // 読み込みが済む (成否は問わない) まで何も出さない。済んでから出せば、操作の入力が途中で作り直されない
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    let live = true;
    getOverview().then(
      (d) => {
        if (!live) return;
        setData(d);
        setSettled(true);
        const s = Array.isArray(d.ships) ? d.ships.find((x) => x.name === ship) : undefined;
        onRunning(s?.state ? isRunning(s) : null);
      },
      () => {
        if (!live) return;
        onRunning(null);
        setSettled(true);
      },
    );
    return () => {
      live = false;
    };
  }, [ship, onRunning]);
  const s = data && Array.isArray(data.ships) ? data.ships.find((x) => x.name === ship) : undefined;
  if (!settled) return null;
  // 状態が読めなくても操作は出す (操作は状態に依らない)
  if (!data || !s) return <>{children}</>;
  return <ShipCard ship={s} now={data.generated_at} waits={data.waits ?? []} showName={false}>{children}</ShipCard>;
}

export function ShipPage() {
  const { name } = useParams();
  const ship = name ?? "";
  const [running, setRunning] = useState<boolean | null>(null);
  return (
    <>
      <h1>艦: {name}</h1>
      <p><Link to={`/ship/${encodeURIComponent(ship)}/config`}>設定</Link></p>
      <ShipStatus key={`status-${ship}`} ship={ship} onRunning={setRunning}>
        {editToken() !== null && <ShipOps key={ship} ship={ship} running={running} />}
      </ShipStatus>
      <ShipFiles key={`files-${ship}`} ship={ship} />
    </>
  );
}
