import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ShipPage } from "./ShipPage";

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

describe("艦ページの操作", () => {
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); setToken(false); });

  it("--edit でなければ操作の入口を出さない", () => {
    setToken(false);
    page();
    expect(screen.queryByRole("button")).toBeNull();
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
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      argv: ["yamato", "x"], exit_code: 1, timed_out: false, truncated: false, output: "<b>断られた</b>",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    page();
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(fetchMock).not.toHaveBeenCalled();
    const region = screen.getByRole("region");
    fireEvent.click(screen.getByRole("button", { name: confirm }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
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
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).params).toEqual({ for: "3h", seats: "impl,review" });
    fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
    fireEvent.change(screen.getByLabelText(/延ばす時間/), { target: { value: "1h" } });
    fireEvent.click(screen.getByRole("button", { name: "extend" }));
    fireEvent.click(screen.getByRole("button", { name: "延ばす" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(String(fetchMock.mock.calls[1][0])).toBe("/api/v1/ops/extend");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).params).toEqual({ span: "1h" });
  });

  it("send は席と本文をそのまま params で送り、出力は text で描く", async () => {
    setToken(true);
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      argv: ["yamato", "send"], exit_code: 0, timed_out: false, truncated: false, output: "<i>送った</i>",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    page();
    fireEvent.change(screen.getByLabelText(/^席/), { target: { value: "pm" } });
    fireEvent.change(screen.getByLabelText(/本文/), { target: { value: "--from x\n二行目" } });
    fireEvent.click(screen.getByRole("button", { name: "send" }));
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "送る" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/v1/ops/send");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      ship: "dev", params: { seat: "pm", message: "--from x\n二行目" }, confirmed: true });
    expect((await screen.findByLabelText("出力")).textContent).toBe("<i>送った</i>");
  });

  it("やめるを押せば呼ばない", () => {
    setToken(true);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    page();
    fireEvent.click(screen.getByRole("button", { name: "halt" }));
    fireEvent.click(screen.getByRole("button", { name: "やめる" }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("region")).toBeNull();
  });
});
