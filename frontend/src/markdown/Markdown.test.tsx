import { render, screen } from "@testing-library/react";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("見出し・箇条書き・コード・強調を出す", () => {
    const { container } = render(
      <Markdown text={"# 題\n\n- 一つ目 **太字** と `code`\n- 二つ目\n  - 入れ子\n\n```\nx < y\n```\n"} />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "題" })).toBeInTheDocument();
    expect(container.querySelectorAll("li")).toHaveLength(3);
    expect(container.querySelector("strong")?.textContent).toBe("太字");
    expect(container.querySelector("pre code")?.textContent).toBe("x < y");
  });

  it("生の HTML は要素にならず文字として出る", () => {
    const { container } = render(<Markdown text={'<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">'} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<script>alert(1)</script>");
  });

  it("リンクは http(s) と mailto だけ。javascript: は文字のまま", () => {
    const { container } = render(<Markdown text={"[ok](https://example.com) [bad](javascript:alert(1))"} />);
    const links = container.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("https://example.com");
    expect(links[0].getAttribute("rel")).toContain("noopener");
    expect(container.textContent).toContain("[bad](javascript:alert(1))");
  });

  it("表を出す", () => {
    const { container } = render(<Markdown text={"| a | b |\n|---|---|\n| 1 | 2 |\n"} />);
    expect(container.querySelectorAll("th")).toHaveLength(2);
    expect(container.querySelectorAll("td")).toHaveLength(2);
  });
});

describe("Markdown の目次", () => {
  const doc = "# 題\n\n## 一\n\nあ\n\n## 二\n\nい\n\n## 三\n\nう\n";
  it("toc 指定で h2 が 3 つ以上あれば目次を出し、h2 に id を振る", () => {
    render(<Markdown text={doc} toc />);
    expect(screen.getByText("目次")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "二" })).toHaveAttribute("href", expect.stringMatching(/^#md-sec-/));
    expect(screen.getByRole("heading", { level: 2, name: "二" })).toHaveAttribute("id");
  });
  it("toc 指定が無いか h2 が少なければ出さない", () => {
    const { unmount } = render(<Markdown text={doc} />);
    expect(screen.queryByText("目次")).toBeNull();
    unmount();
    render(<Markdown text={"## 一\n\n## 二\n"} toc />);
    expect(screen.queryByText("目次")).toBeNull();
  });
});
