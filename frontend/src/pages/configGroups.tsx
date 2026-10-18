import type { ReactNode } from "react";
import type { ShipConfig } from "../api/client";
import styles from "./config.module.css";

export type Field = ShipConfig["fields"][number];

// 項目のまとまり。schema にはまとまりの情報が無いので、画面側のこの 1 か所で決める。
// ここに無い項目 (schema に後から足されたもの) は最後の「その他」に入る。
const GROUPS: { id: string; title: string; keys: string[] }[] = [
  { id: "basics", title: "艦の基本", keys: ["name", "template", "workspace", "hub"] },
  {
    id: "seats",
    title: "席と役割",
    keys: ["roles", "profiles", "up_seats", "talk_default", "setting_sources", "settings", "env_unset", "deny", "inject"],
  },
  { id: "time", title: "時間と予算", keys: ["time_limit", "grace", "last_call", "watch", "context_windows", "seat_stop"] },
  { id: "git", title: "git と PR", keys: ["git", "board", "decisions"] },
];
const OTHER = { id: "other", title: "その他" };

export type Group = { id: string; title: string; fields: Field[] };

export function groupFields(fields: Field[]): Group[] {
  const known = new Set(GROUPS.flatMap((g) => g.keys));
  const groups = GROUPS.map((g) => ({
    id: g.id,
    title: g.title,
    fields: g.keys.map((k) => fields.find((f) => f.key === k)).filter((f): f is Field => !!f),
  }));
  groups.push({ ...OTHER, fields: fields.filter((f) => !known.has(f.key)) });
  return groups.filter((g) => g.fields.length > 0);
}

/** 省略したときの値や動作: schema の default、無ければ説明文の「省略時 …」。 */
export function omittedHint(f: Field): string | null {
  const d = (f.schema as { default?: unknown } | null)?.default;
  if (d !== undefined) return typeof d === "string" ? d : JSON.stringify(d);
  const m = f.description?.match(/省略時[^。]*/);
  return m ? m[0] : null;
}

/** ページの頭の目次 (まとまりへのリンク)。 */
export function Toc({ groups }: { groups: Group[] }) {
  return (
    <nav aria-label="目次" className={styles.toc}>
      <ul>
        {groups.map((g) => (
          <li key={g.id}><a href={`#group-${g.id}`}>{g.title}</a></li>
        ))}
      </ul>
    </nav>
  );
}

/** まとまりごとの節。項目の中身 (閲覧と編集で違う) は render が決める。 */
export function Groups({ groups, render }: { groups: Group[]; render: (f: Field) => ReactNode }) {
  return (
    <>
      {groups.map((g) => (
        <section key={g.id} id={`group-${g.id}`} aria-labelledby={`group-${g.id}-title`} className={styles.group}>
          <h2 id={`group-${g.id}-title`}>{g.title}</h2>
          {g.fields.map((f) => render(f))}
        </section>
      ))}
    </>
  );
}

/** 表・入れ子の大きい値は全幅で下に置く。小さい値だけ 2 段 (名前と説明 / 値) にする。 */
export function isWide(f: Field, editing: boolean): boolean {
  if (editing) return f.type === "object" || f.type === "array";
  const v = f.value;
  if (!f.set || typeof v !== "object" || v === null) return false;
  if (Array.isArray(v)) return v.some((x) => typeof x === "object" && x !== null);
  return Object.values(v).some((x) => typeof x === "object" && x !== null && !Array.isArray(x));
}

/** 項目の見出しと説明。値は主役なので説明は小さく薄くする。 */
export function FieldHead({ field }: { field: Field }) {
  return (
    <div className={styles.head}>
      <h3>
        <code>{field.key}</code>
        {field.required && <small className={styles.badge}>必須</small>}
        {field.type && <small className={styles.badge}>{field.type}</small>}
      </h3>
      {field.description && <p className={styles.desc}>{field.description}</p>}
    </div>
  );
}
