import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getShipFile, type ShipFile } from "../api/files";
import { Markdown } from "../markdown/Markdown";

// roles/*.md・charter.md・knowledge.md を markdown で出す。
export function ShipFilePage() {
  const { name = "", file = "" } = useParams();
  const [state, setState] = useState<{ key: string; file?: ShipFile; error?: string }>({ key: "" });
  const key = `${name}\n${file}`;

  useEffect(() => {
    let live = true;
    getShipFile(name, file).then(
      (f) => live && setState({ key, file: f }),
      (e: Error) => live && setState({ key, error: e.message }),
    );
    return () => { live = false; };
  }, [name, file, key]);

  const cur = state.key === key ? state : { key };
  return (
    <>
      <p><Link to={`/ship/${encodeURIComponent(name)}`}>← {name}</Link></p>
      <h1>{file}</h1>
      {cur.error && <p role="alert">読めない: {cur.error}</p>}
      {cur.file && <Markdown text={cur.file.text} />}
      {!cur.file && !cur.error && <p>読み込み中</p>}
    </>
  );
}
