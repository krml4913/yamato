import { useState } from "react";
import { editToken, getShipItem } from "../api/client";
import { Markdown } from "../markdown/Markdown";
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
    <div role="group" aria-label={`艦 ${wait.ship} の判断 ${wait.id} を閉じる`}>
      <div className={styles.closeRow}>
        <label>決定 <input value={choice} onChange={(e) => setChoice(e.target.value)} placeholder="例 A" /></label>
        <label>理由 <input value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        <button onClick={() => setPending({
          op: "decide-close", ship: wait.ship, params: { id: wait.id, choice, reason },
          title: `艦 ${wait.ship} の判断 ${wait.id} を閉じる`, confirmLabel: "閉じる",
          detail: `owner として閉じる (yamato decide close)。止まっていたタスクが戻り、席に知らせが飛ぶ。「${wait.title}」`,
        })}>判断を閉じる</button>
      </div>
      {pending && <OpPanel spec={pending} onClose={() => setPending(null)} />}
    </div>
  );
}

// 判断の本文 (背景・選択肢・推し)。開いたときだけ読む。閉じるフォームは本文を読んだあとに本文の下へ出す
// (読まずに閉じる入口は作らない。本文が読めなかったときも、閉じる手段は残す)
function Body({ wait, editing }: { wait: Wait; editing: boolean }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<{ text: string } | { error: string } | null>(null);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && state === null) {
      getShipItem(wait.ship, wait.id)
        .then((it) => setState({ text: it.body }))
        .catch((e: Error) => setState({ error: e.message }));
    }
  };
  return (
    <div>
      <button aria-expanded={open} aria-label={`艦 ${wait.ship} の判断 ${wait.id} の本文`} onClick={toggle}>
        {open ? "本文を閉じる" : "本文を読む"}
      </button>
      {open && (state === null ? <p className={status.dim}>読み込み中…</p>
        : "error" in state ? <p role="alert" className={status.red}>{state.error}</p>
        : <div className={styles.waitBody}><Markdown text={state.text} /></div>)}
      {open && state !== null && editing && <CloseOne wait={wait} />}
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
                <Body wait={w} editing={editing} />
              </li>
            ))}
        </ul>
      )}
    </section>
  );
}
