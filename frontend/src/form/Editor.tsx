import { useState } from "react";
import status from "../status.module.css";
import styles from "./form.module.css";
import { defaultFor, isMap, kindOf, parseScalar, type Json, type Schema } from "./schema";

type Props = {
  schema: Schema;
  value: Json;
  onChange: (v: Json) => void;
  label: string; // アクセシブル名 (項目の path)
};

const text = (v: Json) => (typeof v === "string" ? v : JSON.stringify(v));
const describe = (s: Schema) => (typeof s.description === "string" ? s.description : null);

function Unsupported({ value }: { value: Json }) {
  return (
    <div>
      <pre>{JSON.stringify(value, null, 2)}</pre>
      <p className={status.dim}>この形はフォームで直せない。生の yaml で直す。</p>
    </div>
  );
}

function Remove({ onClick, what }: { onClick: () => void; what: string }) {
  return (
    <button type="button" className={styles.small} onClick={onClick} aria-label={`${what} を消す`}>
      消す
    </button>
  );
}

function AddKey({ onAdd, what }: { onAdd: (name: string) => void; what: string }) {
  const [name, setName] = useState("");
  return (
    <div className={styles.row}>
      <input value={name} onChange={(e) => setName(e.target.value)} aria-label={`${what} に足す名前`} placeholder="名前" />
      <button
        type="button"
        className={styles.small}
        disabled={name.trim() === ""}
        onClick={() => {
          onAdd(name.trim());
          setName("");
        }}
      >
        足す
      </button>
    </div>
  );
}

/** schema から組んだ入力欄 (再帰)。値の型・enum・説明は schema のまま。 */
export function Editor({ schema, value, onChange, label }: Props) {
  const k = kindOf(schema, value);
  switch (k.kind) {
    case "enum": {
      const i = k.options.findIndex((o) => JSON.stringify(o) === JSON.stringify(value));
      return (
        <select aria-label={label} value={i} onChange={(e) => onChange(k.options[Number(e.target.value)])}>
          {i < 0 && <option value={-1}>{text(value)} (選べる値にない)</option>}
          {k.options.map((o, n) => <option key={n} value={n}>{text(o)}</option>)}
        </select>
      );
    }
    case "boolean":
      return (
        <input type="checkbox" aria-label={label} checked={value === true} onChange={(e) => onChange(e.target.checked)} />
      );
    case "number":
      return (
        <input
          type="number"
          aria-label={label}
          min={k.min}
          step={k.integer ? 1 : "any"}
          value={typeof value === "number" ? value : ""}
          onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
        />
      );
    case "scalar": {
      const listId = `${label}-options`;
      return (
        <>
          <input
            aria-label={label}
            list={k.options.length ? listId : undefined}
            value={typeof value === "string" ? value : text(value)}
            onChange={(e) => onChange(parseScalar(e.target.value, k.types))}
          />
          {k.options.length > 0 && (
            <datalist id={listId}>{k.options.map((o, n) => <option key={n} value={text(o)} />)}</datalist>
          )}
        </>
      );
    }
    case "text": {
      const s = typeof value === "string" ? value : "";
      return s.includes("\n") ? (
        <textarea aria-label={label} rows={Math.min(12, s.split("\n").length + 1)} value={s} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input aria-label={label} value={s} onChange={(e) => onChange(e.target.value)} />
      );
    }
    case "list": {
      const items = Array.isArray(value) ? value : [];
      return (
        <div>
          {items.map((it, i) => (
            <div key={i} className={styles.row}>
              <Editor
                label={`${label}[${i}]`}
                schema={k.items}
                value={it}
                onChange={(v) => onChange(items.map((x, j) => (j === i ? v : x)))}
              />
              <Remove what={`${label}[${i}]`} onClick={() => onChange(items.filter((_, j) => j !== i))} />
            </div>
          ))}
          <button type="button" className={styles.small} onClick={() => onChange([...items, defaultFor(k.items)])}>
            {label} に足す
          </button>
        </div>
      );
    }
    case "object": {
      const obj = isMap(value) ? value : {};
      const known = Object.keys(k.props);
      const extras = Object.keys(obj).filter((key) => !known.includes(key));
      const set = (key: string, v: Json | undefined) => {
        const next = { ...obj };
        if (v === undefined) delete next[key];
        else next[key] = v;
        onChange(next);
      };
      return (
        <div className={styles.group}>
          {known.map((key) => {
            const sub = k.props[key];
            const name = `${label}.${key}`;
            const v = obj[key];
            return (
              <div key={key} className={styles.item}>
                <div className={styles.head}>
                  <strong>{key}</strong>
                  {k.required.includes(key) && <small> 必須</small>}
                  {v !== undefined && !k.required.includes(key) && <Remove what={name} onClick={() => set(key, undefined)} />}
                </div>
                {describe(sub) && <p className={status.dim}>{describe(sub)}</p>}
                {v === undefined ? (
                  <button type="button" className={styles.small} onClick={() => set(key, defaultFor(sub))} aria-label={`${name} を設定する`}>
                    未設定 (設定する)
                  </button>
                ) : (
                  <Editor label={name} schema={sub} value={v} onChange={(nv) => set(key, nv)} />
                )}
              </div>
            );
          })}
          {extras.map((key) => {
            const name = `${label}.${key}`;
            return (
              <div key={key} className={styles.item}>
                <div className={styles.head}>
                  <strong>{key}</strong>
                  {k.extra && <Remove what={name} onClick={() => set(key, undefined)} />}
                </div>
                {k.extra ? (
                  <Editor label={name} schema={k.extra} value={obj[key]} onChange={(nv) => set(key, nv)} />
                ) : (
                  <Unsupported value={obj[key]} />
                )}
              </div>
            );
          })}
          {k.extra && <AddKey what={label} onAdd={(key) => !(key in obj) && set(key, defaultFor(k.extra as Schema))} />}
        </div>
      );
    }
    default:
      return <Unsupported value={value} />;
  }
}
