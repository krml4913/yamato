import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";

const schema = {
  name: { type: "string", description: "艦の名前。" },
  grace: { description: "猶予", anyOf: [{ type: "string" }, { type: "integer", minimum: 1 }] },
  roles: {
    type: "object",
    additionalProperties: {
      type: "object",
      properties: {
        model: { type: "string", description: "モデル" },
        shift: { enum: ["per_task", "persistent", "headless"], description: "シフト" },
        count: { type: "integer", minimum: 1 },
      },
    },
  },
  settings: { type: "object", additionalProperties: true },
};
const field = (key: string, value: unknown, extra: object = {}) => ({
  key, set: value !== undefined, required: false, value: value ?? null, type: null, description: `${key} の説明`,
  enum: null, schema: (schema as Record<string, object>)[key], ...extra,
});
const config = () => ({
  name: "t1", path: "/s/team.yaml", raw: "name: t1\n", files: [], pending: { changed: false, keys: [] },
  fields: [
    field("name", "t1", { required: true }),
    field("grace", "20m"),
    field("roles", { impl: { model: "sonnet", shift: "per_task", count: 2 } }),
    field("settings", { env: { A: "1" } }),
  ],
});

type Call = { url: string; body?: { ops: unknown; base_hash?: string } };
let calls: Call[];

function open(replies: Record<string, { status: number; body: unknown }>) {
  calls = [];
  document.head.innerHTML = '<meta name="yamato-token" content="tok">';
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: { body?: string }) => {
    calls.push({ url, body: init?.body ? JSON.parse(init.body) : undefined });
    const r = replies[url] ?? { status: 404, body: { error: { code: "not_found", message: "no" } } };
    return { ok: r.status < 400, status: r.status, json: async () => r.body };
  }));
  render(<MemoryRouter initialEntries={["/ship/t1/config"]}><App /></MemoryRouter>);
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.head.innerHTML = "";
});

const cfg = { "/api/v1/ships/t1/config": { status: 200, body: config() } };
const previewOk = { valid: true, errors: [], changed: true, diff: "-grace: 20m\n+grace: 5m\n", base_hash: "h1" };

describe("ConfigForm", () => {
  it("schema の説明・enum・型から入力欄を出し、扱えない形は生 yaml へ案内する", async () => {
    open(cfg);
    expect(await screen.findByLabelText("roles.impl.model")).toHaveValue("sonnet");
    expect(screen.getByText("モデル")).toBeInTheDocument();
    const shift = screen.getByLabelText("roles.impl.shift") as HTMLSelectElement;
    expect([...shift.options].map((o) => o.text)).toEqual(["per_task", "persistent", "headless"]);
    expect(screen.getByLabelText("roles.impl.count")).toHaveValue(2);
    expect(screen.getAllByText(/生の yaml で直す/).length).toBeGreaterThan(0);
    // 変えるまで保存バーは出ない
    expect(screen.queryByRole("button", { name: "差分を確認" })).toBeNull();
    expect(screen.queryByRole("region", { name: "保存" })).toBeNull();
  });

  it("閲覧と同じまとまりと目次で並ぶ", async () => {
    open(cfg);
    await screen.findByLabelText("roles.impl.model");
    expect(screen.getByRole("navigation", { name: "目次" })).toBeInTheDocument();
    expect(document.getElementById("group-seats")!.querySelector("#field-roles")).not.toBeNull();
    expect(document.getElementById("group-basics")!.querySelector("#field-name")).not.toBeNull();
  });

  it("直す → 差分を確認 → 保存。保存後は『次の yamato up で効く』を出す", async () => {
    open({
      ...cfg,
      "/api/v1/edit/preview": { status: 200, body: previewOk },
      "/api/v1/edit/commit": { status: 200, body: { written: true, hash: "h2" } },
    });
    fireEvent.change(await screen.findByLabelText("grace"), { target: { value: "5m" } });
    expect(screen.getByRole("region", { name: "保存" })).toHaveTextContent("1 項目を変えた");
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "差分を確認" }));
    expect(await screen.findByText(/\+grace: 5m/)).toHaveClass(/add/);
    expect(screen.getByText(/-grace: 20m/)).toHaveClass(/del/);
    expect(calls.find((c) => c.url.endsWith("preview"))?.body).toMatchObject({
      ship: "t1", file: "team.yaml", ops: [{ op: "set_value", path: ["grace"], value: "5m" }],
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("status")).toHaveTextContent("次の yamato up で効く");
    expect(calls.find((c) => c.url.endsWith("commit"))?.body?.base_hash).toBe("h1");
  });

  it("変えた欄を元に戻すとバーが消える", async () => {
    open(cfg);
    fireEvent.change(await screen.findByLabelText("grace"), { target: { value: "5m" } });
    fireEvent.click(screen.getByRole("button", { name: "元に戻す" }));
    expect(screen.queryByRole("region", { name: "保存" })).toBeNull();
    expect(screen.getByLabelText("grace")).toHaveValue("20m");
  });

  it("保存に失敗したらエラーをバーの中に出し、保存したとは出さない", async () => {
    open({
      ...cfg,
      "/api/v1/edit/preview": { status: 200, body: previewOk },
      "/api/v1/edit/commit": { status: 500, body: { error: { code: "io", message: "書けなかった" } } },
    });
    fireEvent.change(await screen.findByLabelText("grace"), { target: { value: "5m" } });
    fireEvent.click(screen.getByRole("button", { name: "差分を確認" }));
    fireEvent.click(await screen.findByRole("button", { name: "保存" }).then(async (b) => { await waitFor(() => expect(b).toBeEnabled()); return b; }));
    expect(await screen.findByRole("alert")).toHaveTextContent("書けなかった");
    expect(screen.getByRole("region", { name: "保存" })).toContainElement(screen.getByRole("alert"));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("検証に通らなければ errors を出し、保存できない", async () => {
    open({
      ...cfg,
      "/api/v1/edit/preview": { status: 200, body: { ...previewOk, valid: false, errors: ["grace が不正"] } },
    });
    fireEvent.change(await screen.findByLabelText("roles.impl.count"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "差分を確認" }));
    expect(await screen.findByText("grace が不正")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });

  it("項目を消す・足す。部分置き換えで扱えない形はサーバーの案内を出す", async () => {
    open({
      ...cfg,
      "/api/v1/edit/preview": { status: 422, body: { error: { code: "unpatchable", message: "この形はフォームで直せない。生の yaml で直す", detail: "anchor" } } },
    });
    fireEvent.click(await screen.findByRole("button", { name: "grace を未設定に戻す" }));
    fireEvent.click(screen.getByRole("button", { name: "差分を確認" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("生の yaml で直す (anchor)"));
    expect(calls.find((c) => c.url.endsWith("preview"))?.body?.ops).toEqual([{ op: "remove_key", path: ["grace"] }]);
  });

  it("読むだけ (トークン無し) のときはフォームを出さない", async () => {
    open(cfg);
    document.head.innerHTML = "";
    // 再描画せず、新しく描画し直す
    document.body.innerHTML = "";
    render(<MemoryRouter initialEntries={["/ship/t1/config"]}><App /></MemoryRouter>);
    expect((await screen.findAllByText("grace の説明")).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("grace")).toBeNull();
  });
});
