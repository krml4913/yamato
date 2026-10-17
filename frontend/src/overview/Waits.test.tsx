import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Waits } from "./Waits";
import type { Wait } from "./types";

function setToken(on: boolean) {
  document.head.querySelectorAll('meta[name="yamato-token"]').forEach((m) => m.remove());
  if (on) {
    const m = document.createElement("meta");
    m.name = "yamato-token";
    m.content = "tok";
    document.head.appendChild(m);
  }
}

const w = (ship: string, id: string, title: string): Wait =>
  ({ ship, id, title, urgent: false, age: "1h", recommendation: null });
const waits = [w("dev", "X-002", "<b>一番上</b>"), w("dev", "X-005", "二番目")];

describe("判断待ちを閉じる", () => {
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); setToken(false); });

  it("--edit でなければ入口を出さない", () => {
    setToken(false);
    render(<Waits waits={waits} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("待ちが無ければ入口を出さない", () => {
    setToken(true);
    render(<Waits waits={[]} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("一番上の判断だけを、決定と理由を添えて確認のあとに閉じる", async () => {
    setToken(true);
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      argv: ["yamato", "decide"], exit_code: 0, timed_out: false, truncated: false, output: "閉じた: X-002",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<Waits waits={waits} />);
    expect(screen.getAllByRole("button")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText(/決定/), { target: { value: "A" } });
    fireEvent.change(screen.getByLabelText(/理由/), { target: { value: "-x 安いから" } });
    fireEvent.click(screen.getByRole("button", { name: "判断を閉じる" }));
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/v1/ops/decide-close");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      ship: "dev", params: { id: "X-002", choice: "A", reason: "-x 安いから" }, confirmed: true });
    expect((await screen.findByLabelText("出力")).textContent).toBe("閉じた: X-002");
  });
});
