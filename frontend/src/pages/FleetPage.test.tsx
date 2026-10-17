import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";

const ship = (name: string, extra: object = {}) => ({
  name, path: `/h/${name}`, registered: true, exists: true, team: name, error: null, ...extra,
});

function open(body: unknown, status = 200) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: status < 400, status, json: async () => body })));
  render(<MemoryRouter initialEntries={["/fleet"]}><App /></MemoryRouter>);
}

afterEach(() => vi.unstubAllGlobals());

describe("FleetPage", () => {
  it("艦を表にし、消えた艦と読めない艦を区別し、admiral は別枠に出す", async () => {
    open({
      home: "/h",
      ships: [
        ship("a"),
        ship("gone", { exists: false, team: null, error: "/h/gone/team.yaml がありません" }),
        ship("loose", { registered: false }),
      ],
      admiral: { path: "/h/_admiral", team: "yamato", roles: ["admiral"], error: null },
    });
    const table = await screen.findByRole("table");
    expect(within(table).getByRole("link", { name: "a" })).toHaveAttribute("href", "/ship/a/config");
    expect(within(table).queryByRole("link", { name: "gone" })).toBeNull();
    expect(within(table).getByText("フォルダが無い")).toBeInTheDocument();
    expect(within(table).getByText("/h/gone/team.yaml がありません")).toBeInTheDocument();
    expect(within(table).getByText("$YAMATO_HOME 直下")).toBeInTheDocument();
    const adm = screen.getByRole("region", { name: "admiral" });
    expect(within(adm).getByText("roles: admiral")).toBeInTheDocument();
    expect(within(table).queryByText("_admiral")).toBeNull();
  });

  it("書き込みの入口 (ボタン・フォーム) を出さない", async () => {
    open({ home: "/h", ships: [ship("a")], admiral: null });
    expect(await screen.findByText("まだ作られていない")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("API が落ちたらエラーを出す", async () => {
    open({ error: { code: "internal", message: "艦の一覧の読み取りに失敗した" } }, 500);
    expect(await screen.findByRole("alert")).toHaveTextContent("艦の一覧の読み取りに失敗した");
  });
});
