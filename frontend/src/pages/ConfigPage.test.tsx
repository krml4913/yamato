import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";

const field = (key: string, extra: object = {}) => ({
  key, set: true, required: false, value: "x", type: "string", description: `${key} の説明`, enum: null, ...extra,
});

function config(over: object = {}) {
  return {
    name: "t1", path: "/s/team.yaml", raw: "name: t1\n", files: [],
    pending: { changed: false, keys: [] },
    fields: [
      field("name", { required: true, value: "t1" }),
      field("roles", { type: "object", value: { impl: { model: "sonnet", count: 2 }, pm: { model: "opus" } } }),
      field("grace", { set: false, value: null, type: null }),
    ],
    ...over,
  };
}

function open(body: unknown, status = 200) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: status < 400, status, json: async () => body })));
  render(<MemoryRouter initialEntries={["/ship/t1/config"]}><App /></MemoryRouter>);
}

afterEach(() => vi.unstubAllGlobals());

describe("ConfigPage", () => {
  it("項目ごとに節を立て、schema の説明を出す。roles は表", async () => {
    open(config());
    expect(await screen.findByText("name の説明")).toBeInTheDocument();
    expect(screen.getByText("roles の説明")).toBeInTheDocument();
    expect(screen.getByText("未設定")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByText("impl")).toBeInTheDocument();
    expect(within(table).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["名前", "model", "count"]);
    expect(screen.getByText("生の yaml").closest("details")).not.toHaveAttribute("open");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("まとまりに分け、頭に目次を置く。入れ替わった並びでも schema に無い項目は『その他』", async () => {
    open(config({ fields: [field("grace", { value: "20m" }), field("name", { value: "t1" }), field("zzz", { value: "q" })] }));
    await screen.findByText("name の説明");
    const toc = screen.getByRole("navigation", { name: "目次" });
    expect(within(toc).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["艦の基本", "#group-basics"], ["時間と予算", "#group-time"], ["その他", "#group-other"],
    ]);
    const basics = document.getElementById("group-basics")!;
    expect(within(basics).getByText("name")).toBeInTheDocument();
    expect(within(document.getElementById("group-other")!).getByText("zzz")).toBeInTheDocument();
  });

  it("object の値は鍵: 値で出し、生の JSON を見せる。未設定は省略時の動作を添える", async () => {
    open(config({
      fields: [
        field("template", { type: "object", value: { name: "default", versions: ["001", "002"], opt: { a: 1 } } }),
        field("grace", { set: false, value: null, description: "猶予。省略時 20m。" }),
      ],
    }));
    const group = await screen.findByText("default");
    expect(group).toBeInTheDocument();
    expect(screen.getByText("versions")).toBeInTheDocument();
    expect(screen.getByText("002")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('{"');
    expect(screen.getByText("未設定")).toBeInTheDocument();
    expect(screen.getByText(/省略時 20m/, { selector: "span" })).toBeInTheDocument();
  });

  it("team.yaml が新しければ次の up で効く変更を出す", async () => {
    open(config({ pending: { changed: true, keys: ["hub"] } }));
    expect(await screen.findByRole("status")).toHaveTextContent("次の yamato up で効く変更がある (hub)");
  });

  it("読めない艦はエラーを出して落ちない", async () => {
    open({ error: { code: "unreadable", message: "team.yaml を読めません" } }, 500);
    expect(await screen.findByRole("alert")).toHaveTextContent("team.yaml を読めません");
    expect(screen.getByText("設定: t1")).toBeInTheDocument();
  });
});
