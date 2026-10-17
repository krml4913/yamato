import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getShipFile, type ShipFile } from "../api/files";
import { editToken } from "../api/client";
import { Markdown } from "../markdown/Markdown";
import { MdEditor } from "./MdEditor";

// roles/*.md・charter.md・knowledge.md を markdown で出す。--edit で起動していれば role・charter は直せる
// (knowledge.md は memory apply の記録のため読むだけ)。
export function ShipFilePage() {
  const { name = "", file = "" } = useParams();
  const [state, setState] = useState<{ key: string; file?: ShipFile; error?: string }>({ key: "" });
  const [editing, setEditing] = useState(false);
  const [reload, setReload] = useState(0);
  const key = `${name}\n${file}`;

  useEffect(() => {
    let live = true;
    getShipFile(name, file).then(
      (f) => live && setState({ key, file: f }),
      (e: Error) => live && setState({ key, error: e.message }),
    );
    return () => { live = false; };
  }, [name, file, key, reload]);

  const cur = state.key === key ? state : { key };
  return (
    <>
      <p><Link to={`/ship/${encodeURIComponent(name)}`}>← {name}</Link></p>
      <h1>{file}</h1>
      {cur.error && <p role="alert">読めない: {cur.error}</p>}
      {cur.file && !editing && (
        <>
          {editToken() && cur.file.kind !== "knowledge" && <p><button onClick={() => setEditing(true)}>編集</button></p>}
          <Markdown text={cur.file.text} />
        </>
      )}
      {cur.file && editing && (
        <MdEditor
          ship={name}
          file={file}
          initial={cur.file.text}
          onSaved={() => { setEditing(false); setReload((n) => n + 1); }}
          onCancel={() => setEditing(false)}
        />
      )}
      {!cur.file && !cur.error && <p>読み込み中</p>}
    </>
  );
}
