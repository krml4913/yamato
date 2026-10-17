// team.schema.json の定義 (API が項目ごとに返す) からフォームの形を決める。項目名・enum・説明は
// ここに固定しない。扱えない形は kind: "unsupported" にして、画面は生の yaml へ案内する。

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type Schema = Record<string, unknown>;

export type Kind =
  | { kind: "enum"; options: Json[] }
  | { kind: "boolean" }
  | { kind: "text" } // 文字列
  | { kind: "number"; integer: boolean; min?: number }
  // 文字列・数・真偽のどれでもよい (anyOf)。入力された文字から型を決める。options は候補 (enum/const)
  | { kind: "scalar"; types: string[]; options: Json[] }
  | { kind: "list"; items: Schema }
  | { kind: "object"; props: Record<string, Schema>; required: string[]; extra: Schema | null }
  | { kind: "unsupported" };

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

export const isMap = (v: unknown): v is { [k: string]: Json } => isObj(v);

function typesOf(s: Schema): string[] {
  const t = s.type;
  return (Array.isArray(t) ? t : t === undefined ? [] : [t]).filter((x): x is string => typeof x === "string" && x !== "null");
}

function variants(s: Schema): Schema[] {
  const list = (s.anyOf ?? s.oneOf) as unknown;
  if (!Array.isArray(list)) return [s];
  return list.filter(isObj).flatMap(variants);
}

function enumOf(s: Schema): Json[] | null {
  if (Array.isArray(s.enum)) return s.enum as Json[];
  if ("const" in s) return [s.const as Json];
  return null;
}

/** schema と今の値 (未設定は undefined) から、どの入力欄を出すか決める。 */
export function kindOf(schema: Schema | null | undefined, value?: Json): Kind {
  if (!schema) return { kind: "unsupported" };
  const vs = variants(schema);
  if (vs.length > 1) {
    const complex = vs.filter((v) => ["object", "array"].some((t) => typesOf(v).includes(t)));
    const scalars = vs.filter((v) => !complex.includes(v));
    const wantComplex = Array.isArray(value) ? "array" : isMap(value) ? "object" : null;
    if (wantComplex) {
      const hit = complex.find((v) => typesOf(v).includes(wantComplex));
      return hit ? kindOf(hit, value) : { kind: "unsupported" };
    }
    if (scalars.length === 0) return { kind: "unsupported" };
    const options = scalars.flatMap((v) => enumOf(v) ?? []);
    const types = [...new Set(scalars.flatMap(typesOf))];
    if (types.length === 0 && options.length > 0) return { kind: "enum", options };
    if (types.length === 0) return { kind: "unsupported" };
    return { kind: "scalar", types, options };
  }
  const options = enumOf(schema);
  if (options) return { kind: "enum", options };
  const types = typesOf(schema);
  const t = types[0];
  if (types.length !== 1) return { kind: "unsupported" };
  if (t === "boolean") return { kind: "boolean" };
  if (t === "string") return { kind: "text" };
  if (t === "integer" || t === "number") {
    return { kind: "number", integer: t === "integer", min: typeof schema.minimum === "number" ? schema.minimum : undefined };
  }
  if (t === "array") {
    const items = schema.items;
    return isObj(items) && kindOf(items).kind !== "unsupported" ? { kind: "list", items } : { kind: "unsupported" };
  }
  if (t === "object") {
    const props = isObj(schema.properties) ? (schema.properties as Record<string, Schema>) : {};
    const ap = schema.additionalProperties;
    const extra = isObj(ap) ? ap : null;
    // 項目も値の型も決まっていない map (settings など) はフォームにできない
    if (Object.keys(props).length === 0 && !extra) return { kind: "unsupported" };
    const required = Array.isArray(schema.required) ? (schema.required as string[]) : [];
    return { kind: "object", props, required, extra };
  }
  return { kind: "unsupported" };
}

/** 未設定の項目を「設定する」ときの初期値。 */
export function defaultFor(schema: Schema): Json {
  if (schema.default !== undefined && !Array.isArray(schema.default)) return schema.default as Json;
  const k = kindOf(schema);
  switch (k.kind) {
    case "enum":
      return k.options[0] ?? null;
    case "boolean":
      return false;
    case "number":
      return k.min ?? 0;
    case "list":
      return [];
    case "object": {
      const out: { [key: string]: Json } = {};
      for (const key of k.required) if (k.props[key]) out[key] = defaultFor(k.props[key]);
      return out;
    }
    case "scalar":
      return k.options[0] ?? "";
    default:
      return "";
  }
}

/** 入力された文字を、許される型の値にする (数に読めて数が許されるなら数、true/false なら真偽)。 */
export function parseScalar(text: string, types: string[]): Json {
  const t = text.trim();
  const num = /^-?\d+(\.\d+)?$/.test(t);
  if (num && types.includes("integer") && /^-?\d+$/.test(t)) return Number(t);
  if (num && types.includes("number")) return Number(t);
  if (types.includes("boolean") && (t === "true" || t === "false")) return t === "true";
  return text;
}

export type Op =
  | { op: "set_value"; path: (string | number)[]; value: Json }
  | { op: "add_key"; path: (string | number)[]; key: string; value: Json }
  | { op: "remove_key"; path: (string | number)[] }
  | { op: "insert_item"; path: (string | number)[]; value: Json }
  | { op: "remove_item"; path: (string | number)[] };

const same = (a: Json, b: Json) => JSON.stringify(a) === JSON.stringify(b);
const kindName = (v: Json) => (Array.isArray(v) ? "list" : isMap(v) ? "map" : "scalar");

/** old → next を yaml の部分置き換えの操作の列にする (変わった所だけ)。 */
export function diffOps(old: Json | undefined, next: Json | undefined, path: (string | number)[] = []): Op[] {
  if (same(old as Json, next as Json)) return [];
  const ops: Op[] = [];
  if (isMap(old) && isMap(next)) {
    for (const k of Object.keys(old)) if (!(k in next)) ops.push({ op: "remove_key", path: [...path, k] });
    for (const [k, v] of Object.entries(next)) {
      if (!(k in old)) {
        ops.push({ op: "add_key", path, key: k, value: v });
      } else if (kindName(old[k]) !== kindName(v)) {
        // scalar ⇔ map / list は set_value で変えられない。消して足す (並びは末尾になる)
        ops.push({ op: "remove_key", path: [...path, k] }, { op: "add_key", path, key: k, value: v });
      } else ops.push(...diffOps(old[k], v, [...path, k]));
    }
    return ops;
  }
  if (Array.isArray(old) && Array.isArray(next)) {
    const n = Math.min(old.length, next.length);
    for (let i = 0; i < n; i++) ops.push(...diffOps(old[i], next[i], [...path, i]));
    for (let i = old.length - 1; i >= n; i--) ops.push({ op: "remove_item", path: [...path, i] });
    for (let i = n; i < next.length; i++) ops.push({ op: "insert_item", path, value: next[i] });
    return ops;
  }
  return [{ op: "set_value", path, value: next as Json }];
}

/** 最上位の項目の draft (未設定は undefined) 同士の差。 */
export function diffFields(old: Record<string, Json | undefined>, next: Record<string, Json | undefined>): Op[] {
  const ops: Op[] = [];
  for (const key of Object.keys(old)) {
    const a = old[key], b = next[key];
    if (a !== undefined && b === undefined) ops.push({ op: "remove_key", path: [key] });
    else if (a === undefined && b !== undefined) ops.push({ op: "add_key", path: [], key, value: b });
    else if (a !== undefined && b !== undefined) {
      if (kindName(a) !== kindName(b)) ops.push({ op: "remove_key", path: [key] }, { op: "add_key", path: [], key, value: b });
      else ops.push(...diffOps(a, b, [key]));
    }
  }
  return ops;
}
