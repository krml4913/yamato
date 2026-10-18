import { useEffect } from "react";

// 操作が成功したあと、画面のデータをすぐ読み直してもらうための合図。
// 操作の確認画面は表の奥や入力の下など画面のあちこちに出るので、props で配らず window の event で伝える。
export const REFRESH_EVENT = "yamato:refresh";

export function requestRefresh() {
  window.dispatchEvent(new Event(REFRESH_EVENT));
}

// 合図が来たら handler を呼ぶ。handler は呼び出し側で安定させる (useCallback)。
export function useRefreshRequest(handler: () => void) {
  useEffect(() => {
    window.addEventListener(REFRESH_EVENT, handler);
    return () => window.removeEventListener(REFRESH_EVENT, handler);
  }, [handler]);
}
