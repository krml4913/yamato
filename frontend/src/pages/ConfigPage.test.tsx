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
