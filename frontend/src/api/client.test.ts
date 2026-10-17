import { afterEach, describe, expect, it, vi } from "vitest";
import { commitEdit, editToken, EditApiError, previewEdit } from "./client";

afterEach(() => {
  document.head.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("edit client", () => {
  it("meta が無ければ読むだけ: トークン無し、POST しない", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect(editToken()).toBeNull();
    await expect(previewEdit({ ship: "s", file: "f", content: "c" })).rejects.toBeInstanceOf(EditApiError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("meta のトークンをヘッダで返す", async () => {
    document.head.innerHTML = '<meta name="yamato-token" content="tok">';
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ written: true, hash: "h" })));
    vi.stubGlobal("fetch", fetchMock);
    await commitEdit({ ship: "s", file: "f", content: "c", base_hash: "b" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/edit/commit");
    expect(init.headers["X-Yamato-Token"]).toBe("tok");
    expect(init.method).toBe("POST");
  });

  it("エラー応答は code を持つ例外にする", async () => {
    document.head.innerHTML = '<meta name="yamato-token" content="tok">';
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { code: "conflict", message: "変わった" } }), { status: 409 }),
      ),
    );
    await expect(commitEdit({ ship: "s", file: "f", content: "c", base_hash: "b" })).rejects.toMatchObject({
      status: 409,
      code: "conflict",
    });
  });
});
