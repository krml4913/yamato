import { useState } from "react";
import { commitEdit, EditApiError, previewEdit, type EditPreview } from "../api/client";
import { DiffView } from "../edit/DiffView";
import { SaveBar, saveBarStyles } from "../edit/SaveBar";
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
  const [doing, setDoing] = useState<"preview" | "commit" | null>(null);

  const dirty = text !== props.initial;

  async function run(kind: "preview" | "commit", f: () => Promise<void>) {
    setBusy(true);
    setDoing(kind);
    setError(null);
    try {
      await f();
    } catch (e) {
      setError(e instanceof EditApiError && e.code === "conflict" ? `${e.message} (差分を取り直す)` : (e as Error).message);
      setPv(null);
    } finally {
      setBusy(false);
      setDoing(null);
    }
  }

  const showDiff = () => run("preview", async () => setPv(await previewEdit({ ship, file, content: text })));
  const confirm = () =>
    run("commit", async () => {
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
      <SaveBar
        count={doing === "preview" ? "差分を取っている…" : doing === "commit" ? "保存している…" : pv?.changed && pv.valid ? "差分を確認した。確定で書く" : dirty ? "変更あり" : "変更なし"}
        busy={busy}
        error={error}
        panel={
          <>
            {pv && !pv.valid && (
              <ul role="alert" className={styles.error}>{pv.errors.map((m) => <li key={m}>{m}</li>)}</ul>
            )}
            {pv && pv.valid && !pv.changed && <p>変更なし</p>}
            {pv && pv.changed && pv.valid && <DiffView diff={pv.diff} />}
          </>
        }
      >
        <button onClick={showDiff} disabled={busy}>差分を見る</button>
        <button className={saveBarStyles.primary} onClick={confirm} disabled={busy || !pv || !pv.valid || !pv.changed}>確定</button>
        <button onClick={() => { setText(props.initial); setPv(null); setError(null); }} disabled={busy || !dirty}>元に戻す</button>
        <button onClick={props.onCancel} disabled={busy}>やめる</button>
      </SaveBar>
    </div>
  );
}
