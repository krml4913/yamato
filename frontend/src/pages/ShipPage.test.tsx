import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ShipPage, seatNames } from "./ShipPage";

function setToken(on: boolean) {
  document.head.querySelectorAll('meta[name="yamato-token"]').forEach((m) => m.remove());
  if (on) {
    const m = document.createElement("meta");
    m.name = "yamato-token";
    m.content = "tok";
    document.head.appendChild(m);
  }
}

const page = () => render(
  <MemoryRouter initialEntries={["/ship/dev"]}>
    <Routes><Route path="/ship/:name" element={<ShipPage />} /></Routes>
  </MemoryRouter>,
);

// 席の一覧を取る config の GET は数えず、ops の POST だけを見る
const ops = (m: ReturnType<typeof vi.fn>) => m.mock.calls.filter(([u]) => String(u).includes("/ops/"));

describe("艦ページの操作", () => {
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); setToken(false); });

  it("--edit でなければ操作の入口を出さない", () => {
    setToken(false);
    page();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("config の files に出る md へのリンクを並べる", async () => {
    setToken(false);
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      fields: [], raw: "", pending: { changed: false, keys: [] },
      files: [
        { file: "roles/pm.md", kind: "role", bytes: 1 },
        { file: "charter.md", kind: "charter", bytes: 1 },
        { file: "knowledge.md", kind: "knowledge", bytes: 1 },
      ],
    }), { status: 200 }))));
    page();
    const link = await screen.findByRole("link", { name: "roles/pm.md" });
    expect(link.getAttribute("href")).toBe("/ship/dev/files/roles%2Fpm.md");
    expect(screen.getByRole("link", { name: "charter.md" }).getAttribute("href")).toBe("/ship/dev/files/charter.md");
    expect(screen.getByRole("link", { name: "knowledge.md" }).getAttribute("href")).toBe("/ship/dev/files/knowledge.md");
  });

  it("files が取れなければ一覧を出さない", async () => {
    setToken(false);
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response("{}", { status: 500 }))));
    page();
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByLabelText("md の一覧")).toBeNull();
  });

  it("5 つの操作を出す", () => {
    setToken(true);
    page();
    for (const n of ["up", "down", "extend", "down --force", "halt"]) {
      expect(screen.getByRole("button", { name: n })).toBeTruthy();
    }
  });

  it.each([
    ["halt", "halt", {}, "緊急停止する"],
    ["down --force", "down-force", {}, "強制停止する"],
    ["down", "down", {}, "終業を指示する"],
  ])("%s は確認画面を経てから POST する (alert は使わない)", async (label, op, params, confirm) => {
    setToken(true);
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      argv: ["yamato", "x"], exit_code: 1, timed_out: false, truncated: false, output: "<b>断られた</b>",
    }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    page();
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(ops(fetchMock)).toHaveLength(0);
    const region = screen.getByRole("region");
    fireEvent.click(screen.getByRole("button", { name: confirm }));
    await waitFor(() => expect(ops(fetchMock)).toHaveLength(1));
    const [url, init] = ops(fetchMock)[0];
    expect(String(url)).toBe(`/api/v1/ops/${op}`);
    expect(JSON.parse(init.body)).toEqual({ ship: "dev", params, confirmed: true });
    expect(await screen.findByText("exit code 1")).toBeTruthy();
    expect(region.querySelector("pre")?.textContent).toBe("<b>断られた</b>");
    expect(alert).not.toHaveBeenCalled();
  });

  it("up は稼働時間と席を、extend は時間を params で送る", async () => {
    setToken(true);
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      argv: ["yamato"], exit_code: 0, timed_out: false, truncated: false, output: "ok",
    }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    page();
    fireEvent.change(screen.getByLabelText(/稼働時間/), { target: { value: "3h" } });
    fireEvent.change(screen.getByLabelText(/一緒に起こす席/), { target: { value: "impl,review" } });
    fireEvent.click(screen.getByRole("button", { name: "up" }));
    fireEvent.click(screen.getByRole("button", { name: "起動する" }));
    await waitFor(() => expect(ops(fetchMock)).toHaveLength(1));
    expect(JSON.parse(ops(fetchMock)[0][1].body).params).toEqual({ for: "3h", seats: "impl,review" });
    fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
    fireEvent.change(screen.getByLabelText(/延ばす時間/), { target: { value: "1h" } });
    fireEvent.click(screen.getByRole("button", { name: "extend" }));
    fireEvent.click(screen.getByRole("button", { name: "延ばす" }));
    await waitFor(() => expect(ops(fetchMock)).toHaveLength(2));
    expect(String(ops(fetchMock)[1][0])).toBe("/api/v1/ops/extend");
    expect(JSON.parse(ops(fetchMock)[1][1].body).params).toEqual({ span: "1h" });
  });

  it("send は席と本文をそのまま params で送り、出力は text で描く", async () => {
    setToken(true);
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      argv: ["yamato", "send"], exit_code: 0, timed_out: false, truncated: false, output: "<i>送った</i>",
    }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    page();
    fireEvent.change(screen.getByLabelText(/^席/), { target: { value: "pm" } });
    fireEvent.change(screen.getByLabelText(/本文/), { target: { value: "--from x\n二行目" } });
    fireEvent.click(screen.getByRole("button", { name: "send" }));
    expect(ops(fetchMock)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "送る" }));
    await waitFor(() => expect(ops(fetchMock)).toHaveLength(1));
    expect(String(ops(fetchMock)[0][0])).toBe("/api/v1/ops/send");
    expect(JSON.parse(ops(fetchMock)[0][1].body)).toEqual({
      ship: "dev", params: { seat: "pm", message: "--from x\n二行目" }, confirmed: true });
    expect((await screen.findByLabelText("出力")).textContent).toBe("<i>送った</i>");
  });

  it("席は艦の roles から select にする", async () => {
    setToken(true);
    const cfg = { fields: [{ key: "roles", set: true, value: { pm: {}, impl: { count: 2 } } }] };
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(cfg), { status: 200 }))));
    page();
    const select = await screen.findByRole("combobox", { name: /^席/ });
    expect([...select.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["(選ぶ)", "pm", "impl-1", "impl-2"]);
    fireEvent.change(select, { target: { value: "impl-2" } });
    expect((select as HTMLSelectElement).value).toBe("impl-2");
  });

  it("席が取れないときは自由入力に戻す", async () => {
    setToken(true);
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ error: { message: "壊れている" } }), { status: 500 })));
    vi.stubGlobal("fetch", fetchMock);
    page();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByRole("textbox", { name: /^席/ })).toBeTruthy();
  });

  it("seatNames は読めない roles で null を返す", () => {
    const mk = (value: unknown, set = true) => ({ fields: [{ key: "roles", set, value }] }) as never;
    expect(seatNames({ fields: [] } as never)).toBeNull();
    expect(seatNames(mk(undefined, false))).toBeNull();
    expect(seatNames(mk({ a: { count: 0 } }))).toBeNull();
    expect(seatNames(mk({ a: { count: "x" } }))).toBeNull();
    expect(seatNames(mk({}))).toBeNull();
  });

  it("やめるを押せば呼ばない", () => {
    setToken(true);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    page();
    fireEvent.click(screen.getByRole("button", { name: "halt" }));
    fireEvent.click(screen.getByRole("button", { name: "やめる" }));
    expect(ops(fetchMock)).toHaveLength(0);
    expect(screen.queryByRole("region")).toBeNull();
  });
});
