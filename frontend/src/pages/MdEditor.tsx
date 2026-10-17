import { useState } from "react";
import { commitEdit, EditApiError, previewEdit, type EditPreview } from "../api/client";
import styles from "./mdEditor.module.css";

// 書き込みは 2 段。差分 (preview) を見てから確定 (commit) する。
export function MdEditor(props: {
  ship: string;
  file: string;
  initial: string;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const { ship, file } = props;
  const [text, setText] = useState(props.initial);
  const [pv, setPv] = useState<EditPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(f: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await f();
    } catch (e) {
      setError(e instanceof EditApiError && e.code === "conflict" ? `${e.message} (差分を取り直す)` : (e as Error).message);
      setPv(null);
    } finally {
      setBusy(false);
    }
  }

  const showDiff = () => run(async () => setPv(await previewEdit({ ship, file, content: text })));
  const confirm = () =>
    run(async () => {
      if (!pv) return;
      await commitEdit({ ship, file, content: text, base_hash: pv.base_hash });
      props.onSaved();
    });

  return (
    <div className={styles.editor}>
      <textarea
        className={styles.text}
        aria-label={`${file} の中身`}
        value={text}
        onChange={(e) => { setText(e.target.value); setPv(null); }}
      />
      {error && <p role="alert" className={styles.error}>{error}</p>}
      {pv && !pv.valid && (
        <ul role="alert" className={styles.error}>{pv.errors.map((m) => <li key={m}>{m}</li>)}</ul>
      )}
      {pv && pv.valid && !pv.changed && <p>変更なし</p>}
      {pv && pv.changed && <pre className={styles.diff} aria-label="差分">{pv.diff}</pre>}
      <div className={styles.actions}>
        <button onClick={showDiff} disabled={busy}>差分を見る</button>
        <button onClick={confirm} disabled={busy || !pv || !pv.valid || !pv.changed}>確定</button>
        <button onClick={props.onCancel} disabled={busy}>やめる</button>
      </div>
    </div>
  );
}
