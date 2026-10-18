import { useState } from "react";
import { commitEdit, EditApiError, previewEdit, type EditPreview, type ShipConfig } from "../api/client";
import { Editor } from "../form/Editor";
import { defaultFor, diffFields, type Json, type Schema } from "../form/schema";
import status from "../status.module.css";
import styles from "./config.module.css";
import { FieldHead, groupFields, Groups, type Field } from "./configGroups";
import form from "../form/form.module.css";

type Draft = Record<string, Json | undefined>;

const initial = (fields: Field[]): Draft =>
  Object.fromEntries(fields.map((f) => [f.key, f.set ? (f.value as Json) : undefined]));

function message(e: unknown): string {
  if (e instanceof EditApiError) return e.detail ? `${e.message} (${e.detail})` : e.message;
  return e instanceof Error ? e.message : String(e);
}

/** --edit のときの設定ページ: schema から組んだフォーム → 差分と検証 → 保存。 */
export function ConfigForm({ name, config, saved, onSaved, onDirty }: {
  name: string;
  config: ShipConfig;
  saved: boolean;
  onSaved: () => void;
  onDirty: () => void;
}) {
  const base = initial(config.fields);
  const [draft, setDraft] = useState<Draft>(base);
  const [prev, setPrev] = useState<EditPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ops = diffFields(base, draft);
  const change = (key: string, v: Json | undefined) => {
    setDraft({ ...draft, [key]: v });
    setPrev(null);
    onDirty();
    setError(null);
  };

  async function check() {
    setBusy(true);
    setError(null);
    try {
      setPrev(await previewEdit({ ship: name, file: "team.yaml", ops }));
    } catch (e) {
      setPrev(null);
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!prev) return;
    setBusy(true);
    setError(null);
    try {
      await commitEdit({ ship: name, file: "team.yaml", ops, base_hash: prev.base_hash });
      setPrev(null);
      onSaved();
    } catch (e) {
      setError(e instanceof EditApiError && e.code === "conflict" ? `${e.message}。読み込み直して、もう一度直す` : message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {saved && (
        <p role="status" className={form.saved}>
          保存した。次の yamato up で効く (自動では up しない)。
        </p>
      )}
      <Groups groups={groupFields(config.fields)} render={(f) => {
        const v = draft[f.key];
        const schema = f.schema as Schema | null;
        return (
          <section key={f.key} id={`field-${f.key}`} className={styles.field}>
            <FieldHead field={f} />
            {v === undefined ? (
              <button
                type="button"
                disabled={!schema}
                onClick={() => change(f.key, defaultFor(schema ?? {}))}
                aria-label={`${f.key} を設定する`}
              >
                未設定 (設定する)
              </button>
            ) : (
              <>
                <Editor label={f.key} schema={schema ?? {}} value={v} onChange={(nv) => change(f.key, nv)} />
                {!f.required && (
                  <p>
                    <button type="button" className={form.small} onClick={() => change(f.key, undefined)} aria-label={`${f.key} を未設定に戻す`}>
                      未設定に戻す
                    </button>
                  </p>
                )}
              </>
            )}
          </section>
        );
      }} />
      <div className={form.actions}>
        <button type="button" disabled={busy || ops.length === 0} onClick={check}>差分を確認</button>
        <button type="button" disabled={busy || !prev || !prev.valid || !prev.changed} onClick={save}>保存</button>
      </div>
      {error && <p role="alert" className={styles.error}>{error}</p>}
      {prev && !prev.valid && (
        <div role="alert" className={styles.error}>
          <p>検証に通らない。直してからもう一度確認する。</p>
          <ul>{prev.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
        </div>
      )}
      {prev && prev.valid && (
        <>
          <h2>差分</h2>
          {prev.changed ? <pre className={form.diff}>{prev.diff}</pre> : <p className={status.dim}>変更なし</p>}
        </>
      )}
    </>
  );
}
