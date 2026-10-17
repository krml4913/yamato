import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getShipConfig, type ShipConfig } from "../api/client";

type Field = ShipConfig["fields"][number];

// 値が「名前 → オブジェクト」の項目 (roles・profiles など) は表にする。
function isTable(value: unknown): value is Record<string, Record<string, unknown>> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const rows = Object.values(value);
  return rows.length > 0 && rows.every((r) => typeof r === "object" && r !== null && !Array.isArray(r));
}

function show(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
}

function Table({ rows }: { rows: Record<string, Record<string, unknown>> }) {
  const columns = [...new Set(Object.values(rows).flatMap((r) => Object.keys(r)))];
  return (
    <table className="config-table">
      <thead>
        <tr>
          <th>名前</th>
          {columns.map((c) => <th key={c}>{c}</th>)}
        </tr>
      </thead>
      <tbody>
        {Object.entries(rows).map(([name, row]) => (
          <tr key={name}>
            <th scope="row">{name}</th>
            {columns.map((c) => <td key={c}>{show(row[c])}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Value({ field }: { field: Field }) {
  if (!field.set) return <p className="muted">未設定</p>;
  if (isTable(field.value)) return <Table rows={field.value} />;
  if (typeof field.value === "object" && field.value !== null) {
    return <pre>{JSON.stringify(field.value, null, 2)}</pre>;
  }
  return <p><code>{show(field.value)}</code></p>;
}

function Section({ field }: { field: Field }) {
  return (
    <section id={`field-${field.key}`} className="config-field">
      <h2>
        {field.key}
        {field.required && <small> 必須</small>}
        {field.type && <small> {field.type}</small>}
      </h2>
      {field.description && <p className="muted">{field.description}</p>}
      {field.enum && <p className="muted">選べる値: {field.enum.map(show).join(" / ")}</p>}
      <Value field={field} />
    </section>
  );
}

export function ConfigPage() {
  const { name = "" } = useParams();
  // 結果は艦名つきで持つ (艦を切り替えたとき、前の艦の表示を出さない)
  const [result, setResult] = useState<{ name: string; config?: ShipConfig; error?: string } | null>(null);

  useEffect(() => {
    let live = true;
    getShipConfig(name).then(
      (config) => live && setResult({ name, config }),
      (e: Error) => live && setResult({ name, error: e.message }),
    );
    return () => {
      live = false;
    };
  }, [name]);

  const current = result?.name === name ? result : null;
  const config = current?.config;
  const error = current?.error;

  return (
    <>
      <h1>設定: {name}</h1>
      <p><Link to={`/ship/${encodeURIComponent(name)}`}>艦のページへ</Link></p>
      {error && <p role="alert" className="config-error">読み込めない: {error}</p>}
      {!config && !error && <p>読み込み中</p>}
      {config && (
        <>
          <p className="muted">{config.path}</p>
          {config.pending.changed && (
            <p role="status" className="config-pending">
              次の yamato up で効く変更がある
              {config.pending.keys.length > 0 && ` (${config.pending.keys.join(", ")})`}
            </p>
          )}
          {config.fields.map((f) => <Section key={f.key} field={f} />)}
          <details>
            <summary>生の yaml</summary>
            <pre>{config.raw}</pre>
          </details>
        </>
      )}
    </>
  );
}
