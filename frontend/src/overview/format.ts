// 一覧の表示用の整形 (Python 側の util.fmt_time / fmt_span / report._tokens と同じ見た目)。

const pad = (n: number) => String(n).padStart(2, "0");

export function fmtTime(epoch: number | null): string {
  if (!epoch) return "-";
  const d = new Date(epoch * 1000);
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function fmtSpan(seconds: number): string {
  const s0 = Math.trunc(seconds);
  const sign = s0 < 0 ? "-" : "";
  const s = Math.abs(s0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${sign}${h}h${pad(m)}m`;
  if (m) return `${sign}${m}m${pad(s % 60)}s`;
  return `${sign}${s % 60}s`;
}

// 「3 分前」のような相対時刻。表で 1 行に収めるため、いちばん大きい単位だけ出す。
export function fmtAgo(seconds: number): string {
  const s = Math.trunc(seconds);
  if (s < 10) return "たった今";
  if (s < 60) return `${s} 秒前`;
  if (s < 3600) return `${Math.floor(s / 60)} 分前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 時間前`;
  return `${Math.floor(s / 86400)} 日前`;
}

// 作業ログの 1 行に混じる長い id (uuid 形) を先頭 8 文字と … に縮める。全文は title で読める。
export function shortenIds(text: string): string {
  return text.replace(/\b([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/g, "$1…");
}

export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

// rotate の閾値に対して、この割合を超えたら「近い」と出す。
export const CONTEXT_NEAR = 0.8;

export function fmtDateTime(epoch: number): string {
  const d = new Date(epoch * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

// 稼働時間の表示 (例: "deadline 10-17 17:23 (残り 1h54m)") から「残り …」だけ取り出す。無ければ元の文字列。
export function remaining(deadline: string): string {
  const m = /\((残り [^)]+)\)/.exec(deadline);
  return m ? m[1] : deadline;
}

// 稼働時間の表示から「(残り …)」を除いたもの。残り時間はバッジが言うので、地の文では繰り返さない。
export function deadlineAt(deadline: string): string {
  return deadline.replace(/\s*\(残り [^)]+\)/, "");
}
