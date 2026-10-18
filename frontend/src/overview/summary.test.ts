import { remaining } from "./format";
import { isRunning, stoppedSummary } from "./summary";
import type { Ship } from "./types";

const ship = (seats: Array<{ alive: boolean; last: number | null }>, counts = {}): Ship =>
  ({ name: "s", error: null, state: { seats, counts } } as unknown as Ship);

describe("summary", () => {
  it("生きている席があれば動いている。読めない艦も動いている側", () => {
    expect(isRunning(ship([{ alive: false, last: null }, { alive: true, last: null }]))).toBe(true);
    expect(isRunning(ship([{ alive: false, last: null }]))).toBe(false);
    expect(isRunning({ name: "x", error: "壊れた", state: null } as Ship)).toBe(true);
  });

  it("止まった艦の要約は最後の動きと board の件数", () => {
    const s = ship([{ alive: false, last: 1000 }, { alive: false, last: 400 }], { done: 4, active: 1 });
    expect(stoppedSummary(s, 1000 + 3600)).toBe("最後の動き 1h00m前 / active 1 / done 4");
    expect(stoppedSummary(ship([{ alive: false, last: null }]), 5)).toBe("動きの記録なし / board 空");
  });

  it("deadline の表示から残り時間だけ取る", () => {
    expect(remaining("deadline 10-17 17:23 (残り 1h54m)")).toBe("残り 1h54m");
    expect(remaining("deadline なし")).toBe("deadline なし");
  });
});
