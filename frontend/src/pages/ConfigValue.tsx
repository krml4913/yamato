import status from "../status.module.css";
import table from "../table.module.css";
import styles from "./config.module.css";
import { omittedHint, type Field } from "./configGroups";

type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);

// 値が「名前 → オブジェクト」の項目 (roles・profiles など) は表にする。
function isTable(value: unknown): value is Record<string, Rec> {
  if (!isRec(value)) return false;
  const rows = Object.values(value);
  return rows.length > 0 && rows.every(isRec);
}

function Scalar({ v }: { v: unknown }) {
  if (v === null || v === undefined || v === "") return <span className={status.dim}>(空)</span>;
  return <span className={styles.scalar}>{typeof v === "string" ? v : String(v)}</span>;
}

/** セルや入れ子の中の値: 鍵: 値の入れ子のリスト、配列は箇条、それ以外は文字。 */
function Nested({ v }: { v: unknown }) {
  if (Array.isArray(v)) {
    if (v.length === 0) return <span className={status.dim}>(空)</span>;
    return <ul className={styles.list}>{v.map((x, i) => <li key={i}><Nested v={x} /></li>)}</ul>;
  }
  if (isRec(v)) {
    const entries = Object.entries(v);
    if (entries.length === 0) return <span className={status.dim}>(空)</span>;
    return (
      <dl className={styles.kv}>
        {entries.map(([k, x]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd><Nested v={x} /></dd>
          </div>
        ))}
      </dl>
    );
  }
  return <Scalar v={v} />;
}

function Table({ rows }: { rows: Record<string, Rec> }) {
  const columns = [...new Set(Object.values(rows).flatMap((r) => Object.keys(r)))];
  return (
    <table className={`${table.table} ${styles.valueTable}`}>
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
            {columns.map((c) => <td key={c}>{c in row ? <Nested v={row[c]} /> : null}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** 閲覧の値。未設定 (省略時の値や動作つき) と、書かれている値を見分けられる。 */
export function ConfigValue({ field }: { field: Field }) {
  if (!field.set) {
    const hint = omittedHint(field);
    return (
      <p className={styles.unset}>
        <span className={styles.unsetLabel}>未設定</span>
        {hint && <span className={styles.hint}> → {hint}</span>}
      </p>
    );
  }
  if (isTable(field.value)) return <Table rows={field.value} />;
  if (typeof field.value === "object" && field.value !== null) {
    return <div className={styles.value}><Nested v={field.value} /></div>;
  }
  return <p className={styles.value}><Scalar v={field.value} /></p>;
}
