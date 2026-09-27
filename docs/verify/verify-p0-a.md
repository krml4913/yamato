# yamato P0 検証 A: メッセージ配送と席のライフサイクル — 報告

- 実施: 2026-09-25 12:39–12:56 / Claude Code 2.1.282 / macOS / 全席 `--model haiku`
- cwd: scratchpad 配下の使い捨てディレクトリ `cwd/`(git なし)。pty スクリプトで trust プロンプトを承認してから使った(`claude --bg` は未 trust だと `Workspace not trusted` で exit 1)
- 席の名前は全部 `spike-a.*`。transcript の確認は `~/.claude/projects/<cwd>/<sessionId>.jsonl` を読む小さなスクリプト(tx.py)で行い、`claude logs` と `claude agents --json --all` を併用した
- 前提: ユーザー設定の `defaultMode` は `dontAsk`。`remoteControlAtStartup: true` のため、全席が Remote Control にもつながる

## まとめ

| # | 問い | 判定 |
|---|---|---|
| 1 | SendMessage で idle の席を起こせるか | ✅ 動く。ただし権限モードのクラスが違うと hold される。無人チームでは `crossSessionInbound: "accept"` が必須 |
| 2 | 停止中の席を起こす CLI | ✅ `claude --resume <フル sessionId> --bg "<msg>"`。同じ id で再開し、起動時のオプションも復元される。⚠ 短い id を渡すと停止中でもコピーになる |
| 3 | 席が自分を停止できるか | 🟡 部分的に動く。直接 `claude stop` を打つと最後のターンが transcript に残らない。**遅延 stop なら問題なく止まる** |
| 4 | inbox ファイルへの追記で自分を起こせるか | ✅ asyncRewake の Stop hook でも Monitor でも、1〜2 秒で起きた |

## Q1. エージェント間の配送 (B1) — ✅ 動く(権限モードに条件あり)

**手順**: `spike-a.impl`(「メッセージが来たら `ACK: <本文>` と返せ」)を起動し、idle になるまで待つ。次に `spike-a.cap` を起動し、「ListAgents で spike-a.impl を探して、SendMessage で `PING-1 from cap` を送れ」と指示した。
- cap の transcript: `SendMessage {"to":"spike-a.impl","message":"PING-1 from cap"}` → `{"success":true, ... queued there ...}`
- impl の transcript(送信直後の 03:41:20): 受信は `<cross-session-message from="uds:/tmp/cc-socks/2260.sock" from-name="spike-a.cap" from-mode="prompting">PING-1 from cap</cross-session-message>` という isMeta の user ターンとして入り、続いて `assistant: ACK: PING-1 from cap`。**idle の席で新しいターンが始まった**(送信から約 1 秒)
- `claude logs` にも受信した `PING-2 from cap2` と ACK が出ていた

**crossSessionInbound の有無**: 送り手 `spike-a.cap2` は dontAsk。受け手を 3 つ用意して、同じ `PING-2 from cap2` を送らせた。
| 受け手 | 設定 | 結果 |
|---|---|---|
| impl | dontAsk(設定なし) | ✅ 届いた(上の PING-1) |
| `spike-a.auto` | `--permission-mode auto`(設定なし) | ✅ 届いた。`ACK: PING-2 from cap2`(auto と dontAsk は同じクラス) |
| `spike-a.byp` | `--permission-mode bypassPermissions`(設定なし) | ❌ **hold された**。受け手の transcript: `Held peer message — from uds:/tmp/cc-socks/6157.sock [verified pid 6157] (peer claims name: spike-a.cap2) ... not delivered to Claude (1 held)`。送り手にも `Cross-session message held for approval (recipient: ...)` という informational が返った。`claude agents --json` では **`status:"waiting", waitingFor:"permission prompt"`** になり、約 10 分後の片付けまでその状態のままだった(無人だと滞留し続ける) |
| `spike-a.bypacc` | bypass + `--settings '{"crossSessionInbound":"accept"}'` | ✅ 届いた。`ACK: PING-2 from cap2` |

- 副産物: bypass の自分(driver)から dontAsk の cap に送ったメッセージも hold され、cap が `waitingFor:"permission prompt"` で止まった。**方向は関係なく、クラスが違えば hold される**
- 停止中の席に SendMessage した場合は `{"success":false,"message":"Failed to send to spike-a.impl: HTTP 409 — that Remote Control session may have ended or disconnected..."}` となり、**送り手はその場で失敗を知る**(停止中の席には SendMessage で届かない)
- 注意: ListAgents の宛先は名前。`--name` は一意ではないので(spike-zellij-attach Q5)、同じ名前の席が 2 つ生きていると宛先があいまいになる。SendMessage は `name [ref]` の形で特定もできる

## Q2. 停止中の席を起こす — ✅ `--resume <フル sessionId> --bg`

- `claude stop db87798d` → JSON は `state:"stopped", pid:null`
- `claude --resume db87798d-3833-4f61-93a8-aa8055ac9bbe --bg "PING-3 via resume"` →
  `note: woke session db87798d with its saved options (--name, --model).` / `backgrounded · db87798d · spike-a.impl`。**同じ id と同じ transcript** で再開し、新しい pid で `PONG-3` と返した(startedAt は更新される)
- **起動フラグは復元される**: bypacc(bypass + `--settings`)を stop してフル id で resume すると、`woke session 9c2ae108 with its saved options (--name, --permission-mode, --settings, --model).` と出た。resume 後に cap2 から再送すると即座に `ACK: PING-7 from cap2` が返ったので、**`--settings` の中身(accept)も resume 後に効いている**。これで design §4.1 の【要検証 P0】の半分に答えが出た(`--agents` は未確認)。`--settings` をファイルパスで渡した場合は、resume 時にそのファイルを読み直す(中身を書き換えてから resume すると、新しい allow ルールが効いた。Q3 で確認)
- **生きている席に resume した場合**: `note: session 9c2ae108 is already running in the background, so this started a copy as 7d17b83c. \`claude attach 9c2ae108\` opens the original.` と出て、同じ名前 `spike-a.bypacc` の別セッションが並んで生きている状態になった。コピーは会話履歴を引き継いで `ACK: PING-4 ...` と返した。**research の記述どおり**
- ⚠ **短い id(`claude --resume 9c2ae108 --bg`)を渡すと、停止中でもコピーになる**: `note: started a copy of that conversation as 65f83a9c. To continue a session under its own id, pass its full session id (lowercase, as \`claude agents --json\` prints it) to --resume.` しかもコピーの `name` は**プロンプトの文字列**になり、席名が失われた

## Q3. 席が自分を停止する (B3) — 🟡 部分的に動く(遅延 stop が正解)

**自分の id の取り方**: 席の Bash の環境に **`CLAUDE_CODE_SESSION_ID`(フルの sessionId)** がある。`printenv CLAUDE_CODE_SESSION_ID` → `63899403-1618-4e64-959e-4787777088a2`(JSON の sessionId と一致)。`claude agents --json` を読む必要はない。`claude stop` には先頭 8 文字を渡す。

**権限**: `--permission-mode auto` でも、`claude stop 7b6047b9` の実行前に `This command requires approval`(`waitingFor:"permission prompt"`)で止まった。`env` の全出力も要承認になった。→ `--settings` の allow に `Bash(claude stop:*)` が必要。追加したら通った。

**(a) 直接 stop**(`claude stop <自分>` を Bash で実行): 止まる(pid:null)。handoff ファイルも書けていた。transcript の jsonl も壊れていない(全行 parse OK)。ただし:
- **最後の assistant ターン(`claude stop` の tool_use)が transcript に保存されない**。Write の結果が最後の記録になる
- そのターンの **Stop hook は走らない**(`stop_hook_summary` が無い)
- フル id で resume すると、合成された `No response requested.` の後に新しいプロンプトが続く。席は「ステップ 3 の `claude stop` を実行していません」と**誤認した**。永続席なら、起きた直後にもう一度自分を止めようとする恐れがある

**(b) 遅延 stop**(推奨): 席が `nohup sh -c 'sleep 10; claude stop ${CLAUDE_CODE_SESSION_ID:0:8}' >/dev/null 2>&1 &` を実行し、`SHIFT-END` と返してターンを終える。
- transcript には tool_use → `SHIFT-END` → `stop_hook_summary` まで**全部残った**。約 10 秒後に pid:null になり、プロセスも消えた
- この compound コマンドを auto モードで通すには、allow を広めにする必要があった(テストでは `Bash` を丸ごと allow にした)。実運用では `yamato seat-stop --after 10` のような専用コマンドにして、`Bash(yamato seat-stop:*)` だけを allow にするのがよい

**`state` の注意**: 停止後の `state` は `stopped` になるとは限らない。外から stop した場合も自分で stop した場合も、最終的には `state:"done", status:null, pid:null` になった(stop 直後に一度 `stopped` と出たことはある)。**生存の判定は `pid != null` で行うこと**。`state` は使えない。

## Q4. inbox への追記で自分を起こす — ✅ どちらの方式も動く

- **asyncRewake の Stop hook**: `--settings` に `{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"watch-inbox.sh <inbox>","async":true,"asyncRewake":true}]}]}}` を入れた。watch-inbox.sh は、inbox の行数が増えるまで 1 秒ごとに確認し、増えたら新しい行を stderr に出して exit 2 する。
  - 追記(12:54:42)→ hook のログは `fired n=1`。席には `Stop hook blocking error from command "Stop": New inbox lines:\nMSG-RW-1 from admiral` が user ターンとして入り、`INBOX: MSG-RW-1 ...` と返した。ターンが終わると Stop が走るので、12:54:45 に `armed n0=1` で**自動的に再アーム**された
  - JSON の status は `idle` のまま。supervisor の約 1h 停止ルールからは守られない
  - 注意: SendMessage などで別の理由でターンが走ると、そのたびに Stop で watcher が増える。本番の script は、pidfile などで watcher を 1 本に保つ必要がある
- **Monitor ツール**: 席自身に `Monitor(command: "tail -n 0 -F <inbox>", timeout_ms: 1800000)` を起動させた。追記すると `<task-notification>… Monitor event: "inbox" <event>MSG-MON-1 from admiral</event>` で起き、`INBOX: MSG-MON-1 ...` と返した
  - status は `busy` になる(working 扱い)。常駐を兼ねるが、**30 分で期限切れ**になり、再アームが要る(期限切れの通知で席が起きるので、プロンプトで「期限が来たら再アームせよ」と書いておけば続けられる)
- どちらも、席を stop すると watcher / tail のプロセスは消えた(リークなし)。**プロセスが生きている席でしか使えない**のは同じ

## yamato の `send` とシフト終了の設計への示唆

1. **`crossSessionInbound: "accept"` はチームの settings に必ず入れる**(§4.1 の案のとおり)。auto と dontAsk は同じクラスだが、誰か 1 人でも bypass になると黙って滞留する。admiral / captain が bypass で動く可能性も考えると、accept は必須。`team status` では `waitingFor == "permission prompt"` を「詰まり」として赤く出すとよい(hold はここで見える)
2. **`send` の分岐**: 記録(inbox)に追記したあと、次のように分ける
   - 宛先が生きている(`pid != null`)場合 → 送り手のエージェントが SendMessage する。失敗は `success:false` で返る(停止中は 409)ので、送り手のプロンプトに「失敗したら `yamato send` を使え」と書けば足りる
   - 宛先が停止している `persistent` の席 → `claude --resume <roster のフル sessionId> --bg "<inbox を読め>"`。**必ずフル sessionId を使う**(短い id だとコピーができ、席名も失われる)。roster にはフル id を保存する
   - 宛先が生きているのに resume するのは NG(コピーができる)。事前に pid を確認すること
   - `per_task` → 常に新しいシフト(B3 の決定どおり)
3. **起動フラグ**: `--name/--model/--permission-mode/--settings` は resume 後も復元される。`--settings` はパスで渡せば、resume のときに最新の中身を読む(チームの設定を更新すると、次のシフトから効く)。`--agents` の復元は未確認(P0 の検証 3 に残す)
4. **シフトの終わり (B3)**: 「終業処理 → 遅延 stop → ターンを終える」の順にする。直接 stop だと最後のターンと Stop hook が消え、resume した席が自分を止め直そうとする。専用の `yamato seat-stop`(内部で nohup + sleep + `claude stop ${CLAUDE_CODE_SESSION_ID:0:8}`)を用意し、allow はそれだけにする。自分の id は `CLAUDE_CODE_SESSION_ID` から取る(env の焼き付き問題 #315 とは違い、これは席ごとに正しい値だった)。ただし、終業時の記録を Stop hook に頼る設計は避ける(直接 stop だと走らない)
5. **受信箱の監視(B1 の追加案)**: 検証の結果、どちらの方式も使える。常駐させない席には asyncRewake の Stop hook(idle のままで安い)、常駐させたい席には Monitor(busy で 1h 停止を防ぐが、30 分ごとに再アームが要る)。ただし、どちらも止まった席は起こせないので、`send` の resume 経路は必要なまま
6. **生存の判定は `pid != null`** で行う。`state` は停止後も `done` のことがあり、判定に使えない

## 片付け
- 作成した全セッション(probe, impl, cap, cap2, byp, bypacc, auto, self×3 世代, rw, mon と、コピーの 7d17b83c・65f83a9c)を全部 stop + rm した。`claude agents --json --all` で `spike-a` で始まるものは **0 件**。watcher / tail のプロセスも残っていない
- 既存のセッション(fleet-leader, main-leader, fleet-p0-verify-b-driver, spike-b.* など)には触っていない。zellij にも触っていない
- リポジトリの変更と PR は無し。副作用: scratchpad の `cwd` に Claude の workspace trust を 1 件追加した(~/.claude.json)
