import styles from "./saveBar.module.css";

/** unified diff を行ごとに色分けする。色だけに頼らず先頭の +/- はそのまま残す。 */
export function DiffView({ diff, label = "差分" }: { diff: string; label?: string }) {
  const lines = diff.replace(/\n$/, "").split("\n");
  return (
    <pre className={styles.diff} aria-label={label}>
      {lines.map((l, i) => {
        const header = l.startsWith("+++") || l.startsWith("---");
        const cls = header || l.startsWith("@@") ? styles.hunk
          : l.startsWith("+") ? styles.add
          : l.startsWith("-") ? styles.del
          : "";
        return <span key={i} className={`${styles.line} ${cls}`}>{l || " "}{"\n"}</span>;
      })}
    </pre>
  );
}
