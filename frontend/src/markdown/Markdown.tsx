import { useState, type ReactNode } from "react";
import { parseBlocks, safeHref, type Block, type Inline } from "./parse";
import styles from "./markdown.module.css";

// markdown を React の要素にして出す。本文は必ず text node になる (HTML を文字列のまま挿す API は使わない)。
function inline(nodes: Inline[]): ReactNode[] {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "text": return n.s;
      case "code": return <code key={i}>{n.s}</code>;
      case "strong": return <strong key={i}>{inline(n.c)}</strong>;
      case "em": return <em key={i}>{inline(n.c)}</em>;
      case "link": return <a key={i} href={safeHref(n.href) ?? undefined} target="_blank" rel="noopener noreferrer">{inline(n.c)}</a>;
    }
  });
}

// 目次に載せる見出し (h2)。目次を出すときだけ id を振る (1 画面に複数の md があっても id が重ならないように)
const tocId = (i: number) => `md-sec-${i}`;

function plain(nodes: Inline[]): string {
  return nodes.map((n) => (n.t === "text" || n.t === "code" ? n.s : plain(n.c))).join("");
}

function block(b: Block, key: number, withIds = false): ReactNode {
  switch (b.t) {
    case "heading": {
      const H = `h${Math.min(b.level, 6)}` as "h1";
      return <H key={key} id={withIds && b.level === 2 ? tocId(key) : undefined}>{inline(b.c)}</H>;
    }
    case "para": return <p key={key}>{inline(b.c)}</p>;
    case "code": return <pre key={key}><code>{b.s}</code></pre>;
    case "quote": return <blockquote key={key}>{b.c.map((c, i) => block(c, i))}</blockquote>;
    case "hr": return <hr key={key} />;
    case "list": {
      const L = b.ordered ? "ol" : "ul";
      return <L key={key}>{b.items.map((it, i) => <li key={i}>{it.map((c, j) => block(c, j))}</li>)}</L>;
    }
    case "table":
      return (
        <table key={key}>
          <thead><tr>{b.head.map((c, i) => <th key={i}>{inline(c)}</th>)}</tr></thead>
          <tbody>{b.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody>
        </table>
      );
  }
}

// 見出しが 3 つ以上あるときだけ目次を出す
const TOC_MIN = 3;

function Toc({ items }: { items: { id: string; label: string }[] }) {
  // 広い画面では横に開いたまま、狭い画面では畳んで先頭に置く
  const [open] = useState(() => typeof window.matchMedia === "function" && window.matchMedia("(min-width: 1100px)").matches);
  return (
    <details className={styles.toc} open={open}>
      <summary>目次</summary>
      <ol>{items.map((t) => <li key={t.id}><a href={`#${t.id}`} onClick={(e) => { e.preventDefault(); document.getElementById(t.id)?.scrollIntoView(); }}>{t.label}</a></li>)}</ol>
    </details>
  );
}

export function Markdown({ text, toc = false }: { text: string; toc?: boolean }) {
  const blocks = parseBlocks(text);
  const items = toc
    ? blocks.flatMap((b, i) => (b.t === "heading" && b.level === 2 ? [{ id: tocId(i), label: plain(b.c) }] : []))
    : [];
  const show = items.length >= TOC_MIN;
  const body = <div className={styles.md}>{blocks.map((b, i) => block(b, i, show))}</div>;
  return show ? <div className={styles.withToc}><Toc items={items} />{body}</div> : body;
}
