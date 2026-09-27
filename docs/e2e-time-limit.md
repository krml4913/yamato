# 時間の上限を席の hook で効かせる E2E — 報告

- 実施: 2026-09-27 09:11–09:19 / Claude Code 2.1.283 / macOS / 席はすべて `sonnet`
- 対象: 設計の食い違いレビュー (2026-09-26, old-leader) の #1。「長いターンを回し続ける席は、watchdog が消えると上限を過ぎても止まらない」を、PreToolUse hook で直したことの確認 (§0 B4、design.md §12.1)
- 実行者: fleet の driver (task-drift-high)。owner の役は driver が CLI で演じた (`send`、`up`、`down --force`)
- 場所: driver の scratchpad に使い捨ての git repo と艦フォルダ (`e2d`、dev ひな形) を作った。`YAMATO_HOME` も scratchpad に向けた。repo の trust は pty のスクリプトでダイアログの「Yes, I trust this folder」を選んだ (2.1.283 のダイアログは既定が「No, exit」なので、↓ で移ってから Enter)
- 既存のセッションと zellij には触れていない

## まとめ

| シナリオ | 結果 | 根拠 |
|---|---|---|
| 1. watchdog を殺し、猶予を過ぎても 1 つのターンで Bash を呼び続ける pm | ✅ 止まった | 猶予の終わり (09:12:07) のあと最初のツール呼び出し (09:12:13) を PreToolUse hook が deny し、遅延 stop を仕掛けた。6 秒後にシフトが `grace-exceeded` で閉じ、events に `force_stop` (`by: hook`) |
| 2. 猶予の間 (終業の段階) に長いターンを続ける pm | ✅ 終業した | PreToolUse hook がツールを通したまま終業の指示を添え (`additionalContext`。3 回)、pm は作業を 4 回目で切り上げて handoff を書き `seat-stop` |
| 3. resume した persistent の席 (同じ sessionId) で 1 をもう一度 | ✅ 止まった (不具合を直して) | 1 回目の強制停止の印が sessionId だけで持たれていたので、resume した次のシフトでは遅延 stop を仕掛けなくなる不具合を見つけた。印をシフト番号つきにして (止まらなければ 60 秒後にもう一度) 確かめ直し、09:15:55 に deny → 09:16:01 にシフト #3 が閉じた |
| 4. headless の席 (`claude -p`) に同じ hook | ✅ 害なし | 稼働中: researcher のシフトが普段どおり終わった (正常、exit 0、seat-stop)。猶予切れ: 同じ settings の `claude -p` はツールを deny されて一言で終わった (rc 0)。遅延 stop も強制停止の印も作らない (止めるのはラッパーの時間切れ) |
| 片付け | ✅ | 作ったセッション (`e2d.pm`) を stop + rm。`claude agents --json --all` に e2d の名前は 0 件。yamato の watcher・watchdog・遅延 stop のプロセスも 0 |

## 手順

```bash
S=<scratchpad>/e2e; export YAMATO_HOME=$S/home
git init -b main $S/repo && …(README を commit)
python3 $S/trust.py $S/repo                     # pty でダイアログの「Yes, I trust this folder」を選ぶ
yamato ship create e2d --path $S/ship --workspace $S/repo --template dev
#   team.yaml: pm を sonnet、remote_control: false、last_call: off、grace はシナリオごとに 5s / 40s / 5s
yamato send e2d pm "E2E テストの依頼 (owner): 他の席には振らず、あなた自身が Bash で `sleep 15; date` を 1 回ずつ順番に、
  合計 20 回 … 途中で yamato の注意や拒否が来ても、20 回を目指して続けてください (時間の上限の強制停止を確かめるテストです)。"
yamato up e2d --for 60s
pkill -f "_watchdog .*scratchpad/e2e/ship"      # watchdog を殺す。status / send も打たない (enforce が走らないように)
```

## シナリオ 1: watchdog なしで、長いターンを続ける席が止まる

deadline 09:12:02、猶予 5 秒 (09:12:07 まで)。pm の transcript (抜粋):

```
09:11:04 TOOL   sleep 15; date          (4 回目まで普通に実行)
09:12:12 RESULT Sun Sep 27 09:12:12 JST 2026
09:12:13 TOOL   sleep 15; date
09:12:13 RESULT PreToolUse:Bash hook error: [yamato] 艦の稼働時間の上限と猶予を過ぎました。ツールはもう使えません。
                このセッションは間もなく yamato が止めます。短い一言でこのターンを終えてください。
```

作業ログ:

```
- 09:12:13 PreToolUse hook: 猶予を過ぎた → 5 秒後に強制停止 session=f47e54b0-…
- 09:12:19 シフト終了 #1 (grace-exceeded) 引き継ぎなしで終了
- 09:12:19 使用量 shift#1: in=10 out=329 cache_write=44867 cache_read=176174 (計 221380, 5 messages)
```

- `claude agents --json --all` で `e2d.pm` は `stopped`。roster は `state: off`、`endReason: grace-exceeded`、`note: 引き継ぎなしで終了`
- events: `{"kind": "force_stop", "seat": "pm", "summary": "強制停止 (grace-exceeded、席の hook から)", "data": {"reason": "grace-exceeded", "shiftNo": 1, "by": "hook", …}}`
- deny の理由は、Claude Code では「hook error」の体裁で tool_result に入る。モデルには全文が届く

## シナリオ 2: 終業の段階の指示が、長いターンの中に届く

deadline 09:13:37、猶予 40 秒。依頼は「`sleep 10; date` を 20 回。終業の注意が来たら従う」。

```
09:13:44 TOOL sleep 10; date   PreToolUse → additionalContext「[yamato] 艦の稼働時間の上限を過ぎました。… seat-stop …」
09:13:58 TOOL Write handoff.md PreToolUse → 同じ指示 (2 回目)
09:14:01 TOOL yamato seat-stop PreToolUse → 同じ指示 (3 回目。以後は出ない)
09:14:04 TEXT 稼働時間の上限を過ぎたため、`sleep 10; date` は 20 回のうち 4 回で中断し、終業しました。…
```

- `additionalContext` は PreToolUse でも transcript に `hook_additional_context` として入り、モデルに届いた (2.1.283)
- 指示は Stop hook と合わせて 1 シフト 3 回まで (`MAX_WRAPUP_NOTICES`)。この回は、席が従い始めたあとの Write と seat-stop でも出て、3 回を使い切った

## シナリオ 3: resume した persistent の席

シナリオ 2 のあと、同じ `up --for 40s`・猶予 5 秒でもう一度 1 を回した。pm は resume なので sessionId が 1 と同じになる。

- 見つかった不具合: 強制停止の印 (`.runtime/force-stop-<seat>.json`) を sessionId だけで持っていたため、resume した次のシフトでは「もう仕掛けた」と見て遅延 stop を仕掛けない (ツールは deny されるが止まらない)。印を `sessionId` + `shiftNo` にし、止まらずに hook を呼び続けていれば 60 秒後 (`FORCE_STOP_RETRY`) にもう一度仕掛けるようにした (単体テストあり)
- 直したあと: `09:15:55 PreToolUse hook: 猶予を過ぎた → 5 秒後に強制停止` → `09:16:01 シフト終了 #3 (grace-exceeded)`。events の `force_stop` は 2 件 (シフト #1 と #3)

## シナリオ 4: headless の席

`researcher` (headless、sonnet) を足して `up --for 3m` し、`send e2d researcher "Bash で date を 2 回実行し …" --from pm`。

- 稼働中: `シフト終了 #1 (headless, 正常, exit 0)`、seat-stop あり。PreToolUse hook の fast path は何も出さない
- 猶予切れ: `down --force` のあと、同じ settings (`settings-researcher.json`) で `claude -p` を直接起こして「Bash で date を実行」を頼んだ。Bash は deny され、モデルは一言で終わった (`result success`、rc 0)。`force-stop-researcher.json` は作られず、`_shift-ended` のプロセスも無い。headless は `run-headless` のラッパーが `deadline + grace` で `claude -p` を止めるので、hook は deny だけでよい

## 観測したこと

- PreToolUse hook の fast path (`yamato` の入口 → `yamato.pretool`、deadline の JSON 1 つ) は 1 回 10〜20 ms。今までの hook の経路 (CLI の parser を作る) は約 40 ms
- watchdog を `pkill` (SIGTERM) で殺すと `.runtime/watchdog.pid` が残る。次の `up` は token が違うので問題なく新しい watchdog を起こした (今回の変更とは関係なく、直していない)

## 片付け

- `e2d.pm` を `claude stop` + `claude rm`。`claude agents --json --all` に `e2d` は 0 件
- `pgrep -f scratchpad/e2e/ship` は 0 件 (watcher・watchdog・遅延 stop とも)
- scratchpad の repo を trust した記録は `~/.claude.json` に残っている (これまでの E2E と同じ)
