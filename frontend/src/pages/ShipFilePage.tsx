import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getShipFile, type ShipFile } from "../api/files";
import { editToken } from "../api/client";
import { Markdown } from "../markdown/Markdown";
import { parseBlocks } from "../markdown/parse";
import { SavedNotice } from "../edit/SaveBar";
import { MdEditor } from "./MdEditor";
import styles from "./mdEditor.module.css";

// roles/*.md・charter.md・knowledge.md を markdown で出す。--edit で起動していれば role・charter は直せる
// (knowledge.md は memory apply の記録のため読むだけ)。
export function ShipFilePage() {
  const { name = "", file = "" } = useParams();
  const [state, setState] = useState<{ key: string; file?: ShipFile; error?: string }>({ key: "" });
  const [reload, setReload] = useState(0);
  const [savedKey, setSavedKey] = useState<string | null>(null);
  const key = `${name}\n${file}`;
  const [editKey, setEditKey] = useState<string | null>(null);
  const editing = editKey === key;   // 別の file に移ったら編集は閉じる
  const setEditing = (on: boolean) => setEditKey(on ? key : null);

  useEffect(() => {
    let live = true;
    getShipFile(name, file).then(
      (f) => live && setState({ key, file: f }),
      (e: Error) => live && setState({ key, error: e.message }),
    );
    return () => { live = false; };
  }, [name, file, key, reload]);

  const cur = state.key === key ? state : { key };
  // 題は 1 つだけ: md が h1 で始まればそれを題にし、無ければ (roles/*.md など) ファイル名を h1 にする
  const first = cur.file ? parseBlocks(cur.file.text).find((b) => b.t !== "hr") : undefined;
  const hasTitle = first?.t === "heading" && first.level === 1;
  return (
    <>
      <p className={styles.crumb}><Link to={`/ship/${encodeURIComponent(name)}`}>← {name}</Link> {hasTitle && <> / <span>{file}</span></>}</p>
      {cur.file && !hasTitle && <h1 className={styles.title}>{file}</h1>}
      {savedKey === key && !editing && <SavedNotice />}
      {cur.error && <p role="alert">読めない: {cur.error}</p>}
      {cur.file && !(editing && cur.file.kind !== "knowledge") && (
        <>
          {editToken() && cur.file.kind !== "knowledge" && <p><button onClick={() => { setSavedKey(null); setEditing(true); }}>編集</button></p>}
          <Markdown text={cur.file.text} toc />
        </>
      )}
      {cur.file && editing && cur.file.kind !== "knowledge" && (
        <MdEditor
          ship={name}
          file={file}
          initial={cur.file.text}
          onSaved={() => { setEditing(false); setSavedKey(key); setReload((n) => n + 1); }}
          onCancel={() => setEditing(false)}
        />
      )}
      {!cur.file && !cur.error && <p>読み込み中</p>}
    </>
  );
}
