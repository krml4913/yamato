import type { ReactNode } from "react";
import styles from "./saveBar.module.css";

/** 画面の下に固定する保存バー。count は変えた項目の数の表示、children はボタン、panel は差分・エラー。 */
export function SaveBar({ count, children, panel, error, busy }: {
  count: string;
  busy?: boolean;
  children: ReactNode;
  panel?: ReactNode;
  error?: string | null;
}) {
  return (
    <div className={styles.bar} role="region" aria-label="保存">
      {(error || panel) && (
        <div className={styles.panel}>
          {error && <p role="alert" className={styles.error}>{error}</p>}
          {panel}
        </div>
      )}
      <div className={styles.row}>
        <span className={styles.count} data-busy={busy ? "true" : "false"} aria-live="polite">{count}</span>
        {children}
      </div>
    </div>
  );
}

/** 保存が通ったことを、いま見ている場所に出す。 */
export function SavedNotice() {
  return (
    <p role="status" className={styles.saved}>
      保存した。次の yamato up で効く (自動では up しない)。
    </p>
  );
}

export { styles as saveBarStyles };
