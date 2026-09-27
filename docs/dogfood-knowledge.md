# yamato を開発する艦の knowledge (叩き台)

<!-- dogfooding の艦 (workspace = yamato の repo) を作ったら、下の「---」から後ろを艦フォルダの knowledge.md に写す。
     templates/dev/knowledge.md は艦ごとに育てるものなので触らない。経緯は docs/handoff.md §9 -->

---

## この repo (yamato) の決まり
- 作業対象は yamato 自身 (Python 3.11+、pip install なし、PyYAML は `vendor/`)。入口は `./yamato`、本体は `src/yamato/`
- docs・コメント・board の本文・報告は日本語。repo は private (中身を外に出さない)
- 設計の正本は `docs/design.md` §0 (最新の決定)。P1 の詳細は `docs/design-p1.md`。食い違えば §0。全体の地図は `docs/handoff.md`
- 設計の根幹 (§0 の決定・コマンドの名前・記録の形式) を変える task は、実装の前に design の判断を開く

## mechanism-not-policy (owner の方針)
- コードが持つのは道具・記録の整合性・安全網だけ。誰が何をいつどう進めるかは team.yaml・ひな形・役割プロンプトに置く
- 判定の問い: 「別の PJ でこれが邪魔にならないか」。邪魔になりうるならコードで強制せず、ひな形の既定値か役割プロンプトに書く
- コードに残す強制の一覧は `docs/design.md` §2.1 (出典 `docs/_archive/policy-audit.md` §4)。増やすときは PR の本文に理由を書く

## テスト (速く保つ)
- `python3 -m unittest discover` が正 (全体で 10 秒程度)。`--durations 10` で自分の足したテストが上位に来ていないか見る
- 実時間で待たない: `headless.POLL`・`seat.WATCHDOG_POLL` などのモジュール定数を `mock.patch.object` で縮める
- 偽の claude は `tests/fake_claude.py` (`tests/helpers.py` の `patch_fast` で同じプロセス)。git のテストは `GitShipTestCase` の形 (一時 repo をコピー)
- 本物の claude を使う確認 (E2E) は艦の中で走らせない。要るなら owner の判断を開く

## Claude Code の事実 (2.1.283 の観測。出典は docs/handoff.md §5)
- Claude Code とのやり取りは `src/yamato/claude.py` 1 か所に閉じ込める
- resume は pid が消えてから、フルの sessionId で。短い id や stop 直後はフラグ抜きのコピーになる
- 生死は pid で見る。agent view の `state` は発言の意味づけで、完了の判定に使わない
- `claude --bg` は起動前に落ちても exit 0。起動のあと `claude agents --json` で確かめる
- SessionStart hook の注入は hook 1 本 10,000 文字まで (超えるとプレビューに化ける)
- bg の席は daemon の環境で動く。起動側の `env -u` は効かず、席の Bash に効くのは settings の `env`
- auto では Bash の allow で絞れない (外を読む役割は dontAsk)。`ask` ルールは auto でも止まる。Haiku は auto 不可
- idle の席は約 60 分で止まるが、attach 中と Remote Control の席は止まらない

## この艦で気をつけること
- fleet の worktree と同じ repo を使う。同じブランチ・同じファイルを fleet の task と同時に触らない
- `pr open` / `pr merge` は本物の GitHub に PR を作る。merge は owner の判断のあと
- 席が呼ぶ yamato は `~/dev/yamato` の checkout。main の変更は pull するまで効かない (pull は艦が止まっているときに)
