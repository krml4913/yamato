import status from "../status.module.css";

// 席の表の「要対応」列の凡例。ページに 1 回だけ出す。
export function Legend() {
  return (
    <p className={status.dim}>
      要対応の列: <span className={status.red}>赤</span> = すぐ手が要る (詰まり・API エラー) /{" "}
      <span className={status.warn}>黄</span> = 様子見 (動いていない・担当が無い)。内部の言葉は文に重ねたとき (hover) に出る
    </p>
  );
}
