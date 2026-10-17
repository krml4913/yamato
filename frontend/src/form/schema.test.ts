import { defaultFor, diffFields, diffOps, kindOf, parseScalar, type Json, type Schema } from "./schema";

describe("kindOf", () => {
  it("type・enum・anyOf から入力欄を決める", () => {
    expect(kindOf({ type: "string" })).toEqual({ kind: "text" });
    expect(kindOf({ type: "integer", minimum: 1 })).toEqual({ kind: "number", integer: true, min: 1 });
    expect(kindOf({ type: "boolean" })).toEqual({ kind: "boolean" });
    expect(kindOf({ enum: ["a", "b"] })).toEqual({ kind: "enum", options: ["a", "b"] });
    expect(kindOf({ type: "array", items: { type: "string" } }).kind).toBe("list");
    expect(kindOf({ anyOf: [{ type: "string" }, { type: "integer" }] })).toEqual({
      kind: "scalar", types: ["string", "integer"], options: [],
    });
    expect(kindOf({ anyOf: [{ const: "none" }, { enum: ["x"] }] })).toEqual({ kind: "enum", options: ["none", "x"] });
  });

  it("anyOf は今の値の型で枝を選ぶ。決まらない形は unsupported", () => {
    const s: Schema = {
      anyOf: [{ type: "object", properties: { a: { type: "string" } } }, { enum: ["off", false] }],
    };
    expect(kindOf(s, { a: "1" }).kind).toBe("object");
    expect(kindOf(s, "off")).toEqual({ kind: "enum", options: ["off", false] });
    expect(kindOf({ type: "object", additionalProperties: true }).kind).toBe("unsupported");
    expect(kindOf({ type: "array", items: { type: "object", additionalProperties: true } }).kind).toBe("unsupported");
    expect(kindOf(null).kind).toBe("unsupported");
  });
});

describe("defaultFor / parseScalar", () => {
  it("初期値は schema から", () => {
    expect(defaultFor({ enum: ["x", "y"] })).toBe("x");
    expect(defaultFor({ type: "integer", minimum: 1 })).toBe(1);
    expect(defaultFor({ type: "object", required: ["a"], properties: { a: { type: "boolean" } } })).toEqual({ a: false });
    expect(defaultFor({ type: "string", default: "d" })).toBe("d");
  });

  it("文字を許される型にする", () => {
    expect(parseScalar("20", ["string", "integer"])).toBe(20);
    expect(parseScalar("20", ["string"])).toBe("20");
    expect(parseScalar("20m", ["string", "integer"])).toBe("20m");
    expect(parseScalar("true", ["boolean", "string"])).toBe(true);
    expect(parseScalar("1.5", ["number"])).toBe(1.5);
  });
});

describe("diffOps", () => {
  it("変わった所だけ操作にする", () => {
    const old: Json = { a: 1, roles: { impl: { model: "sonnet" }, pm: { model: "opus" } }, tags: ["x", "y"] };
    const next: Json = { a: 1, roles: { impl: { model: "opus" } }, tags: ["x", "z", "w"], extra: { k: 1 } };
    expect(diffOps(old, next)).toEqual([
      { op: "remove_key", path: ["roles", "pm"] },
      { op: "set_value", path: ["roles", "impl", "model"], value: "opus" },
      { op: "set_value", path: ["tags", 1], value: "z" },
      { op: "insert_item", path: ["tags"], value: "w" },
      { op: "add_key", path: [], key: "extra", value: { k: 1 } },
    ]);
    expect(diffOps(old, old)).toEqual([]);
  });

  it("list を縮めるときは後ろから消す。scalar ⇔ map は消して足す", () => {
    expect(diffOps(["a", "b", "c"], ["a"])).toEqual([
      { op: "remove_item", path: [2] },
      { op: "remove_item", path: [1] },
    ]);
    expect(diffOps({ v: "off" }, { v: { a: "1" } })).toEqual([
      { op: "remove_key", path: ["v"] },
      { op: "add_key", path: [], key: "v", value: { a: "1" } },
    ]);
  });

  it("最上位の項目: 未設定→設定は add_key、設定→未設定は remove_key", () => {
    expect(diffFields({ a: "1", b: undefined, c: "x" }, { a: "2", b: "n", c: undefined })).toEqual([
      { op: "set_value", path: ["a"], value: "2" },
      { op: "add_key", path: [], key: "b", value: "n" },
      { op: "remove_key", path: ["c"] },
    ]);
  });
});
