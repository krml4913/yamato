// 小さな markdown のパーサ。出力は木 (データ) だけで、HTML 文字列は作らない。
// 描画側 (Markdown.tsx) が React の要素にするので、本文の `<script>` などは文字として出る。

export type Inline =
  | { t: "text"; s: string }
  | { t: "code"; s: string }
  | { t: "strong"; c: Inline[] }
  | { t: "em"; c: Inline[] }
  | { t: "link"; href: string; c: Inline[] };

export type Block =
  | { t: "heading"; level: number; c: Inline[] }
  | { t: "para"; c: Inline[] }
  | { t: "code"; lang: string; s: string }
  | { t: "quote"; c: Block[] }
  | { t: "list"; ordered: boolean; items: Block[][] }
  | { t: "table"; head: Inline[][]; rows: Inline[][][] }
  | { t: "hr" };

const INLINE = /(`+)([\s\S]+?)\1|\*\*([\s\S]+?)\*\*|(?<![\w*])\*([^*\s][^*]*?)\*(?!\w)|\[([^\]]+)\]\(([^)\s]+)\)/;

// リンク先で使ってよいのは http(s) と mailto だけ (javascript: などは文字のまま出す)。
export function safeHref(href: string): string | null {
  return /^(https?:\/\/|mailto:)/i.test(href) ? href : null;
}

export function parseInline(src: string): Inline[] {
  const out: Inline[] = [];
  let rest = src;
  while (rest) {
    const m = INLINE.exec(rest);
    if (!m) {
      out.push({ t: "text", s: rest });
      break;
    }
    if (m.index > 0) out.push({ t: "text", s: rest.slice(0, m.index) });
    if (m[2] !== undefined) out.push({ t: "code", s: m[2].trim() });
    else if (m[3] !== undefined) out.push({ t: "strong", c: parseInline(m[3]) });
    else if (m[4] !== undefined) out.push({ t: "em", c: parseInline(m[4]) });
    else if (safeHref(m[6]) !== null) out.push({ t: "link", href: m[6], c: parseInline(m[5]) });
    else out.push({ t: "text", s: m[0] });
    rest = rest.slice(m.index + m[0].length);
  }
  return out;
}

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR = /^ {0,3}([-*_])( ?\1){2,}\s*$/;
const ITEM = /^( *)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function cells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

export function parseBlocks(src: string): Block[] {
  return blocks(src.replace(/\r\n?/g, "\n").split("\n"));
}

function blocks(lines: string[]): Block[] {
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    const fence = FENCE.exec(line);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
      i++;
      out.push({ t: "code", lang: fence[2], s: body.join("\n") });
      continue;
    }
    const h = HEADING.exec(line);
    if (h) { out.push({ t: "heading", level: h[1].length, c: parseInline(h[2]) }); i++; continue; }
    if (HR.test(line)) { out.push({ t: "hr" }); i++; continue; }

    if (/^ {0,3}>/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^ {0,3}>/.test(lines[i])) body.push(lines[i++].replace(/^ {0,3}> ?/, ""));
      out.push({ t: "quote", c: blocks(body) });
      continue;
    }

    const item = ITEM.exec(line);
    if (item) {
      const base = item[1].length;
      const ordered = /\d/.test(item[2]);
      const items: Block[][] = [];
      while (i < lines.length) {
        const m = ITEM.exec(lines[i]);
        if (!m || m[1].length !== base || /\d/.test(m[2]) !== ordered) break;
        const body = [m[3]];
        i++;
        // 次の項目までの、より深く字下げした行・続きの行は項目の中身
        while (i < lines.length) {
          const next = lines[i];
          if (!next.trim()) {
            if (i + 1 < lines.length && /^\s+\S/.test(lines[i + 1]) && !ITEM.test(lines[i + 1].replace(/^ {0,3}/, "")) ) { body.push(""); i++; continue; }
            break;
          }
          const nm = ITEM.exec(next);
          if (nm && nm[1].length <= base) break;
          if (!nm && !/^\s/.test(next)) break;
          body.push(next.replace(new RegExp(`^ {0,${base + 2}}`), ""));
          i++;
        }
        items.push(blocks(body));
      }
      out.push({ t: "list", ordered, items });
      continue;
    }

    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const head = cells(line).map(parseInline);
      i += 2;
      const rows: Inline[][][] = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) rows.push(cells(lines[i++]).map(parseInline));
      out.push({ t: "table", head, rows });
      continue;
    }

    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !FENCE.test(lines[i]) && !HEADING.test(lines[i])
      && !HR.test(lines[i]) && !/^ {0,3}>/.test(lines[i]) && !(para.length && ITEM.test(lines[i]))) {
      para.push(lines[i++].trim());
    }
    if (!para.length) { para.push(lines[i++].trim()); }
    out.push({ t: "para", c: parseInline(para.join(" ")) });
  }
  return out;
}
