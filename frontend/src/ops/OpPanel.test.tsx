import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { REFRESH_EVENT } from "../api/refresh";
import { OpPanel, type OpSpec } from "./OpPanel";

const spec: OpSpec = { op: "extend", ship: "dev", params: { span: "1h" }, title: "延ばす", confirmLabel: "延ばす", detail: "d" };

function Host() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>開く</button>
      {open && <OpPanel spec={spec} onClose={() => setOpen(false)} />}
    </>
  );
}

const reply = (exit_code: number) => vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
  argv: ["yamato", "extend"], exit_code, timed_out: false, truncated: false, output: "out",
}), { status: 200 })));

describe("OpPanel の出方と後始末", () => {
  beforeEach(() => {
    const m = document.createElement("meta");
    m.name = "yamato-token";
    m.content = "tok";
    document.head.appendChild(m);
  });
  afterEach(() => {
    vi.restoreAllMocks(); vi.unstubAllGlobals();
    document.head.querySelectorAll('meta[name="yamato-token"]').forEach((m) => m.remove());
  });

  it("出たらフォーカスが確認画面に移り、閉じたら押したボタンに戻る", () => {
    render(<Host />);
    const opener = screen.getByRole("button", { name: "開く" });
    opener.focus();
    fireEvent.click(opener);
    const panel = screen.getByRole("region", { name: "延ばす" });
    expect(document.activeElement).toBe(panel);
    fireEvent.click(screen.getByRole("button", { name: "やめる" }));
    expect(screen.queryByRole("region")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("exit 0 のときだけ画面の読み直しを頼む", async () => {
    const seen = vi.fn();
    window.addEventListener(REFRESH_EVENT, seen);
    try {
      vi.stubGlobal("fetch", reply(1));
      const { unmount } = render(<OpPanel spec={spec} onClose={() => {}} />);
      fireEvent.click(screen.getByRole("button", { name: "延ばす" }));
      await screen.findByText("exit code 1");
      expect(seen).not.toHaveBeenCalled();
      unmount();

      vi.stubGlobal("fetch", reply(0));
      render(<OpPanel spec={spec} onClose={() => {}} />);
      fireEvent.click(screen.getByRole("button", { name: "延ばす" }));
      await waitFor(() => expect(seen).toHaveBeenCalledTimes(1));
    } finally {
      window.removeEventListener(REFRESH_EVENT, seen);
    }
  });
});
