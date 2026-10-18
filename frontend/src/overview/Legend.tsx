import { useId, useState } from "react";
import status from "../status.module.css";
import styles from "./overview.module.css";

// 席の表の「要対応」見出しの横の「?」。押す・hover・キーボードの focus で凡例を出す (Esc で閉じる)。
export function Legend() {
  const [pinned, setPinned] = useState(false);
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const id = useId();
  const open = pinned || hover || focus;
  return (
    <span className={styles.legend}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      onKeyDown={(e) => { if (e.key === "Escape") { setPinned(false); setHover(false); } }}>
      <button type="button" className={styles.legendBtn} aria-label="要対応の見方"
        aria-expanded={open} aria-controls={id}
        onClick={() => setPinned(!pinned)} onFocus={() => setFocus(true)} onBlur={() => setFocus(false)}>?</button>
      {open && (
        <span id={id} role="note" className={styles.legendBody}>
          <span className={status.red}>赤</span> = すぐ手が要る (詰まり・API エラー)<br />
          <span className={status.warn}>黄</span> = 様子見 (動いていない・担当が無い)<br />
          内部の言葉は文に重ねたとき (hover) に出る
        </span>
      )}
    </span>
  );
}
