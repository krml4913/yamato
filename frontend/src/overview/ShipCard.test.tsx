import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ShipCard } from "./ShipCard";
import type { Ship } from "./types";

const base = {
  hub: "pm", hub_last: null, deadline: null, tokens: 0, owner_unread: 0, counts: {}, seats: [],
  board: [], prs: [], orphans: [], anomalies: [], memory: [], report: null, multi_repo: false,
};
const render1 = (over: object) => render(
  <MemoryRouter>
    <ShipCard ship={{ name: "dev", state: { ...base, ...over } } as unknown as Ship} now={1000} waits={[]} />
  </MemoryRouter>,
);

describe("艦の枠の段組", () => {
  it("board・孤児が空なら 1 行で出し、段組にしない", () => {
    const { container } = render1({});
    expect(screen.getByText("空")).toBeTruthy();
    expect(container.querySelector('[class*="cols"]')).toBeNull();
    expect(container.querySelector('[class*="cards"]')).toBeNull();
  });

  it("board と異常があれば 2 段組のまま", () => {
    const { container } = render1({
      counts: { open: 1 },
      board: [{ id: "T-1", state: "open", title: "x", assignee: null, blocked_on: [] }],
      anomalies: [{ kind: "force_stop", ts: 999, summary: "s" }],
    });
    expect(container.querySelector('[class*="cols"]')).not.toBeNull();
  });

  it("孤児があれば日報と並べる", () => {
    const { container } = render1({ orphans: [{ id: "T-2", title: "y", why: "w" }] });
    expect(container.querySelector('[class*="cards"]')).not.toBeNull();
  });
});
