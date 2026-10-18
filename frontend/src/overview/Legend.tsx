import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import status from "../status.module.css";
import styles from "./overview.module.css";

// 席の表の「要対応」見出しの横の「?」。押す (クリック・Enter・Space) で開閉し、Esc でも閉じる。
// hover や focus では開かない (開いたまま閉じられなくなるため)。
export function Legend() {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<CSSProperties>({});
  const btn = useRef<HTMLButtonElement>(null);
  const id = useId();
  // 席の表は横に動かす入れ物 (overflow) の中にあり、absolute の本文は切れる。fixed で画面に対して置く。
  // 位置は開いたときの値なので、動いたら (スクロール・リサイズ) 閉じる
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => { window.removeEventListener("scroll", close, true); window.removeEventListener("resize", close); };
  }, [open]);
  const toggle = () => {
    if (!open && btn.current) {
      const r = btn.current.getBoundingClientRect();
      const width = Math.min(18 * 16, window.innerWidth * 0.8);
      setPos({ top: r.bottom, left: Math.max(8, Math.min(r.left, window.innerWidth - width - 8)) });
    }
    setOpen(!open);
  };
  return (
    <span className={styles.legend}
      onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}>
      <button type="button" className={styles.legendBtn} aria-label="要対応の見方"
        aria-expanded={open} aria-controls={id} ref={btn}
        onClick={toggle}>?</button>
      {open && (
        <span id={id} role="note" className={styles.legendBody} style={pos}>
          <span className={status.red}>赤</span> = すぐ手が要る (詰まり・API エラー)<br />
          <span className={status.warn}>黄</span> = 様子見 (動いていない・担当が無い)<br />
          内部の言葉は文に重ねたとき (hover) に出る
        </span>
      )}
    </span>
  );
}
