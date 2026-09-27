# yamato P0 検証 C: 未検証のまま残っていた Claude Code の挙動 — 報告

- 実施: 2026-09-26 10:47–16:55(12:25〜15:36 は利用上限で中断。Q3 は再開後にやり直した) / Claude Code 2.1.283 / macOS / headless のみ
- 席はすべて `spike-c.*`。モデルは haiku、auto が要る場面(検証 1 の読み戻し)だけ sonnet
- cwd は scratchpad 配下の使い捨てディレクトリ(git なし)。`claude --bg` は未 trust の cwd を `Workspace not trusted` で拒否するので、pty スクリプトで trust プロンプトを承認してから使った。**既定の選択肢は「No, exit」なので、Down を送ってから Enter**(検証 A/B と同じ)
- 席の起動は A/B の推奨レシピと同じ形(`--setting-sources project,local` を付け、`--settings` は 1 本、最後に `--`)。transcript は `~/.claude/projects/<cwd>/<sessionId>.jsonl`、席の内部状態は `~/.claude/jobs/<id>/state.json` と `timeline.jsonl`(後者は非公開 I/F)を読んだ
- 状態の遷移は、`claude agents --json --all` を 2 秒ごとに読んで変化だけを記録するポーラーで取った
- zellij は専用の使い捨てセッション `spike-c-view` だけを使った(`fleet-main` には触れていない)。zellij を操作するコマンドは `env -u ZELLIJ -u ZELLIJ_SESSION_NAME -u ZELLIJ_PANE_ID zellij --session spike-c-view ...` で実行した
- 前提: ユーザー設定は `defaultMode: dontAsk`、`remoteControlAtStartup: true`、`env` に `GH_TOKEN` など(値はどこにも書かない。比較にはハッシュの一致だけを使った)。`--setting-sources project,local` を付けた席でも `/rc connecting…` と出て、Remote Control には繋がっていた

## まとめ

| # | 問い | 判定 |
|---|---|---|
| 1 | SessionStart hook の注入の上限 | ✅ **hook 1 本の出力が 10,000「文字」を超えると、本文の代わりに約 2KB のプレビュー + 全文の保存先パスが届く**。判定は hook ごと(合算ではない)。バイトではなく文字数(日本語 5,000 文字=14,940 バイトは全文届く)。警告は本文に埋め込まれるだけ |
| 2 | 環境変数の伝搬 | 🟡 **起動側の環境変数は席に届かない**(`env -u GH_TOKEN` は無効)。席に見える `GH_TOKEN` は起動側ではなく、daemon 側・ユーザー設定の `env` 経由。消すには settings の `env` で空にする(EMPTY)か、SessionStart hook で `unset`(UNSET) |
| 3 | attach による常駐 | ✅ **attach していれば 1 時間を超えても止められない**(RC なしの条件で 75 分生存。attach なしの対照は 60 分で停止)。ただし **Remote Control に繋がっている席は attach なしでも止められない**(4 時間 48 分生存)。ユーザー設定の `remoteControlAtStartup: true` が効いていると、1 時間停止の規則は全席に効かない |
| 4 | Monitor による常駐 | ✅ 30 分の期限切れの通知で席が起き、自分で再アームできた(RC あり・なしとも 2 回ずつ)。Monitor が生きている間、席は `busy`。ただし再アームにはモデル呼び出しが要り、利用上限に当たると失敗して常駐が切れた |
| 5 | `state` の意味 | ✅ **`state` は「人間に何を求めているか」を席の発言から意味づけしたラベル。プロセスの実状態は `status` / `pid`**。A/B の食い違いはこれで説明がつく |

## Q1. SessionStart hook の注入の上限 — ✅ 10,000 文字まで。超えるとファイル参照に化ける

**仕掛け**: `--settings` に SessionStart hook を入れ、`hookgen.py <文字数> <json|plain>` を実行させた。生成するのは「先頭行 `BEGIN-CANARY`、`L00001 <md5 hex>` 形式の行番号つき本文、末尾行 `END-CANARY-<n>`」で、切り詰めの位置が行番号で読める。`json` は `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"…"}}`、`plain` は stdout に本文をそのまま出す形。

```bash
claude --bg --name spike-c.t10001p --model haiku --setting-sources project,local \
  --settings '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"python3 hookgen.py 10002 plain"}]}]}}' \
  -- "Reply with just OK."
```

確認は 2 通りで行った。(a) transcript の `hook_success`(stdout の長さ)と `hook_additional_context`(**席に実際に注入された本文**の長さ・先頭・末尾)、(b) 席に「見えている最後の行」と「切り詰めの警告」を自己申告させる。

| hook の出力 | plain (stdout) | json (`additionalContext` 本文) |
|---|---|---|
| 約 1KB | ✅ 全文 | ✅ 全文 |
| 3,999 / 7,999 / 9,899 / 9,999 文字 | ✅ 全文 | ✅ 全文(9,899 / 9,999) |
| **10,000 文字** | ✅ 全文 | ✅ 全文(stdout はラッパー込みで 10,230) |
| **10,001 文字** | ❌ persisted | ❌ persisted(stdout 10,231) |
| 10,100(plain のみ)/ 10,183 / 約 50KB / 約 200KB | ❌ persisted | ❌ persisted(10,183 / 約 50KB / 約 200KB) |
| 日本語 3,000 文字(8,940 バイト) | ✅ 全文 | — |
| 日本語 5,000 文字(14,940 バイト) | ✅ 全文 | ✅ 全文(stdout 15,024 バイト) |

- 「persisted」のとき、席に注入される本文は次の形(約 2.3KB、サイズによらずほぼ一定)になる。**本文は届かない**:
  ```
  <persisted-output>
  Output too large (199.9KB). Full output saved to: ~/.claude/projects/<cwd>/<sessionId>/tool-results/hook-<uuid>-stdout.txt

  Preview (first 2KB):
  BEGIN-CANARY size=… / L00001 … / … / L00028 …
  ...
  </persisted-output>
  ```
  プレビューは先頭 28 行(約 2KB)で、末尾の `END-CANARY` は含まれない。席は「Output too large … preview truncated at 2KB」と正しく認識して自己申告した。hook 自体の `exitCode` は 0 のまま(hook のエラー扱いにはならない)
- 保存ファイルは**全文が完全**だった(200KB の場合 204,732 バイト、`BEGIN-CANARY` から `END-CANARY-204800` まで)。ファイル名は plain が `hook-<uuid>-stdout.txt`、json が `hook-<uuid>-1-additionalContext.txt`(本文だけで、ラッパー JSON は含まない)
- **判定の単位は「文字数」**。日本語 5,000 文字(14,940 バイト)が全文届いたので、バイト数ではない。json 形式は `additionalContext` の**本文の長さ**で判定される(ラッパー込みの stdout が 10,230 文字でも、本文 10,000 文字なら通る)
- **判定は hook 1 本ごと**。3 本の hook(6,000 / 6,001 / 6,002 文字、合計約 18KB)を並べても、3 本とも全文届いた。なお、**同じコマンド文字列の hook を 2 本並べると 1 本に統合される**(重複排除)
- 席は保存ファイルを読み戻せた。パスは persisted-output の本文に書かれているので、席がそのまま使える
  - haiku(既定の権限モード): `Read` ツールで読めた。**権限ダイアログは出なかった**。`offset` を指定して末尾の `END-CANARY-51201` まで取れた(最初の `offset` がファイルの行数を超えて 1 回やり直し、計 2 回)
  - sonnet(`--permission-mode auto`): `Bash` でファイルの末尾を読んで `END-CANARY-51201` を取った(1 回)

**注意点**
- 上の閾値は 2.1.283 での観測で、設定で変えられるかは調べていない
- 「席に届いた」の判定は transcript に記録された注入本文による。モデルが最後まで読んだか(長文の中間の取りこぼし)は見ていない。1KB のときは席が末尾行まで正しく答えた
- 10,000 文字でも数千トークンになる。注入は毎回の起動(resume の SessionStart を含む)で走る

**yamato への示唆**
1. **§8.2 の注入(役割 prompt / 役割 memory / knowledge.md / 担当ビュー / handoff / inbox 未読)は、hook 1 本あたり 10,000 文字以下に収める**。1 本にまとめると超えやすいので、**hook を分割する**(判定は hook ごとで、3 本(合計約 18KB)がすべて全文で届いた。同じコマンド文字列は統合されるので、引数(種別)で区別すること)。日本語は文字数で数えるので、10,000 文字≒30KB まで入る
2. 上限を超えそうな中身(knowledge.md、長い handoff)は、**hook の側で切って「全文は `<path>` を Read せよ」と書く**設計にする。persisted のパスは session id 入りで席ごとに変わるので、それに頼らず、yamato が自前のファイルに全文を書いてそのパスを注入する
3. 上限超過は、本文がプレビューに化けるだけで hook のエラーにはならない(`exitCode` は 0 のまま)ので、気づきにくい。**`yamato hook session-start` の側で出力の文字数を検査し、10,000 を超えるなら警告を stderr に出して切る**(自前の検査)。席にも「`<persisted-output>` が見えたら、パスを Read してから始めよ」と役割 prompt に書いておくと安全網になる
4. 席が保存ファイルを Read するのに権限の設定は要らなかった(default / auto とも)。ただし deny リストに `Read(~/.claude/**)` のような広い指定を入れると読めなくなる恐れがある(未検証)

## Q2. 環境変数の伝搬 — 🟡 起動側の env は届かない。消すなら settings の `env` か hook で

**手順**: 席の Bash で `python3 envprobe.py` を実行させる。envprobe は環境変数ごとに `UNSET / EMPTY / SET(長さ + sha256 先頭 8 文字)` だけを出す(値は出さない)。起動側は毎回 `SPIKEC_MARK=<固有値>` を付けて起動し、席に届くかも見た。席はすべて `--permission-mode bypassPermissions`(Bash を通すため)。

参照値: 検証を実行している pane のシェルの `GH_TOKEN` と、`~/.claude/settings.json` の `env.GH_TOKEN` は、ハッシュが一致(同じ値)だった。

| # | 起動 | 席の `GH_TOKEN` | 起動側の `SPIKEC_MARK` |
|---|---|---|---|
| v0 | 対照(何も外さない) | SET(現在の値と一致) | UNSET |
| v1 | `env -u GH_TOKEN` + `--setting-sources project,local`(A/B の推奨レシピ) | **SET** | UNSET |
| v2 | `env -u GH_TOKEN` のみ | **SET** | UNSET |
| v3 | `--setting-sources project,local` + `--settings '{"env":{"GH_TOKEN":""}}'` | **EMPTY** | UNSET |
| v4 | v3 + `env -u GH_TOKEN` | **EMPTY** | UNSET |
| v5 | `--setting-sources project,local` + SessionStart hook で `echo "unset GH_TOKEN" >> "$CLAUDE_ENV_FILE"` | **UNSET** | UNSET |
| v6 | `--settings '{"env":{"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB":"1"}}'` | 未確認(席が `permission prompt` で止まった。原因は未調査) | — |
| v7 | `--settings '{"env":{"SPIKEC_MARK":"via-settings","GH_TOKEN":""}}'` | EMPTY | **SET**(settings の `env` で渡した値が見える) |
| v8 | v3 と同じ設定で、**hook のプロセスから**見た env | EMPTY(settings の `env` は hook にも効く) | — |

- **起動側の環境変数は席に一切届かない**。`SPIKEC_MARK`、`FLEET_TASK_ID`、`ZELLIJ_SESSION_NAME` はすべて UNSET(起動側の pane には設定されている)。だから `env -u GH_TOKEN claude --bg …` は**何の効果もない**(v1, v2 で SET のまま)。逆に、席に渡したい変数を起動側の env で渡すこともできない
- 席に見える `GH_TOKEN` の出どころ: `--setting-sources project,local` で user 設定を外した席(v1)にも SET で見えた。`ps eww` で環境を読むと(値は出さずハッシュだけ比較)、
  - **daemon プロセスの `GH_TOKEN` は現在の値と違った**(ハッシュが不一致。daemon が起動したときの古い値が残っている。fleet #315 の「焼き付き」と同じ現象と見られる)
  - **席のプロセスは現在の値(user 設定の `env` と同じ)だった**。daemon の古い値が席に来たわけではない
  - したがって、user 設定の `env` が `--setting-sources` に関係なく席に入っていると考えられる(daemon が席を起こすときに重ねている可能性がある。仕組みの詳細は未確認)。**確実に言えるのは、席が見る `GH_TOKEN` が起動側の環境から来ていないことと、`env -u` では消えないこと**
- `claude daemon status` は `origin: transient — started on-demand by claude (pid …) in <別の cwd>` で、daemon は最初に `claude` を呼んだ cwd・環境で起動されている。`~/.claude/daemon.log` には `bg spare spawned host pid=…` が並ぶので、席は**事前に spawn された待機プロセス(spare host)を割り当てる**方式と見られる。起動側の env が届かないことと辻褄が合う(推定)
- ドキュメント(agent view の「Settings and provider」)も、dispatch 元のシェルから席に引き継ぐのは `PATH`、クラウドプロバイダの選択、`ANTHROPIC_DEFAULT_*_MODEL`、`CLAUDE_CODE_EXTRA_BODY` に限ると書いている。`GH_TOKEN` は含まれない。project 設定の `env` は席に効く、とも書かれている

**席に渡したくない変数を消す方法**
| 方法 | 結果 | 効く範囲・注意 |
|---|---|---|
| `--settings` の `env` で `""` | EMPTY | Bash と hook の両方で見えなくなる(v3, v8)。**空文字であって未設定ではない**。空文字を「設定済み」と扱うツールでは効かないことがある |
| SessionStart hook で `$CLAUDE_ENV_FILE` に `unset VAR` を追記 | UNSET | 確認したのは Bash ツールだけ(v5)。hook プロセス自体は影響を受けない |
| 起動側で `env -u VAR` | 効かない | 上記のとおり届かない |

- **`gh` は `GH_TOKEN` が空でも未設定でも、まったく同じ挙動**だった(`gh auth status` が同一の出力)。この Mac では macOS キーリングのログイン(`gho_***`)にフォールバックする。**`GH_TOKEN` を消しても、席が `gh` で GitHub に操作できることは変わらない**。「別のトークンを持たせない」目的なら、変数を消すだけでは足りない(キーリングは別の話)
- 席ごとに違う値を渡したいときは、席ごとに生成する `--settings` の `env` に書けばよい(v7)。`--settings` はパスで渡せば resume のたびに読み直される(A/B)ので、更新も次のシフトから効く

**yamato への示唆**
1. **A/B の推奨レシピの `env -u GH_TOKEN` は外してよい**(効果がない。書いておくと「効いている」と誤解する)。代わりに、設計 §4.1 のとおり、team / 役割の名前は hook の引数に埋め込む方針を維持する。環境変数で渡すなら `--settings` の `env`
2. 席に見せたくない変数(`GH_TOKEN` など)は、**チームの `settings.json` の `env` に `"VAR": ""` を入れる**。加えて厳密に「未設定」が要るなら SessionStart hook で `unset`(Bash 向け)。`team status` か起動時の検査で、席の Bash から `[ -z "${VAR+x}" ]` を確認するとよい
3. #315 の対策としては、yamato が daemon の環境に依存しないこと(値は `--settings` の `env` で席ごとに明示する)。daemon 自体は yamato が触ってはいけない(`claude daemon stop` は全 background セッションを止める)
4. 認証の隔離が目的なら、`gh` のキーリングログインが残る点を別途扱う必要がある(`GH_CONFIG_DIR` を席ごとに向けるなど。未検証)

## Q3. attach による常駐 — ✅ attach していれば止められない。ただし **Remote Control に繋がっている席も止められない**

docs(agent view「The supervisor process」)の規則: 「Finished or waiting for your next message, and unattached for about an hour: the supervisor stops the process」。「Working, paused on a permission prompt or other dialog, or attached」の席は止められない。

**1 回目(10:50〜)** — 全席 haiku で「OK と返して待て」。`att`(zellij の使い捨てセッション `spike-c-view` のペインで `claude attach`)、`det`(attach なし)、`detrc`(attach なし、user 設定つき)、`mon2`(Monitor が終わったあとの idle)。
- 75 分時点(12:05、最初のターン終了 10:50 から): **5 席とも pid が不変で、全員生存**。attach していない席も止まらず、attach の有無で差が出なかった
- 原因を調べた。全席の `state.json` に `bridgeSessionId`(Remote Control の接続)があった。ユーザー設定の `remoteControlAtStartup: true` のためで、**`--setting-sources project,local` を付けても接続される**(全席で `/rc connecting…`)。`~/.claude/daemon.log` の idle 停止(retire)の記録は、過去に 2 件だけ(`stale-spare, idle 61m` と `idle-prompt, idle 8h, worker 2.1.280 (daemon 2.1.281)`)
- → Remote Control を切った席を追加した。`--settings '{"remoteControlAtStartup":false}'` で `bridgeSessionId` が `None` になる(`state.json` で確認)
  - `norc`(RC なし、attach なし): 12:05:35 に idle → **13:06:08 に停止**(`bg retire 36ed12b0: idle-prompt, idle 61m` → `bg settled 36ed12b0 (done)`)
  - 放置後の 15:37: RC ありの `att` / `det` / `detrc` / `mon2` は **4 時間 48 分**経っても全員生存(pid 不変、`ps` の etime 04:47〜04:48)

**2 回目(15:38〜)** — 全席 RC なし。最初のターン終了は 15:37:57(norc-att)と 15:37:59(norc2)。`norc-att` は zellij のペインで `claude attach`(pid 20533)。

| 席 | 条件 | 結果(16:53、75 分後) |
|---|---|---|
| `norc-att` | RC なし + **attach あり** | **生存**。pid 39948 のまま(`claude attach` も生存)。`state=working, status=idle`(「待て」と書いたため。Q5) |
| `norc2` | RC なし + attach なし | **16:38:08 に停止**(`bg retire 3b22e981: idle-prompt, idle 60m` → `settled (done)`)。`state=done, pid なし` |
| `mon-norc` | RC なし + Monitor(Q4) | 生存。`state=working, status=busy`。30 分の期限切れ 2 回とも再アーム |
| (参考)`norc` | 1 回目の対照 | 61 分で停止(上のとおり) |

- **判定**: (a) attach なしの idle 席は、**最後のターン終了から約 60 分**で止められる(2 回とも 60〜61 分)。**定時スイープではない**(当初は「毎時 30 分」を疑ったが、13:06 と 16:38 で止まったので否定)。(b) **attach していれば止められない**(75 分生存)。(c) **Remote Control に繋がった席も止められない**(4 時間 48 分生存)。止められた席は `state=done`、`pid` なしになる
- `att` の attach は zellij のペインで `claude attach` を動かすだけで足りた。席内で `/status` を開くと `Session kind: background job · attached` と出た(RC ありの att で確認)。attach の有無は JSON には出ないが、`/status` で分かる。なお、attach 先で `/status` のようなダイアログを開いたまま放置すると `blocked / waiting / dialog open` になる(Q5)。ダイアログ中も止められないので、テストでは閉じてから測り直した(`att` の 1 回目の起点は 11:14:36)

**注意点**
- Remote Control の接続は `claude agents --json` には出ない。`~/.claude/jobs/<id>/state.json` の `bridgeSessionId`(非公開 I/F)で分かる
- 1 席あたりの RSS は約 330MB(`ps`)。常駐すると席の数だけメモリを食う
- zellij の疑似クライアントは 200x50 の pty で繋いだセッションで確認した。クライアントを繋がずに attach プロセスだけが残っている場合の保護は、今回は測っていない(spike-zellij-attach でも実測していない)
- 停止後に `claude attach` すると席が復活する点(spike-zellij-attach Q4)は、今回は再確認していない

**yamato への示唆**
1. **zellij のペインで attach している席は常駐する**。§10 の「窓を開ける = keep-alive」は実測で裏づけられた(75 分。RC なしの条件で確認)
2. **ユーザー設定に `remoteControlAtStartup: true` があると、全席が Remote Control に繋がり、1 時間停止の規則が効かなくなる**(席が溜まり、メモリを食う)。`--setting-sources project,local` では外れない。「常駐させない席は 1 時間で自然に止まる」を前提にするなら、**チームの `settings.json` に `"remoteControlAtStartup": false` を入れる**。設計 §4.1 の起動レシピの `settings.json` ひな形に足す候補
3. 席を常駐させる手段は 3 つ: (a) zellij のペインで attach、(b) Monitor(`busy`。Q4)、(c) Remote Control 接続(意図せず常駐する)。(c)は席の生存の根拠にしないこと
4. **常駐しているかは JSON からは分からない**。`team status` に出したいなら、`state.json` の `bridgeSessionId` か、席内の `/status` を使う(どちらも非公開・対話寄りなので、当てにしすぎない)。常駐の実態は「pid が消えていない」で見る
5. 停止された席(`state=done, pid なし`)の再開は、A のとおり `claude --resume <フル sessionId> --bg`

## Q4. Monitor による常駐 — ✅ 30 分の期限切れで起きて再アームできる(RC なしでも 75 分以上生存)

**手順**: 席に次を頼む。(1) `Monitor(command: "tail -n 0 -F <inbox>", timeout_ms: 1800000)` を張る、(2) `ARMED#1` と返してターンを終える(ポーリングしない)、(3) Monitor の期限切れや inbox の通知で起こされたら、`date` つきで log に 1 行書いて、同じ Monitor を張り直す。席は 2 つ: `spike-c.mon`(user 設定のまま=Remote Control あり)と、`spike-c.mon-norc`(`remoteControlAtStartup: false`。Q3 の切り分けのため)。加えて、期限を 60 秒にした `spike-c.mon2`(再アームは 2 回まで)で、期限切れの通知が席を起こすこと自体も早く確かめた。

| 席 | 張った | 期限切れ | 席が起きた(log) | 再アーム |
|---|---|---|---|---|
| mon2(60 秒) | 10:50:31 | 1 回目 | 10:51:39(`Monitor expired after 1m with no events delivered`) | ✅ |
| mon2 | 再アーム後 | 2 回目 | 10:52:43 | ✅ |
| mon2 | 再アーム後 | 3 回目 | 10:53:46(プロンプトどおり再アームせず `DONE`) | —(指示) |
| **mon(30 分、RC あり)** | 10:50:26 | 11:20:26 | 11:20:29(`working`)、log 11:20:35(`Monitor expired after 30m with no events delivered. Re-arm it if you still need the watch`) | ✅ 11:20:37 |
| mon | 11:20:37 | 11:50:37 | 11:50:39 | ✅ 11:50:41 |
| mon | 11:50:41 | 12:20:41 | 席は起きたが、そのターンが **`You've hit your session limit · resets 3:20pm`** で失敗 | ❌ 再アームできず(`blocked / idle`)。アカウントの利用上限の影響で、Monitor の挙動ではない |
| **mon-norc(30 分、RC なし)** | 15:39:40 | 16:09:40 | 16:09:44 | ✅ 16:09:47 |
| mon-norc | 16:09:47 | 16:39:47 | 16:39:48 | ✅ 16:39:50 |

- 期限切れは通知(`Monitor expired …`)として席に届き、**約 3〜5 秒で新しいターンが始まった**。席は指示どおり自分で Monitor を張り直した(transcript に 2 つ目以降の `Monitor` tool_use があり、`state.json` は `inFlight.kinds: ["monitor"]` に戻った)。**30 分の期限切れで起きて再アームできる**ことを、RC あり・なしの両方で 2 回ずつ確認した(RC ありの 3 回目は利用上限で失敗)
- Monitor を張っている間の JSON は `state: blocked, status: busy`(Q5)。**`status: busy`** なので、supervisor の「約 1 時間 idle で停止」の対象にならない。RC なしの `mon-norc` は、同じ時間枠で idle の `norc2` が 60 分で止められたとき(16:38)も生存していた(16:53 時点で 75 分、`busy`)。docs も「実行中の subagent・workflow・monitor は working として数える」と書いている
- **常駐は利用上限に弱い**: 期限切れで起きたターンがモデルの呼び出しに失敗すると、席は再アームできず、Monitor のない idle 席になる。その後は 1 時間の停止規則の対象になる(RC なしなら)

**yamato への示唆**
1. 常駐させたい席には、プロンプトに「期限切れの通知が来たら同じ Monitor を張り直せ」と 1 行書くだけで足りる。再アームは席自身の仕事になる(30 分ごとに 1 ターン走る=使用量がかかる。idle の attach 常駐はターンを消費しない)
2. A の結論(常駐させない席は asyncRewake の Stop hook、常駐させたい席は Monitor)は、そのまま維持できる。ただし **Monitor 常駐は「30 分ごとにモデルを呼べること」が前提**。使用量の上限や API の障害で再アームに失敗すると、席は黙って idle に落ちる。`team status` では、Monitor 席の `status` が `busy` でなくなったこと(と `state=blocked / idle`)を「常駐が切れた」として検知するとよい
3. 「ターンを消費せずに常駐させたい」なら、Monitor より zellij の attach(Q3)のほうが安い

## Q5. `state` の意味 — ✅ 席の発言の「意味づけ」。実状態は `status` / `pid`

**情報源は 2 つ**。(a) `claude agents --json --all` の `state` / `status` / `waitingFor` / `pid`(公開 I/F)、(b) `~/.claude/jobs/<id>/state.json`(`state` / `detail` / `tempo` / `inFlight`)と `timeline.jsonl`(`at` / `state` / `detail` / `text`=席の最後の発言)。(b)は非公開 I/F で、(a)の `state` の元になっている。

**ドキュメントの定義**(agent view の `state`。原文):
- `working`: 「A turn is running, or the session is between steps of work it drives on its own」。`status` はプロセスが今 `busy` かを示す
- `blocked`: 「The session is waiting on you: a question it asked, a permission or sandbox decision, an error only you can clear …。When the wait is an open prompt in a live process, `waitingFor` names it」
- `done`: 「The last turn finished what you asked for and the session is ready for your next prompt, whether or not its process is still alive」
- `failed` / `stopped`: 「The task ended with an error, or the session was stopped」
- 「A session that finished its turn and is waiting for your next instruction reads `done`, not `blocked`」
- `status`(プロセスが生きている間だけ): `busy` / `waiting` / `idle`。`waitingFor`(`status: waiting` のとき): `permission prompt` / `input needed` / `sandbox request` / `worker request` / `dialog open`

**観察**(`state.json` は `state / tempo`):

| 席の状況 | JSON: `state` / `status` / `waitingFor` | `state.json` | 備考 |
|---|---|---|---|
| 依頼を完了して返答(`Reply with just OK.`) | done / idle | done / idle | `detail: "replied OK"` |
| 「OK と返して、**そのあと待て**」 | **working** / idle | working / idle | `detail: "awaiting further instruction"`。プロセスは idle なのに `working` |
| 質問して回答待ち(赤か青かを聞く) | blocked / idle | blocked / blocked | 最後の発言が質問 |
| 完了できなかったと報告(ファイルが無い) | blocked / idle | blocked / blocked | `detail: "file … not found"` |
| 権限ダイアログ待ち(default モード + Write) | blocked / **waiting** / `permission prompt` | working / blocked | JSON だけ `blocked` に上書きされる |
| PermissionRequest hook で deny され、その旨を報告して終了 | done / idle | done / idle | 依頼文に「拒否されたらそう言え」と書いた |
| Monitor 待ち | blocked / **busy** | blocked / blocked、`inFlight.kinds: ["monitor"]` | `detail: "mon armed; awaiting Monitor …"` |
| Monitor 期限切れの通知で起きたターン | working / busy | — | |
| ツール実行中(Bash `sleep 120`) | working / busy | — | |
| attach 先で `/status` ダイアログを開いた | blocked / waiting / `dialog open` | — | 閉じたら working / idle に戻った |
| 存在しないモデル(`--model no-such-model-xyz`) | **failed** / idle(**プロセスは生存**) | failed / idle | `detail` はモデルのエラー文 |
| 不正なフラグで worker が起動前に終了 | failed / なし / pid なし | failed / idle | `detail: "exit 1 before init — error: unknown option …"`。**`claude --bg` 自体は exit 0 で `backgrounded · <id>` を出す** |
| `kill -9 <pid>` | 元の `state` のまま / なし(約 10 秒)→ **新しい pid で idle** | — | supervisor が再起動(docs のとおり) |
| `claude stop`(working / blocked の席) | **stopped** / なし / pid なし | — | 4 時間以上たっても `stopped` のまま |
| `claude stop`(done の席) | **done のまま** / なし / pid なし | — | |
| supervisor による 1 時間 idle の停止(Q3) | done / なし / pid なし | done / idle | daemon.log: `bg retire …: idle-prompt, idle 61m` → `bg settled … (done)` |

- **`state` は席が「人間に何を求めているか」を、最後の発言から意味づけしたラベル**。`detail` は `canary block extracted: …` のような短い要約で、席の発言ごとに変わる(誰がどう付けているかは非公開。席自身のツール呼び出しは transcript に無いので、Claude Code 本体が付けていると見られる=推定)。**言い回しで変わる**: 同じ「OK と返せ」でも、「そのあと待て」と書いた席だけ `working` になった(docs は「次の指示を待つ席は `done`」と書いているが、そうなるとは限らない)
- **プロセスの実状態は `status` と `pid`**。JSON の `state` は `state.json` の `state` を写すが、`tempo: blocked`(ダイアログ待ち)のときだけ `blocked` に上書きされ、`status: waiting` と `waitingFor` が付く

**A / B の食い違いの整理**
- A「停止後の `state` は `stopped` とは限らず `done` になる」: **`done` の席を止めても `state` は `done` のまま**。`stopped` になるのは、作業中や blocked の席を止めたとき。A で「stop 直後に一度だけ `stopped` と出た」のは、そのとき席が作業中や blocked だったためと考えられる(推定)。supervisor が idle で止めた場合も `done`
- B「PermissionRequest hook で deny された席が、ターンを終えたのに `state=blocked`、`status=idle`」: `state` は最後の発言の意味づけ。**質問や「できなかった」の報告で終わった席は `blocked / idle`**(今回の質問の席・ファイル無しの席で再現)。B の席の最後の発言は見直していないが、同じ仕組みで説明がつく。今回の deny hook の席は `done / idle` で終わった

**注意点**
- 上の対応は 2.1.283 の観察。`detail` の付け方(モデルによる要約か規則か)は確認していない
- 権限ダイアログ待ちの行は、deny hook を入れていない席(既定の permission モード)のもの。deny hook の行は、hook の JSON を返す小さなスクリプトで作り直した席のもの(最初の席は、`echo` の引用符が sh に剥がれて JSON が壊れ、deny が効かずにダイアログで止まった。これは私の設定ミス)

**yamato への示唆**
1. **生存の判定は `pid != null`**(A のとおり)。`state` は「人間に何か求めているか」の目安として使う。`state == blocked` の中身は `status` と `waitingFor` で分ける: `waiting` + `waitingFor`(`permission prompt` / `dialog open` など)=開いているダイアログ、`idle`=質問や「できなかった」の報告=人間の返事待ち、`busy`=Monitor 待ち(正常な常駐)。`team status` で赤くするのは前 2 つ
2. `state` は言い回しに左右されるので、**完了の判定に使わない**(完了は記録=handoff や board の更新で判断する)。「待て」と書くと `working` に見える
3. **`failed` は 2 種類ある**。(a) worker が起動前に落ちた(pid なし。`claude --bg` は exit 0 のまま)、(b) セッション内のエラー(モデル不正など。プロセスは生きている)。起動コマンドの終了コードを成功の根拠にせず、**起動後に `claude agents --json` で `state` と `pid` を確認する**ステップを `yamato up` に入れる
4. `stopped` は「作業中や待ち中に止められた」。シフト終了(遅延 stop、A)で止めた席は、`done` を出していれば `done` のまま残る

## yamato の起動レシピ・設計への反映(まとめ)

A/B の起動レシピと settings.json ひな形に対する変更の候補:

1. **`env -u GH_TOKEN` は外す**(効果がない。Q2)。席に見せたくない変数は、ひな形の `env` に `"GH_TOKEN": ""` のように空で入れる。厳密な UNSET が要れば SessionStart hook で `unset`(Bash 向け)。ただし `gh` はキーリングのログインにフォールバックするので、認証の隔離は別途(Q2)
2. **`"remoteControlAtStartup": false` をひな形に足すかを決める**(Q3)。足さないと、ユーザーが `remoteControlAtStartup: true` の環境では全席が常駐し、「1 時間で自然に止まる」前提が崩れる
3. **SessionStart の注入は hook 1 本あたり 10,000 文字以下**(Q1)。§8.2 の 6 種類は hook を分けるか、上限つきで切って「全文は `<path>` を Read せよ」にする。`yamato hook session-start` の側で文字数を検査する
4. **`yamato up` は起動後に `claude agents --json` で `state` と `pid` を確認する**(Q5)。`claude --bg` は worker が起動前に落ちても exit 0 を返し、失敗は `state=failed, pid なし` で後から分かる
5. **`team status`**: 生存は `pid != null`。「詰まり」は `status=waiting`(`waitingFor` を出す)と `state=blocked`(`status=idle` は質問や断念の報告=人間の返事待ち)。Monitor 席が `busy` でなくなったら「常駐が切れた」(Q4、Q5)
6. **常駐の手段**は 3 つ: zellij の attach(ターンを消費せず、RC なしで実測 OK)、Monitor(30 分ごとにモデルを呼ぶ。利用上限に弱い)、Remote Control(意図せず常駐する)。常駐の根拠は `pid` の生存で見る(Q3、Q4)

## 注意点と未検証の事項

- Q1 の閾値(10,000 文字)は 2.1.283 での観測。設定や環境変数で変えられるかは調べていない。席が長い注入を最後まで読んだか(中間の取りこぼし)も見ていない
- Q2 の「席の環境が daemon から来る」の仕組み(spare host 方式、user 設定の `env` が席に入る理由)は、ログとプロセス環境からの推定。`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` は席が `permission prompt` で止まり、結果を取れていない。`CLAUDE_ENV_FILE` の `unset` を確認したのは Bash ツールだけで、MCP サーバなどの子プロセスは見ていない。daemon の作り直し(`claude daemon stop`)は他のセッションを止めるので試していない
- Q3 の停止は haiku の idle 席(RC なし・attach なし)で 2 回(`norc` 61 分、`norc2` 60 分)確認した。停止は「最後のターン終了から約 60 分」で、定時スイープではない。zellij の疑似クライアントを繋がない場合の attach の効果、停止後に attach したときの復活は、今回は測っていない
- Q4 の Monitor 常駐は、RC ありの席で 12:20 の 3 回目が利用上限で失敗したため、「1 時間を超える連続常駐」は RC なしの `mon-norc` の 75 分(2 回の再アーム)で確認した
- Q5 の `state` の付け方(`detail` の生成元)は非公開で、モデルによる要約か規則かは未確認。`state` は言い回しで変わる(「待て」と書くと `working`)
- 私の設定ミスが 3 つあった。いずれも結果には使っていない: (1) 最初の環境変数の検証で、zsh の単語分割のため `--setting-sources project,local` が 1 引数になり、5 席が起動前に `exit 1` で落ちた(そこから `failed` の再現に使った)、(2) deny hook の引用符の誤り(Q5)、(3) 片付けの 1 回目で `stop` が空振りした(`${=VAR}` にして再実行)

## 片付け
- 作成した全セッション(58 席。起動ミスで `failed` になったものを含む)を stop + rm した。**rm は pid が消えるのを待ってから**行った(B の教訓)。`claude agents --json --all` で `spike-c` で始まるものは **0 件**。`~/.claude/jobs/` の記録も消えた
- zellij の使い捨てセッション `spike-c-view` は `kill-session` + `delete-session` した。`zellij list-sessions` に残っているのは `fleet-main` だけ
- attach プロセス、pty クライアント、状態ポーラー、待機スクリプト、Monitor の `tail -F` は、残っていない(`ps` で確認)
- 既存のセッション(fleet-leader、main-leader、他の driver の席)、`fleet-main`、`~/.claude/settings.json` には触れていない。`claude daemon stop` は実行していない
- リポジトリの変更はこのファイル(`docs/verify/verify-p0-c.md`)だけ。副作用: `~/.claude.json` に workspace trust を 1 件追加した(scratchpad の使い捨て cwd)

