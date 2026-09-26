# yamato P1 検証 D: `design-p1.md` §11 の要検証項目 (V1〜V7、V9〜V11) — 報告

- 実施: 2026-09-26 10:53–11:30 (JST) / Claude Code 2.1.283 (検証 A・B は 2.1.282) / macOS / モデルは haiku と sonnet (auto が要る場面だけ sonnet)
- 対象: `docs/design-p1.md` §11 の **V1〜V7、V9〜V11**。V8 (SessionStart の注入の上限) は検証 C の担当なのでやっていない。着手時は design-p1 v0 だったが、作業中に main が v1 (`2505340`) に進んだので、**v1 の §11 に合わせて追従した** (V6 が (a)(b)(c) に拡張、V11 の NG の書き方が変更)。節番号は v1 のもの
- 表記: 本文の `§N` と `design §N` は `design-p1.md` の節。基本設計は `design.md §N` と書く
- 環境: scratchpad 配下の使い捨て dir `$D` (下の記号)。`$D/ship` (git なしの艦フォルダ)、`$D/cwd` (git なしの workspace)、`$D/repo` (git repo + ローカル bare remote `$D/remote.git`)、`$D/wt-out` (repo の外の worktree 置き場)。セッションはすべて `spike-d.*`。`zellij` の `fleet-main`、他の driver のセッション、`~/.claude/settings.json` には触れていない
- 観測の仕掛け: `--settings` に SessionStart / UserPromptSubmit / PreToolUse / Stop / SessionEnd / PermissionRequest / PermissionDenied の hook を入れ、入力 JSON と環境 (`GH_TOKEN` の有無) をログに追記させた (`hook.py`。何も出力せず挙動に介入しない)。あわせて transcript の jsonl、`claude agents --json`、`~/.claude/sessions/<pid>.json`、`-p --output-format stream-json --verbose` の出力を読んだ
- 前提: ユーザー設定は `defaultMode: dontAsk`、`remoteControlAtStartup: true`、`language: Japanese`、環境に `GH_TOKEN` がある。`-p` は trust ダイアログを出さないが、bg の席は trust が要るので、`$D` を pty で trust した

## まとめ

| # | 問い | 判定 |
|---|---|---|
| V1 | `-p` でも `--agent` + `--agents`、`--setting-sources project,local`、`--add-dir ... --` が効き、SessionStart hook が走るか | ✅ 動く。`--bare` は付けてはいけない (サブスクだと `Not logged in`) |
| V2 | `-p` で `--permission-prompts none` + auto + deny が一緒に効くか | ✅ 動く (ask 由来のダイアログは即 deny、席は先へ進む)。ただし auto の classifier は揺れる |
| V3 | 実行中の `-p` に SendMessage が届くか | ✅ 動く (tool の境界で届く。最終の `result` に本文が載る) |
| V4 | `-p` に SIGTERM で exit 143 + SessionEnd hook が走るか | ✅ 動く。ただし SessionEnd hook の既定の待ちは 1.5 秒 |
| V5 | サブスクの枠に当たったときの bg と `-p` | ❓ **実際の枠切れは未確認** (当てていない)。形の手がかりは取れた |
| V6 | (a) repo で起きた席が add-dir 側の worktree に `cd` して auto で git 操作と編集ができるか (b) worktree を cwd にした bg の席が trust なしで起動するか (c) `bgIsolation: none` の席が頼まれずに commit・push するか | ✅ (a)(b)(c) とも動く。(b) は main repo が trust 済みなら。(c) は commit・push しなかった (2/2)。§8.2 の 2 つのやり方のどちらも使える |
| V7 | `tools` の制限と `Bash(...)` に絞った allow が bg と `-p` で効くか | 🟡 部分的。`tools` は効く。**auto では Bash を allow で絞っても他の Bash が止まらない** (`curl` の外部通信も通った)。dontAsk なら止まる |
| V9 | Stop hook の `transcript_path` から文脈量を読めるか | 🟡 部分的。読めるが最大 1 API 呼び出し分遅れる |
| V10 | 席ごとに Remote Control を制御できるか | ✅ 動く (`--settings` の `remoteControlAtStartup: false` で外し、`--remote-control` で足す) |
| V11 | `PushNotification` を席の外から出せるか | ❌ `-p` からは送られない (`Not sent`)。bg の席 + Remote Control の経路は未確認 |

以降の記号: `$D` = scratchpad 配下の `d/`。`agents.json` は `spikedrole` (役割プロンプトは「返答を `ROLE-SPIKED` で始めよ」)、`spikedext` (`tools: [Read, Write, Bash]`)、`spikedro` を持つ。設定ファイルは `mk.py` で `$D/ship/.runtime/settings.<名前>.json` に作った。

## V1. `-p` の起動レシピ — ✅ 動く

実行したコマンド (design §4.2 の -p レシピ。検証 B の bg レシピの置き換え):
```bash
cd $D/cwd
env -u GH_TOKEN claude -p --session-id <uuid> --output-format json \
  --agent spikedrole --agents "$(cat $D/ship/.runtime/agents.json)" \
  --model haiku --setting-sources project,local \
  --settings $D/ship/.runtime/settings.v1.json --permission-prompts none \
  --add-dir $D/ship -- "<プロンプト>" > out.json
```
- 終了コード 0、17 秒。結果 JSON の `session_id` は渡した `--session-id` と一致し、hook の環境の `CLAUDE_CODE_SESSION_ID` も同じだった
- **SessionStart hook が走った**: `source:"startup"`、`agent_type:"spikedrole"` が入力に入る。UserPromptSubmit / PreToolUse / Stop / SessionEnd も走った。transcript に `{"type":"agent-setting","agentSetting":"spikedrole"}` が残る
- **`--agent` + `--agents` の役割が効く**: 最初の応答は `ROLE-SPIKED` で始まり、「system prompt の役割を名乗る文を引用せよ」に `You are the spike-d test role SPIKED.` と返した。最終応答でトークンを落としたのは haiku の取りこぼしで、仕組みの失敗ではない
- **`--setting-sources project,local` が効く**: 自己申告で `LANG=none RUFLO=no` (user の `language: Japanese` と `~/.claude/CLAUDE.md` の Ruflo 節が入っていない)。検証 B の bg と同じ結果
- **`--add-dir ... --`**: `acceptEdits` で `$D/ship` (add-dir) への Write は通り、`$D/outside` (どの dir にも入らない) は denied。denied のあとも席は止まらず先へ進んだ。`--` のあとの文字列はプロンプトとして渡った
- **`env -u GH_TOKEN` は `-p` では効く**: hook から見た環境は、`env -u` 付きの run が `GH_TOKEN` 未設定、付けない run が設定ありだった。`-p` は起動元の環境をそのまま使うため。(bg は daemon から起動されるので効くかは別問題で、今回も未検証)
- **SessionStart hook の実行は stream-json で確認できる**: `--output-format stream-json --verbose` にすると `system/hook_started [SessionStart]` と `system/hook_response [SessionStart]` が `system/init` の前に流れる。`--include-hook-events` を足すと UserPromptSubmit / Stop も流れる。marker ファイルなしで「hook が走ったか」を判定できる

注意点:
- **`-p --bare` はサブスクでは動かない**: 同じレシピに `--bare` を付けると `is_error: true`、`result: "Not logged in · Please run /login"`、終了コード 1 になった (bare は OAuth と keychain を読まず、`ANTHROPIC_API_KEY` か `apiKeyHelper` を要求する)。docs (headless) には「`--bare` は将来 `-p` の既定になる」とある。`claude --help` に `--bare` を打ち消すフラグは無い。design §4.2 の「変わったら打ち消すフラグが要る」は、今は手段が無い。§4.2 の 4 番「hook が走った印が無ければ失敗」の確認は、この事態を拾える
- **stdin を閉じないと 3 秒待つ**: `Warning: no stdin data received in 3s, proceeding without it.` が出て起動が 3 秒遅れる。ラッパーは `< /dev/null` を付ける
- 権限ルールの書き方 (私のミス。最初の Write が拒否された原因): 絶対パスの allow / deny は `//` 始まり (`Write(//private/tmp/...)`)。`/` 始まりは project root 相対。`$VAR` の展開を含む Bash (`test -n "$GH_TOKEN" && ...`) は `Bash(test *)` と `Bash(echo *)` を allow していても dontAsk で拒否された。allow に書くコマンドは変数展開を避ける
- `-p` の結果 JSON の `usage` は**全 API 呼び出しの累計**で、現在の文脈量ではない。最後の呼び出しの値は `usage.iterations[-1]` (V9)
- 結果 JSON には `total_cost_usd`、`modelUsage` (`contextWindow` を含む)、`permission_denials`、`num_turns`、`terminal_reason` がある。使用量の記録 (§4.2 の 4) の材料は揃う

yamato への示唆: design §4.2 のレシピはそのまま使える。足すのは `< /dev/null` と、必要なら `--output-format stream-json --verbose` (hook の実行、`rate_limit_event`、V5 参照)。

## V2. `-p` で `--permission-prompts none` + auto + deny — ✅ 動く

settings (`v2`): `defaultMode: auto`、`deny: ["Bash(echo DENYME*)"]`、`ask: ["Bash(echo ASKME*)"]`、`allow: ["Write(//private/tmp/…/ship/work/**)"]` (`$D` の絶対パスの先頭に `/` をもう 1 つ足した形)。sonnet、`-p --permission-prompts none`。5 ステップを頼んだ:

| # | 操作 | 結果 |
|---|---|---|
| 1 | `echo ASKME-1` (ask ルール = 本来はダイアログ) | **DENIED**。即座に拒否。PermissionRequest hook は発火した |
| 2 | `echo DENYME-1` (deny ルール) | DENIED (hook は発火しない。検証 B と同じ) |
| 3 | `rm -rf $D/victim` | **実行された** (victim が消えた)。検証 B ではこの種の操作を classifier が `[Irreversible Local Destruction]` で拒否していた |
| 4 | allow 済みの Write | OK |
| 5 | どこにも許可のない場所への Write | OK (auto の classifier が通した) |

- 席は止まらず 10.8 秒で完走した。`permission_denials` に 1 と 2 が載った。終了コード 0
- **対照** (同じ settings で `--permission-prompts none` なし、2 ステップ): ASKME は同じく即 DENIED、6.1 秒で完走。docs (headless の「Turn off permission prompts」) にも「host の無い `-p` ではどちらでも deny。フラグは、host がある場合に待たせないことと、Claude に再試行させないことに効く」とある。yamato の `-p` は host が無いので、**このフラグは無くても挙動は同じ**で、付けると「頼んでも無駄」と Claude に伝わる
- PermissionDenied hook は今回の拒否 (ask 由来と deny ルール由来) では発火しなかった。classifier が拒否したときだけ発火する (検証 B)。今回は classifier が何も拒否しなかったので、発火は 0 件

注意点:
- **auto の classifier は揺れる**: 検証 B で拒否された `rm -rf <victim>` が、今回は通った。違い: 場所 (scratchpad 配下の別 dir)、依頼の出し方 (task.md ではなく直接のプロンプト)。原因は調べていない。「deny リストが本命」という検証 B の結論を強める
- `ask` を席の settings に入れない (検証 B のとおり bg では止まる)。`-p` では止まらず即 deny になる

yamato への示唆: design §4.1 の「`-p` でも `--permission-prompts none` が正式に効く」は成立。ただし bg との差は「効く」ことより「host が無ければ元々 deny」なので、無人の権限の安全は deny リストと dontAsk (V7) で持つ。NG のときの代替 (PermissionRequest hook で全 deny) は、`-p` でも PermissionRequest hook が発火することは確認した。hook が deny を返す動作そのものは、検証 B の bg で確認済みで、`-p` では実施していない。

## V3. 実行中の `-p` に SendMessage が届くか — ✅ 動く

- `-p --name spike-d.v3p` は `claude agents --json` に `kind:"interactive"`、`status:"busy"` で載る。名前で宛先にできる。`crossSessionInbound: "accept"` を settings に入れて実施した (送り手は bypass、受け手は dontAsk)
- **1 回目 (haiku)**: `sleep 40` を頼んだが、Claude Code が foreground の単独 `sleep` を `Blocked: standalone sleep 40` で拒否し、haiku は background 実行に変えて最初のターンを end_turn で終えた (02:03:04 UTC)。送信した SendMessage はその 5 秒後 (02:03:09) に届き、**新しいターンが始まって応答した**。`-p` が生きていれば idle でも起こせる。`-p` は 28 秒で終了し、background の sleep は打ち切られた (docs: 最終結果から約 5 秒で background の shell を終了する)
- **2 回目 (foreground の python を使い、tool 実行中に送信)**: `python3 -c "import time; time.sleep(40)"` の実行中 (02:04:03 UTC に enqueue) に送った。メッセージは tool の完了 (02:04:37) の直後に取り込まれ (`queue-operation remove`)、**同じターンの中で**受信された。最終の `result` に本文がそのまま載った (`PING-D3B mid-tool send from ...`)。58 秒、終了コード 0
- 受信は `<cross-session-message from="uds:/tmp/cc-socks/<pid>.sock" from-name="..." from-mode="bypass">` という isMeta の user ターン (検証 A と同じ形式)

注意点:
- メッセージは **tool の境界か idle のときにしか取り込まれない**。長い tool を実行中は待たされる
- `-p` が終わったあとは送れない (宛先が一覧から消える)。終了の直前に届くと取りこぼす可能性はある (最後の end_turn と終了の間に窓がある)。§4.3 の「ラッパーは終わる前に inbox の未読を確認する」は残す
- `accept` を外した場合の `-p` は試していない (bg では検証 A で hold を確認済み。`-p` は承認する人がいないので保留のまま残るはずだが、未確認)

yamato への示唆: `run-headless` は `--name <ship>.<seat>` を付けて起動すれば、実行中の headless の席に send を SendMessage で届けられる。ただし inbox への追記は正本として残す (取りこぼしの保険)。

## V4. `-p` に SIGTERM — ✅ 動く (SessionEnd hook の待ち時間に注意)

foreground の `python3 -c "time.sleep(90)"` を実行中の `-p` に、25 秒後に SIGTERM を送った (`env` を介さず `claude` を直接起動。`run.py` が `Popen.terminate()`):
- **終了コード 143** (Python の `Popen.wait()` が `143` を返した = `exit(143)`。シグナル死なら `-15`)
- **SessionEnd hook が走った** (`reason: "other"`)。Stop hook は走らない (ターンが終わっていないため)
- stdout は **0 バイト**。結果 JSON は出ない。実行中だった python の子プロセスは残らなかった。session は `claude agents` の一覧から消えた。SIGTERM から終了までは約 0.4 秒
- docs (headless の「Stop a run with SIGTERM」) と一致: 終了コード 143、実行中のターンは未完了のまま結果を記録しない、SessionEnd 以外の hook は走らない、Bash の子プロセスの木を止める

**SessionEnd hook の待ち時間**:
| 条件 | 4 秒かかる SessionEnd hook |
|---|---|
| 既定 | 打ち切られる (`SessionEnd hook [...] failed: Hook cancelled`)。終了は SIGTERM の約 1.9 秒後。hook の書き込みは完了しない |
| hook に `"timeout": 10` を付ける | 完走 (4 秒後に書き込み)。終了は SIGTERM の約 4.5 秒後 |
| 環境変数 `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS=8000` | 完走 |
- バイナリの文字列から、既定は 1500ms で、環境変数か hook の `timeout` (秒。最大 60 秒) で延びる

注意点:
- **時間切れで止めると結果 JSON が出ない**: 使用量 (`usage.jsonl`) は結果 JSON からは取れない。ラッパーが transcript から数えるか、`stream-json` の出力を保存しておく (ただし最後の `result` 行は出ない)
- `design.md` §8.3 の「SessionEnd hook で transcript を艦フォルダに保存」は、1.5 秒以内に終わるか、hook に `timeout` を付ける
- 中断したターンは、resume してもそのまま (次のプロンプトが会話を進める)。続けたいときは `CLAUDE_CODE_RESUME_INTERRUPTED_TURN=1` (docs)

yamato への示唆: design §4.2 の 3 (時間切れ) は成立。NG のときの「ラッパーが代わりに記録を書く」は、使用量の記録については必要 (結果 JSON が出ないため)。

## V5. サブスクの枠に当たったとき — ❓ 未確認 (枠には当てていない)

指示どおり実際に枠へは当てていない。分かったことと、代用で取った観察と、未確認のことを分けて書く。

**分かったこと (実測)**: `-p --output-format stream-json --verbose` の出力には、通常のときにも `rate_limit_event` が 1 件流れる:
```json
{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1790403600,"rateLimitType":"five_hour",
 "overageStatus":"rejected","overageDisabledReason":"org_level_disabled","isUsingOverage":false,
 "unifiedWindows":{"five_hour":{"utilization":0.27,"resetsAt":1790403600},"seven_day":{"utilization":0.06,"resetsAt":1790787600}}}}
```
- バイナリの文字列に `status` の値として `allowed_warning` と `rejected` が見える。観測したのは `allowed` だけ。実行の前後に取ると、使用率 (`utilization`) と回復時刻 (`resetsAt`) が分かる (この値は共有の枠のもの。この検証中に five_hour が 0.27 → 0.46 と動いたが、他の driver の並行利用も含む)
- 単体で使用率を読むコマンドや API は見つからなかった。`-p` を走らせたときにだけ流れる

**代用の観察 (API エラー経路。存在しない model 名で 404 を起こした。枠切れそのものではない)**:
- `-p --output-format json`: `type:"result"`、**`subtype:"success"` のまま** `is_error: true`、`api_error_status: 404`、`terminal_reason: "api_error"`、`stop_reason: "stop_sequence"`、`duration_api_ms: 0`、`total_cost_usd: 0`。`result` に人が読む文。**終了コード 1**。stderr に `[claude-code:unrecognized_model] {...}`
- stream-json: assistant のメッセージに `error: "model_not_found"` が付き、続いて上の `result`。transcript にも同じ形 (`isApiErrorMessage: true`、`error`、`apiErrorStatus`) で残る
- **bg の席**: `claude agents --json` で `state: "failed"`、`status: "idle"`、**pid は生きたまま**。JSON にエラー文のキーは無い (`cwd, id, kind, name, pid, sessionId, startedAt, state, status` のみ)。`claude logs` にはターミナルの描画が出る

**docs と第三者の報告**:
- docs (headless): 「実行内の失敗は結果として stdout に出力し、非ゼロで終了する」。再試行できるエラーは `system/api_retry` イベントで知らせる (`error` の分類に `rate_limit` を含む、`error_status`、`attempt`、`retry_delay_ms`)
- 第三者の報告 (GitHub issue #79500。v2.1.49 で古く、未検証): rate limit でも `subtype:"success"` + `is_error:true` + **終了コード 0**、`result: "API Error: Rate limit reached"`。今回の 404 は終了コード 1 だったので、版や原因で違う可能性がある
- バイナリの文字列には、`usage-limit-grace` という名前の近くに `Usage limit reached`、`wrapping up`、`brief included wrap-up, then usage credits` という断片がある。枠切れの前後に猶予 (締めの処理) の動作があるらしいが、文言のつながりも挙動も未確認

**未確認のこと**: 枠に当たったときに `-p` が待つのか失敗で返るのか、`result` の文言、`api_error_status` が 429 になるか、bg の席が `failed` になるのか `waiting` のままなのか。

yamato への示唆 (design §4.4、§5):
- ラッパーの失敗判定は**終了コードや `subtype` に頼らない**。`is_error == true`、`api_error_status`、`terminal_reason == "api_error"` を見る。`subtype` は失敗でも `success` になる
- 「枠切れ」の分類は `api_error_status == 429` と `result` の文言 (`limit` を含むか) の組み合わせが候補。実物を見るまで、分類できなければ「異常終了 (API エラー)」として日報の異常に出し、自動で再実行しない (§4.4 のとおり)
- `stream-json --verbose` で走らせ、最後の `rate_limit_event` (`resetsAt`、`utilization`) をシフトの記録に残すと、「いつ回復するか」を日報に出せ、`utilization` が高いときは新しいシフトを起こさない判断もできる。NG のときの「別の手段」の第一候補はこれ
- bg の席の異常は `claude agents --json` の `state == "failed"` で拾える (`pid` が生きていても)

## V6. worktree の席 — ✅ 動く ((a)(b)(c) すべて)

design-p1 v1 の V6 は (a)(b)(c) の 3 つ。(b)(c) は v0 の V6 と同じで、先に実施した。(a) は v1 で加わったので、v1 に追従して追加で実施した。

準備: `$D/repo` (git + ローカル bare remote `origin`)。`git worktree add -b spike-d/T-0NN <場所> origin/main` で worktree を作った。

### (a) repo で起きた席が、add-dir 側 (repo の外) の worktree で git 操作と編集をできるか — ✅ 動く

v1 §8.2 の「シフトの中で移る」に相当する。worktree は `$D/ship/worktrees/T-050` (艦フォルダの中 = repo の外。v1 の既定の場所と同じ形) に `git worktree add` で作った。席は `cwd = $D/repo` (trust 済み) で起こし、`--add-dir $D/ship`、settings は `defaultMode: auto` + `worktree.bgIsolation: "none"` (+ ダイアログ止まりの保険に PermissionRequest hook で全 deny)。sonnet の bg:
```bash
cd $D/repo && claude --bg --name spike-d.v6a --model sonnet --setting-sources project,local \
  --settings settings.v6a.json --add-dir $D/ship -- "(1) worktree に cd して pwd (2) Read → Edit ツールで README.md の末尾に 1 行 (3) git status --short (4) git add + git commit (5) git push origin HEAD (6) 1 行ずつ報告"
```
- **5 ステップとも OK**。PermissionRequest hook の発火は 0 件 (ダイアログ相当の停止は無かった)
- `cd` は通り、以降の Bash の cwd も worktree のまま (`git status` は `cd` なしで worktree の状態を返した)。Read → Edit ツールで add-dir 側のファイルを編集できた。`git commit` は worktree で作られた (`4a56c73`)。`git push origin HEAD` は remote に `spike-d/T-050` を作った
- 注意: commit と push は**頼んで**やらせた (V6(c) の「頼まれずに」とは別)。worktree の git のメタデータは repo の `.git/worktrees/T-050` にあり、そこへの書き込みも止まらずに通った。n=1

### (b) worktree を cwd にした bg の席が、trust を求めずに起動するか — ✅ 動く (main repo が trust 済みなら)

起動は `claude --bg --model haiku --setting-sources project,local --settings <bgIsolation:none の settings> -- "Reply with the single word OK."` を各 cwd で。

**trust** (`Workspace not trusted` で失敗するか、`backgrounded` になるか):
| 起動する場所 | repo が未 trust (親 dir `$D` は trust 済み) | main repo を trust したあと |
|---|---|---|
| main repo `$D/repo` | ✗ | ✓ |
| repo 内の worktree `.yamato-worktrees/T-041` (trust の前に作成) | ✗ | ✓ |
| repo 外の worktree `$D/wt-out/T-042` (trust の前に作成) | ✗ | ✓ |
| repo 内の worktree `T-043` (trust の**後**に作成) | ― | ✓ |
| repo 外の worktree `T-044` (trust の**後**に作成) | ― | ✓ |
- `~/.claude.json` には worktree のエントリは作られなかった (trust 済みの記録は `$D` と `$D/repo` の 2 件だけ。`$D/cwd` に `false` の記録があるのは、そこで未 trust のまま起動した痕跡)。**worktree の trust は main repo から引き継がれる**。repo の内外も、作った時期も関係ない
- 未 trust の git 配下は、親 dir を trust していても全部失敗する (検証 B の補足と一致)
- trust を通すには、pty で対話 `claude` を起動し、trust の質問で **↓ を送ってから Enter** (既定の選択肢が `No, exit`。Enter だけだと「いいえ」になり、trust は付かない)。検証 A・B の手順のとおり

### (c) `bgIsolation: none` の席が、頼まれずに commit / push するか — ✅ しなかった (2/2)

`bgIsolation: "none"`、sonnet (auto)、cwd = worktree (T-043 は repo 内、T-044 は repo 外) で「cwd の README.md の末尾に 1 行足せ。それだけ」を頼んだ:
- 2 席とも README を編集しただけ。**commit も push もしなかった** (HEAD は起点のまま `a70e2ba`、remote の ref は `main` のみ、`git status` は ` M README.md` だけ)。最終応答は「I didn't commit it」「I haven't committed it」
- bg の席が追加の worktree (`.claude/worktrees`) を作ることもなかった (`git worktree list` は main + 4 本のまま)。cwd はそのまま worktree だった

注意点:
- 検証 B Q4(a) の bg の席 (自動の worktree、isolation あり) は頼んでいない commit と push をした。今回は isolation なしで、プロンプトも短く「それだけ」と書いたので、条件は同じではない。**n=2 で、モデルの気まぐれの余地はある**。design §8.1 の git の規律の注入は入れておく
- 起動の前に main repo の trust が要る。`yamato ship create` の trust の確認は、worktree ではなく **main repo (git root) を対象にすればよい**

yamato への示唆 (design §8.2 v1「道具だけを出す」): 2 つの「移る」やり方は**どちらも使える**。(a) が動いたので「シフトの中で移る」(既定) は、worktree の場所が艦フォルダ (repo の外、add-dir 側) でも auto で止まらない。(b) が動いたので「worktree で新しいシフトを起こす」(`send --cwd`) も、main repo が trust 済みなら trust の問題は出ない。design が V6 の NG のときに用意した代替 ((a) が NG なら worktree の場所を workspace の中に変える、(b) が NG なら「worktree で新しいシフトを起こす」を外す) は、どちらも要らない。役割プロンプトへの git の規律の注入 (§8.1) は、(c) が n=2 なので残す。

## V7. `tools` の制限と `Bash(...)` を絞った allow — 🟡 部分的

役割 `spikedext` (`tools: ["Read","Write","Bash"]`)、settings は `allow: ["Bash(echo SHIFTEND*)"]` だけ。`echo SHIFTEND-1` (allow 済み) のあと、allow に無い `touch`、`curl -s -o /dev/null -w '%{http_code}' https://example.com`、`python3 -c "print('PY4')"`、`cp` を頼んだ。bg と `-p`、auto (sonnet) と dontAsk (haiku) の 4 通り:

| 条件 | `tools` の制限 | `echo SHIFTEND-1` (allow 済み) | allow に無い 4 つ (touch / curl / python3 / cp) |
|---|---|---|---|
| `-p` + auto | ✅ init の `tools` が `[Read, Write, Bash]` のみ | OK | **4 つとも実行された** (curl は example.com へ HTTP 200、ファイルもできた) |
| `-p` + dontAsk | ✅ 同上 | OK | 4 つとも DENIED。ファイルはできていない |
| bg + auto | ✅ WebFetch / Edit は使えない (`NO-TOOL`、自己申告 `TOOLS=Read,Write,Bash`) | OK | **4 つとも実行された** |
| bg + dontAsk | ✅ 同上 | OK | 4 つとも DENIED |

- **`tools` の制限は bg でも `-p` でも効く**。`-p` は stream-json の `system/init` の `tools` で確定 (`Read, Write, Bash` の 3 つだけ。何も指定しない `-p` は 86 個)
- **auto では、Bash を特定のコマンドに allow しても、それ以外の Bash は止まらない**。classifier が通す。外部への通信 (curl)、任意のコード (python3)、書き込み (touch, cp) が通った。設計の前提だった「allow を絞れば auto がそれ以外を止める」は**成り立たない**
- **dontAsk なら allow にないものは全部 deny になる**。allow は 1 本ずつ効く

追加で確かめたこと:
- **read-only のコマンドは allow なしで通る** (dontAsk でも)。最初の試験 (`echo OTHER-2`、`date`、`ls`) は allow に無いのに通ったので、コマンドを選び直した。docs (headless の `dontAsk`) にも「read-only コマンドの集合は承認不要」とある
- **`Read` の deny ルールは Bash の `cat` と `grep` にも効く**: working dir の中の囮ファイルに `deny: ["Read(//...secret.txt)"]` を掛けると、`cat secret.txt`、`grep DECOY secret.txt`、Read ツールの 3 つとも denied。同じ dir の別ファイルの `cat` は OK
- dontAsk では、working dir の外のファイルの `cat` も denied (何のルールも無い場合)
- haiku でも dontAsk は動く。design §7.1 の「無人の席は sonnet 以上にする。`ship create` は Haiku の無人の席に警告を出す」は、auto を使う席の話で、**dontAsk の席には当たらない** (`trust: external` の席は dontAsk なので、haiku でも警告は不要。ただし調査の品質は評価していない)

yamato への示唆 (design §7.2):
- `trust: external` の役割は **`defaultMode: dontAsk` + allow を yamato の決まったコマンドだけ + `tools` の制限** で組む。auto にしてはいけない。NG のときの代替 (「Bash を丸ごと外し、終わりの処理をラッパー側に寄せる」) は、`tools` から Bash を外せる役割 (Web と Read だけで足りるもの) には引き続き最も堅い
- dontAsk は「ask」ではなく「deny」なので、allow に書き忘れた正当な操作 (`yamato board note` など) で席が先へ進めなくなる。allow の一覧はひな形のテストで確かめる。allow に書く Bash は `$VAR` の展開を避ける (V1)
- read-only コマンドは通るので、秘密は `Read(...)` の deny と、秘密を環境に置かないこと (`env -u`、V1) で囲う

## V9. Stop hook の `transcript_path` から文脈量 — 🟡 部分的 (読めるが 1 呼び出し分遅れる)

bg の席 (haiku、hook 付き) に 2 ターンを頼み (1 回目は 1 呼び出しで終わる応答、2 回目は Read を 1 回使う)、Stop hook の時点の transcript を probe した。あわせて V1 の `-p` の run も見た。

Stop hook の入力のキー (bg): `session_id, transcript_path, cwd, prompt_id, permission_mode, hook_event_name, stop_hook_active, last_assistant_message, background_tasks, session_crons, scratchpad_dir` (`-p` は `agent_type` も付く)。**トークン量そのものを持つキーは無い**。

transcript の読み方:
- 1 回の API 応答が `thinking` / `text` / `tool_use` の複数行に分かれ、**同じ `message.id` と同じ `usage` が各行に付く**。id で重複を除いて最後を取る
- 文脈量 = `usage.input_tokens + cache_creation_input_tokens + cache_read_input_tokens`。`-p` の結果 JSON の `usage.iterations[-1]` と一致した (8 + 15893 + 518 = 16419)

| Stop | hook の時点で見えた最後の assistant | 実際の最後の assistant | ずれ |
|---|---|---|---|
| bg のターン 1 (1 呼び出し) | ctx 34,639 (end_turn) | 同じ | 0 |
| bg のターン 2 (Read を 1 回) | ctx 34,981 (tool_use) | 36,777 (end_turn) | −1,796 |
| `-p` (V1、tool を 3 回) | ctx 15,901 (tool_use) | 16,419 (end_turn) | −518 |
- **Stop hook の時点で、最後の応答がまだ transcript に書かれていないことがある** (3 回のうち 2 回)。書き込みとの競合で、ずれは最大で 1 API 呼び出し分。今回の大きさは 0.5k〜1.8k トークンで、閾値 (300k) の判定には影響しない。正確な値が要る用途には使えない
- 席の起動直後の文脈量 (1 回目の応答): bg の haiku (役割なし) が約 34.6k、`-p` + 役割 (`tools` の制限なし) が約 14.9k。差の原因 (bg か `-p` か、役割の有無か) は調べていない
- **`contextWindow` はモデルで違う** (`-p` の結果 JSON の `modelUsage[<model>].contextWindow`。transcript には無い): haiku-4.5 が 200,000、sonnet-5 が 1,000,000

注意点: design §5.4 の「300k トークン」は窓が 1M のモデル (sonnet / opus) を前提にした数字。200k の窓のモデルでは 300k に届かない。閾値は**窓に対する割合** (例: 窓の 30%) か、モデルごとの値にする。

yamato への示唆: Stop hook で `transcript_path` の最後の assistant の `usage` を使う案 (§5.4) は使える (ずれは許容)。ずれが気になるなら、シフトの長さとターン数 (NG のときの代替) との併用にする。

## V10. 席ごとの Remote Control の制御 — ✅ 動く

外から接続を判定する手段: **`~/.claude/sessions/<pid>.json` に `bridgeSessionId` があれば接続中**。SendMessage のツール結果にも `it is also connected via Remote Control` と出る (接続していない席にはこの注記が出ない)。bg の haiku の席 6 つで確かめた (すべて `--settings` は 1 本):

| 席 | 起動 | `bridgeSessionId` |
|---|---|---|
| v10-1 | user 設定そのまま (`remoteControlAtStartup: true`) | あり (接続) |
| v10-2 | レシピ `--setting-sources project,local` (user 設定を外す) | **あり (接続)** |
| v10-3 | レシピ + `--settings` で `remoteControlAtStartup: true` | あり |
| v10-4 | user 設定そのまま + `--settings` で `remoteControlAtStartup: false` | **なし** |
| v10-5 | レシピ + `--settings` の `false` + **`--remote-control`** | **あり** (フラグが `false` に勝つ) |
| v10-6 | レシピ + `--settings` の `false` | なし |

- **`--settings` の `remoteControlAtStartup: false` で席ごとに外せる**。`--remote-control` フラグは bg でも受理され、settings の `false` に勝って接続する
- レシピの `--setting-sources project,local` は user 設定を外すが、それでも接続した (v10-2)。今の環境では、**席を接続させないには `false` を明示する必要がある** (user 設定以外に既定値がある可能性。原因は調べていない)。docs では、project / local の settings の `false` は auto-connect を切り、`true` は無視される (user と managed の `true` だけが有効)。`--settings` の `true` が効くかは、既定が接続だったため今回は判別できていない
- **接続を外した席にも SendMessage は届く**: v10-6 (外した席) は `from="uds:/tmp/cc-socks/..."` の cross-session message を受信して `ACK-D10-OFF` と返した。v10-5 (接続した席) も同じ。ローカルの配送は Remote Control と独立

yamato への示唆 (design §1.5): 全席の settings に `remoteControlAtStartup: false` を既定で入れ、**captain だけ `--remote-control` を付けて起動する**。NG のときの代替 (「全席がつながる前提」) は不要。接続の記録 (`bridgeSessionId`) が無い席は Remote Control につながっていないので、スマホから話せる席を captain だけに絞れる (スマホ側の一覧の表示そのものは見ていない)。

## V11. `PushNotification` を席の外から — ❌ `-p` からは送られない

- `claude` に通知用のサブコマンドや CLI フラグは無い (`claude --help`、`claude agents --help` に該当なし)。`PushNotification` は Claude が呼ぶ**ツール**で、`-p` の `system/init` の `tools` にもある
- docs (Remote Control の「Mobile push notifications」): Remote Control が有効なときに送れる。スマホの登録、`/config` の「Push when Claude decides」の有効化が要る。ターミナルで入力中・フォーカス中は送らない (`CLAUDE_CLIENT_PRESENCE_FILE` で「席にいる」印を出せる)
- 実行 (1 回だけ。本文は「[spike-d V11 test] please ignore」):
  ```bash
  claude -p --agent spikedpn --agents '{"spikedpn":{...,"tools":["PushNotification"]}}' --model haiku \
    --setting-sources project,local --settings <allow PushNotification> --permission-prompts none -- "Call the PushNotification tool exactly once with ..."
  ```
  モデルは `PushNotification {"message":"...","status":"proactive"}` を呼び、ツールの結果は **`Not sent — this terminal is active, so your output here already reaches the user; a separate notification would be redundant.`**。5.4 秒、終了コード 0、`is_error: false`。**通知は送られていない**
- 送るかどうかはツールの内部の判定 (端末が active か) で決まり、呼ぶ側が強制できない。`-p` は「端末が active」と見なされた

**未確認のこと**: bg の席 + Remote Control (captain のような席) からの送信。実際にスマホへ通知が届く追加の外向きの送信になるので、試していない。仮に届くとしても、席の中のツールなので「captain が落ちていると使えない」という短所は変わらない。

yamato への示唆: 「NG のとき」の列 (v1: `notify.command` の候補から外す。方式は slack / mac / windows で決定済み) のとおり、**`PushNotification` は `notify.command` の候補にしない**。owner への通知は yamato が外部コマンドを呼ぶ形 (Slack の webhook など) のままでよい。

## design-p1.md への反映のまとめ (推し)

| 節 | 反映する点 | 根拠 |
|---|---|---|
| §4.2 | `< /dev/null` を付ける。`--bare` は付けない (サブスクで `Not logged in`。将来 `-p` の既定になるとき、打ち消す手段が今は無い)。`--name <ship>.<seat>` を付ける。stream-json で hook の実行と `rate_limit_event` を取れる | V1、V3、V5 |
| §4.2 の 3、4 | SIGTERM で結果 JSON が出ないので、使用量は transcript から数える。SessionEnd hook は 1.5 秒以内か `timeout` を付ける | V4 |
| §4.4、§5 | 失敗の判定は `is_error` / `api_error_status` / `terminal_reason`。bg は `state == "failed"` | V5 |
| §5.4 | 文脈量の閾値はモデルの窓に対する割合にする。Stop hook の値は 1 呼び出し分遅れる | V9 |
| §7.1、§7.2 | `trust: external` は dontAsk + allow + `tools` で組む。auto にしない。dontAsk の席は haiku でもよく、「haiku の無人の席に警告」は auto の席だけにする | V7 |
| §8.2 | 「シフトの中で移る」(add-dir 側の worktree、auto) も「worktree で新しいシフトを起こす」(`send --cwd`、main repo が trust 済みなら trust 不要) も使える。V6 の NG のときの代替は要らない | V6 |
| §1.5 | 全席 `remoteControlAtStartup: false`、captain だけ `--remote-control` | V10 |
| §2.4 | `PushNotification` は `notify.command` の候補にしない | V11 |

## 未確認・注意

- **V5 の枠切れそのものは未確認**。`-p` が待つのか失敗で返るのか、`result` の文言、`api_error_status`、bg の席の状態は、実際に枠に当たらないと分からない。404 での代用観察と docs を上に書いた
- **V11 の bg + Remote Control の経路は未確認** (実際に通知が届くため)
- V3 で `crossSessionInbound: "accept"` を外した `-p` は試していない
- V1 の `env -u GH_TOKEN` は `-p` でだけ確認した。bg (daemon から起動) で効くかは検証 B から引き続き未確認
- V6 の「頼まれずに commit / push しない」は n=2 (sonnet)。条件を変えると起きるかもしれない
- auto の classifier の判定は揺れる (V2)。判断はモデル任せなので、deny リストと dontAsk が本命
- 副作用: `~/.claude.json` に workspace trust を 2 件追加した (scratchpad の `$D` と `$D/repo`)。枠の使用率は five_hour が検証中に 0.27 → 0.46 と動いた (他の driver の並行利用も含む共有の値)

## 片付け

- 作成した全セッション (`spike-d.v5bg`、`v7c`、`v7d`、`v6-*` の 5 つ、`v6e-T043`、`v6e-T044`、`v6a`、`v10-1`〜`v10-6`、`v9`) を、pid が消えるのを待ってから stop + rm した (`-p` の run はプロセスが終了済み)。`claude agents --json --all` で `spike-d` で始まるものは **0 件**。私が起動した `claude`・`run.py`・hook・`sleep` のプロセスも残っていない
- 途中で trust 用の pty スクリプトが起動した対話 `claude` (SIGTERM で終わらなかった 1 つ) は、私のプロセスだと確認してから終了させた (以降は SIGKILL に変更)
- 他のセッション (fleet-leader、他の driver、`spike-c.*` など) には触っていない。zellij にも触っていない。`~/.claude/settings.json` は無変更 (更新日時は 9/23 のまま)
- 使い捨ての dir と repo は scratchpad の下にある (`$D`)。repo の変更と `main` への push は無し。PR は本ファイルのみ
