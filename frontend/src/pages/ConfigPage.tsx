import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { editToken, getShipConfig, type ShipConfig } from "../api/client";
import status from "../status.module.css";
import styles from "./config.module.css";
import { ConfigForm } from "./ConfigForm";
import { ConfigValue } from "./ConfigValue";
import { FieldHead, groupFields, Groups, Toc, type Field } from "./configGroups";

function Section({ field }: { field: Field }) {
  return (
    <section id={`field-${field.key}`} className={styles.field}>
      <FieldHead field={field} />
      {field.enum && <p className={styles.desc}>選べる値: {field.enum.map((v) => String(v)).join(" / ")}</p>}
      <ConfigValue field={field} />
    </section>
  );
}

export function ConfigPage() {
  const { name = "" } = useParams();
  // 結果は艦名つきで持つ (艦を切り替えたとき、前の艦の表示を出さない)
  const [reload, setReload] = useState(0);
  const [saved, setSaved] = useState(false);
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
  }, [name, reload]);

  const current = result?.name === name ? result : null;
  const config = current?.config;
  const error = current?.error;

  return (
    <>
      <h1>設定: {name}</h1>
      <p><Link to={`/ship/${encodeURIComponent(name)}`}>艦のページへ</Link></p>
      {error && <p role="alert" className={styles.error}>読み込めない: {error}</p>}
      {!config && !error && <p>読み込み中</p>}
      {config && (
        <>
          <p className={status.dim}>{config.path}</p>
          {config.pending.changed && (
            <p role="status" className={styles.pending}>
              次の yamato up で効く変更がある
              {config.pending.keys.length > 0 && ` (${config.pending.keys.join(", ")})`}
            </p>
          )}
          {editToken() && (
            <p><Link to={`/ship/${encodeURIComponent(name)}/config/raw`}>生の team.yaml を直す (フォームで扱えない項目はこちら)</Link></p>
          )}
          <Toc groups={groupFields(config.fields)} />
          {editToken() ? (
            <ConfigForm key={config.raw} name={name} config={config} saved={saved}
              onSaved={() => {
                setSaved(true);
                setReload((n) => n + 1);
              }}
              onDirty={() => setSaved(false)}
            />
          ) : (
            <Groups groups={groupFields(config.fields)} render={(f) => <Section key={f.key} field={f} />} />
          )}
          <details>
            <summary>生の yaml</summary>
            <pre>{config.raw}</pre>
          </details>
        </>
      )}
    </>
  );
}
