import { useState } from "react";
import { editToken } from "../api/client";
import { OpPanel, type OpSpec } from "../ops/OpPanel";
import type { Wait } from "./types";
import status from "../status.module.css";
import styles from "./overview.module.css";

// 判断待ち 1 件を閉じる入口。--edit で起動したとき (index.html にトークンがあるとき) だけ出す
function CloseOne({ wait }: { wait: Wait }) {
  const [choice, setChoice] = useState("");
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState<OpSpec | null>(null);
  return (
    <div role="group" aria-label={`判断 ${wait.id} を閉じる`}>
      <div className={styles.closeRow}>
        <label>決定 <input value={choice} onChange={(e) => setChoice(e.target.value)} placeholder="例 A" /></label>
        <label>理由 <input value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        <button onClick={() => setPending({
          op: "decide-close", ship: wait.ship, params: { id: wait.id, choice, reason },
          title: `判断 ${wait.id} を閉じる`, confirmLabel: "閉じる",
          detail: `owner として閉じる (yamato decide close)。止まっていたタスクが戻り、席に知らせが飛ぶ。「${wait.title}」`,
        })}>判断を閉じる</button>
      </div>
      {pending && <OpPanel spec={pending} onClose={() => setPending(null)} />}
    </div>
  );
}

export function Waits({ waits }: { waits: Wait[] }) {
  const editing = editToken() !== null;
  return (
    <section className={`${styles.card} ${waits.length ? styles.top : styles.quiet}`}>
      <h2>owner の判断待ち ({waits.length})</h2>
      {waits.length === 0 ? (
        <p className={status.ok}>owner の判断待ちは無い</p>
      ) : (
        <ul>
            {waits.map((w) => (
              <li key={`${w.ship}/${w.id}`}>
                {w.urgent && <span className={`${styles.tag} ${styles.urgent}`}>急ぎ</span>}
                <b>{w.ship}</b> 判断 {w.id}: {w.title} <span className={status.dim}>({w.age || "?"} 待ち)</span>
                {w.recommendation && (<><br /><span className={status.dim}>推し: {w.recommendation}</span></>)}
                {editing && <CloseOne wait={w} />}
              </li>
            ))}
        </ul>
      )}
    </section>
  );
}
