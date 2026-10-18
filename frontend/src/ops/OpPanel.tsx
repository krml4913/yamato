import { useEffect, useRef, useState } from "react";
import { requestRefresh } from "../api/refresh";
import { EditApiError, runOp, type OpResult } from "../api/client";
import styles from "./ops.module.css";

export type OpSpec = {
  op: string;                       // /api/v1/ops/{op}
  ship: string;
  params: Record<string, string>;
  title: string;                    // 例: 「席 impl を止める」
  detail?: string;                  // 確認画面に出す説明 (何が起きるか)
  confirmLabel: string;
};

// 操作の共通の確認画面と結果。ブラウザの alert は使わず、ページの中で「確認 → 実行 → 出力と exit code」を見せる。
// 出力は text として描く (HTML にしない)。操作を足すときは OpSpec を渡すだけ。
export function OpPanel({ spec, onClose, onDone }: { spec: OpSpec; onClose: () => void; onDone?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<OpResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const panel = useRef<HTMLElement>(null);
  // 押したボタン。閉じたらフォーカスをここに戻す
  const opener = useRef<Element | null>(document.activeElement);

  // 出たらそこへスクロールしてフォーカスを移す。結果に切り替わるときも (確定のボタンが消えるので) 同じ。
  useEffect(() => {
    const el = panel.current;
    if (!el) return;
    el.scrollIntoView?.({ block: "nearest" });
    el.focus({ preventScroll: true });
  }, [result]);

  function close() {
    onClose();
    const o = opener.current;
    if (o instanceof HTMLElement && o.isConnected) o.focus();
  }

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await runOp(spec.op, { ship: spec.ship, params: spec.params, confirmed: true });
      setResult(r);
      if (r.exit_code === 0 && !r.timed_out) requestRefresh();
      onDone?.();
    } catch (e) {
      setError(e instanceof EditApiError || e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section ref={panel} tabIndex={-1} className={styles.panel} aria-busy={busy} role="region" aria-label={spec.title}>
      <h3>{spec.title}</h3>
      {!result && (
        <>
          {spec.detail && <p>{spec.detail}</p>}
          <p className={styles.cmd}>艦 {spec.ship} / {Object.entries(spec.params).map(([k, v]) => `${k}=${v}`).join(" ")}</p>
        </>
      )}
      {error && <p role="alert" className={styles.error}>{error}</p>}
      {result && (
        <>
          <p className={result.exit_code === 0 ? styles.ok : styles.error} role="status">
            {result.timed_out ? "30 秒待ったが終わらない (止めてはいない。裏で続いている)" : `exit code ${result.exit_code}`}
          </p>
          <p className={styles.cmd}>{result.argv.join(" ")}</p>
          <pre className={styles.output} aria-label="出力">{result.output || "(出力なし)"}</pre>
          {result.timed_out && <p className={styles.cmd}>結果は艦の status で確かめる。出力はこのファイルに続く: {result.log}</p>}
          {result.truncated && <p className={styles.cmd}>(長いので後ろを切った)</p>}
        </>
      )}
      <div className={styles.actions}>
        {!result && <button onClick={run} disabled={busy}>{busy ? "実行中…" : spec.confirmLabel}</button>}
        <button onClick={close} disabled={busy}>{result ? "閉じる" : "やめる"}</button>
      </div>
    </section>
  );
}
