# P1 の E2E — 報告

- 実施: 2026-09-26 16:03–16:31 / Claude Code 2.1.283 / macOS / 全席 `sonnet` (pm・editor も sonnet)
- 実行者: fleet の driver (task-p1-e2e)。owner の役は driver が CLI で演じた (依頼の `send`、`up`、`decide close --by owner`、`extend` / `halt` / `down`)。`talk` (attach) は使っていない
- 場所: driver の scratchpad に、使い捨ての git repo (`calc.py` と `test_calc.py`) とその bare remote、艦フォルダ 2 つ (開発艦 `e1`、調査艦 `r1`) を作った。`YAMATO_HOME` も scratchpad に向け、owner の `~/yamato/ships.json` には触れていない
- gh は本物を使わない: `$YAMATO_GH` に偽の gh (scratchpad の `bin/gh-fake`。`pr create/view/checks/merge` を JSON で持ち、merge は bare remote の main に本当に squash する) を渡した。渡し方は下の「見つかった問題」の 1
- 既存のセッション (fleet の各席、spike-c.*) と zellij のセッションには触れていない

## まとめ

| シナリオ | 結果 | 根拠 |
|---|---|---|
| 1. 開発艦: 依頼 2 件 → pm が分けて impl-1 / impl-2 → worktree → PR | ✅ | 依頼から PR 2 本まで 70 秒 (16:07:57 → 16:09:03)。各自 `worktree add` で `ship/worktrees/T-00N` に作業し、`git push` → `pr open` |
| 1. impl が `decide open --category design` → pm が閉じる → 再開 | ✅ | impl-1 が D-001 (0 除算の挙動) を開いて T-001 が blocked (16:08:24)、pm が 13 秒後に閉じ、T-001 が active に戻って impl-1 の新しいシフトが実装 |
| 1. merge の判断は owner → driver が CLI で `decide close --by owner` → pm が `pr merge` | ✅ (不具合 1 件を直して) | pm が merge の判断 D-002 / D-003 を `--urgent` で開き、通知 (command) が届いた。owner が CLI で閉じても pm に何も届かなかった (不具合 A、直した)。直したあとも idle で生きている pm は起きない (問題 C、未修正) ので、driver のセッションから SendMessage で届けた。pm は 2 本を merge し、bare remote の main に squash の commit が 2 つ |
| 1. 最終受付の注意 → 終業 → 日報 → notify | ✅ | 最終受付 (deadline の 3 分前) のあとの resume で注入に 1 回だけ注意が入り (events `last_call`)、pm から impl-1 への send の先頭に「(終了まで 3 分。片付く範囲で)」が付いた。日報は pm が書いて `report send`、command の通知がファイルに届いた。日報の稼働時間の誤り (不具合 B) を直した |
| 1. `ships` / `status` / `extend` / `halt` | ✅ | `extend e1 2m` で deadline と最終受付の時刻がそろって 2 分延びた。`halt` は動いている席が無いときに実行し、強制停止 0 件・安全網は送信済みで何もしなかった |
| 2. 調査艦: editor → researcher (headless) → fact-checker (headless) → 報告書 | ✅ (配送は driver が代行) | 問いから報告書まで 3 分 50 秒。researcher と fact-checker の終わりの報告 (定型文) は、生きている idle の editor に届かない (問題 C)。2 回とも driver が SendMessage で届けた |
| 2. dontAsk の allow で researcher が詰まらないか | ✅ 詰まらなかった | researcher・fact-checker とも拒否 0 件 (`permission_denied` なし)。使った操作は `board show` / `board note` / `log` / `seat-stop`、WebFetch 1 回、`work/` への Write |
| 2. researcher からの `send` が断られる | ✅ | researcher の sessionId を `CLAUDE_CODE_SESSION_ID` にして `send` → exit 1「席 researcher (trust: external) は send を使えない」。`--from researcher` でも同じ |
| 2. resume 後に editor の `--remote-control` が残るか | ✅ 残った | 1 回目 (pid 74491) と resume 後 (pid 86851) の `~/.claude/sessions/<pid>.json` の `bridgeSessionId` が同じ `session_013FDz…` |
| 3. memory: memo → `memory curate` (headless) → `memory apply` | ✅ | editor が 1 件、pm が 2 件 memo。`memory curate` は 1 役割 5〜6 秒・約 4k トークンで案を作り、applier の席を起こした。editor と pm が自分で `memory apply` (役割) と `--knowledge` を打った |
| 4. 入れ替え: Stop hook の促し → `seat-stop --rotate` → 次の send で新しいシフト | ✅ | pm の `rotate.context` を小さくし、(1) 止まった pm への send が「コンテキスト 80k ≥ 80k」で resume せず新しいシフト、(2) 新しいシフトの Stop hook が「46k ≥ 40k」で促し、pm が `seat-stop --rotate`、(3) 次の send (impl-1 の報告) で「入れ替えの印」により新しいシフト #6 |
| 片付け | ✅ | 作った 10 セッション (e1 ×8、r1 ×1、調べ用 ×1) を stop + rm。`claude agents --json --all` に e1 / r1 / e2eprobe の名前は 0 件。yamato の watcher・watchdog のプロセスも 0。worktree も 0 |

見つかった不具合は小さいもの 2 件を直した (A・B)。設計の判断が要るものと大きいものは直さずに下にまとめた (C〜E)。

## 手順

```bash
S=<scratchpad>; export YAMATO_HOME=$S/home
# repo と bare remote。trust は pty のスクリプトでダイアログの「Yes, I trust this folder」を選んだ (owner の手作業に当たる)
git init --bare -b main $S/e1/remote.git && git clone $S/e1/remote.git $S/e1/repo   # calc.py / test_calc.py / .gitignore を commit して push
yamato ship create e1 --path $S/e1/ship --workspace $S/e1/repo --template dev
#   team.yaml: pm を sonnet、impl の count 2、grace 5m、notify.via [command] (scratchpad のファイルに追記)、
#   settings.env に YAMATO_GH / FAKE_GH_STATE / FAKE_GH_REMOTE / YAMATO_HOME (問題 1)
yamato send e1 pm "依頼 1: calc.py に divide(a, b) … 0 で割ったときの振る舞いは公開 API の決め事なので design の判断を開いて …"
yamato send e1 pm "依頼 2: 新しいファイル text.py に slugify(s) … calc.py には触らないこと"
yamato up e1 --for 15m
yamato decide close e1 D-002 --choice "merge する" --reason "…" --by owner     # CLAUDE_CODE_SESSION_ID を外した端末から
yamato ships; yamato extend e1 2m; yamato halt e1

yamato ship create r1 --path $S/r1/ship --template research    # editor・fact-checker を sonnet、researcher の count 1
yamato send r1 editor "問い: Python 3.13 の標準の対話シェル (REPL) で新しくなった点を 2 つ、公式の出典付きで …"
yamato up r1 --for 25m
yamato memory curate r1 --wait; yamato memory curate e1 pm --wait
yamato down r1
```

## シナリオ 1: 開発艦 e1

| 時刻 | 席 | 出来事 |
|---|---|---|
| 16:07:57 | owner | 依頼 2 件を `send` → `up e1 --for 15m` (deadline 16:22:57、最終受付 3 分前) |
| 16:08:09 | pm | T-001 (divide、impl-1) と T-002 (slugify、impl-2) を作り、`branch=` を付けて割り当て。impl-1 には「0 除算は自分で決めず design の判断を開け」と書いた |
| 16:08:20 | impl-2 | `worktree add` (`yamato/e1/slugify`) |
| 16:08:24 | impl-1 | `decide open --category design --blocks T-001` → D-001 (decider pm)。T-001 は blocked |
| 16:08:36 | impl-2 | push → `pr open` → PR #1、pm に「PR を開いた」 |
| 16:08:37 | pm | D-001 を閉じた (ZeroDivisionError を素通し)。T-001 は active に戻り、impl-1 に send |
| 16:08:48–49 | impl-1 | シフト #1 は終業処理中だったので、send が止まるのを待って新しいシフト #2 を起こした (P0 の不具合 3 の対処が効いた) |
| 16:08:57 | pm | T-002 を確認して `review=approved`、merge の判断 D-002 (owner、`--urgent`) → 通知 |
| 16:09:03 | impl-1 | `worktree add` (`yamato/e1/divide`) → 実装 → PR #2 |
| 16:09:19 | pm | T-001 を承認、D-003 (owner、`--urgent`) → 通知。pm は idle で待つ |
| 16:09:51 | owner | `decide close D-002 --by owner` → **誰にも send されない** (不具合 A) |
| 16:10:20 | owner | 直したあとで D-003 を閉じた → pm の inbox に「D-003 が決まった」。ただし pm は生きているので起こさない (問題 C)。driver のセッションから SendMessage で `e1.pm` に届けた |
| 16:10:53 | pm | `pr merge` ×2 (艦のロックで 1 本ずつ) → 衝突の確認 (独立なファイルなので無し) → `worktree rm` ×2 → T-001 / T-002 done |
| 16:11:01–15 | pm | `report daily` → 「一言」「明日」を書く → `report send` (通知) → seat-stop |
| 16:12:17 | owner | `ships` → `extend e1 2m` (deadline 16:24:57、最終受付 16:21:57) |
| 16:22:02 | owner | 最終受付のあとに依頼 3 (README) を send → 止まっていた pm を resume。SessionStart の注入に「最終受付を過ぎました (艦の終了まで 3 分)」 |
| 16:22:1x | pm | T-003 を impl-1 に。send の本文は「(終了まで 3 分。片付く範囲で) T-003: …」 |
| 16:22:50 | pm | PR #3 を承認、D-004 (owner) を開いて終業。memo を 2 件残した |
| 16:23:27 | owner | `memory curate e1 pm` → 案ができて pm を resume → pm が `memory apply` (シナリオ 3) |
| 16:23:44 | owner | `down e1` (pm は直前に seat-stop 済み)。日報は送信済みなので安全網は何もしない |
| 16:24:20 | owner | D-004 を閉じてから `up e1 --for 10m` → pm は resume して PR #3 を merge (シナリオ 4 の準備) |
| 16:26:22 | owner | `halt e1` (シナリオ 4 のあと。D-005 が owner の判断待ちのまま) |

bare remote の main (merge の結果):
```
0fd1101 T-003 README.md を作る (#3)
099222e T-001 calc.py に divide(a,b) を追加 (#2)
dd379f6 T-002 text.py に slugify(s) を追加 (#1)
d2578cb 初期
```

日報 (`reports/daily/2026-09-26.md`、pm が書いた「一言」「明日」以外は yamato が埋めた):
```
# e1 日報 2026-09-26 (稼働 16:07–16:09 / 15m 枠)      ← 不具合 B (直したあとは 16:07–16:11)
## 一言
依頼 2 件 (divide / slugify) を両方 merge 済み。divide の 0 除算は D-001 で ZeroDivisionError 素通しに決定。
## owner の判断待ち (0 件)
## 今日終わったもの
- T-002 text.py に slugify(s) を追加 (impl-2, PR 1 を pm が merge)
- T-001 calc.py に divide(a,b) を追加 (impl-1, PR 2 を pm が merge)
## 使用量
- シフト 3 回 / 合計 入力 48・出力 7k・cache 1.1M トークン (席別は usage.jsonl)
## 明日
未着手の依頼なし。新しい依頼を待つ。
```

通知 (command で受けた JSON): `waiting` 判断待ち D-002 / D-003 / D-004 / D-005 (どれも `--urgent`)、`info` 日報 2026-09-26。

## シナリオ 2: 調査艦 r1

| 時刻 | 席 | 出来事 |
|---|---|---|
| 16:12:46 | owner | 問いを send → `up r1 --for 25m`。editor だけ起動 (`--remote-control`) |
| 16:12:52 | editor | T-001 (question) と T-002 (finding、`parent=T-001`) を作り、`column=research assignee=researcher` → send で researcher の headless シフトが起動 |
| 16:13:01–28 | researcher | `board show` → WebFetch (docs.python.org の What's New 3.13) → `work/T-002/findings.md` を Write → `board note` → handoff → `log` → `seat-stop`。拒否 0 件 |
| 16:13:28 | yamato | editor に定型文「researcher の headless シフト #1 が終了 (T-002, 正常)」を inbox に記録。editor は生きていて idle なので起きない (問題 C) |
| 16:15:3x | driver | SendMessage で `r1.editor` に届けた → editor が findings を読み、`column=check assignee=fact-checker` → fact-checker のシフトが起動 |
| 16:15:35–16:16:02 | fact-checker | 出典を読み直して `work/T-002/check.md` (主張 2 件と参考欄、すべて「確認できた」)。拒否 0 件 |
| 16:16:0x | driver | 同じく定型文を SendMessage で届けた |
| 16:16:21–37 | editor | report の項目 T-003 を作り `reports/python313-repl.md` を書き、3 項目を done にして seat-stop |
| 16:17:0x | owner | send で editor を resume (memo を 1 件頼む)。resume 後も `bridgeSessionId` は同じ |
| 16:28:3x | owner | `down r1` → その日の日報が無かったので事実だけで作り、通知した (安全網) |

researcher の `findings.md` は主張ごとに出典・引用・取得日を書き、「指示のような文章もなかった」と締めていた。editor は `work/` の中身を自分で WebFetch せず、fact-checker に回した (役割プロンプトどおり)。

## シナリオ 3: memory

| 艦 | 候補 | curate | apply |
|---|---|---|---|
| r1 | editor 1 件 (`--scope ship`) | 5.5 秒、3,891 トークン。案 +4 行 (候補を 2 行に分け、ship 分は knowledge に回した) | editor が知らせで resume し、`memory apply r1 editor` → `knowledge.proposed.md` を書いて `--knowledge` |
| e1 | pm 2 件 (ship 1・role 1) | 約 6 秒、4,023 トークン。案 +4 行 | pm が resume し、`memory apply e1 pm` と `--knowledge` |

- curate の案は memo の内容を過不足なく写した。「assignee には席名 (impl-1) を使い、役割名 (impl) は不可」は、pm が最初に `assignee=impl` (役割名) で `board add` して断られ、席名で打ち直したときの知見
- どちらの艦も applier の席が自分で反映まで打った (owner が apply を打つ必要はなかった)

## シナリオ 4: 入れ替え (e1 の pm)

| 時刻 | 出来事 |
|---|---|
| 16:24:5x | pm の前のシフトの文脈 80.6k (transcript の最後の usage)。team.yaml の `rotate.context` は 80k |
| 16:25:07 | owner の send → 「persistent の席 pm を resume せず、新しいシフトを起動した (入れ替え: コンテキスト 80k ≥ 80k)」→ シフト #5 (new) |
| 16:25:20 | #5 の最初のターンの終わりに Stop hook: 「入れ替えの条件 (コンテキスト 46k ≥ 40k) → seat-stop --rotate を促す」(`rotate_suggested`) |
| 16:25:28 | pm は T-004 を割り当ててから `seat-stop --rotate` (`rotate_requested`)。roster に `rotateRequested: true` |
| 16:25:42 | impl-1 の「PR を開いた」の send → 「入れ替え: 入れ替えの印 (seat-stop --rotate), コンテキスト 48k ≥ 40k」でシフト #6 (new) |
| 16:25:55 | #6 でも Stop hook が促し (45k ≥ 40k)、pm は D-005 を開いてから再び `--rotate` |

- 閾値 40k は、新しいシフトの最初のターンの終わりの文脈 (注入・役割プロンプト・1 ターン分で 45k 前後) より小さいので、シフトのたびに入れ替わった (観測 2)。ひな形の既定 (30% = 300k) ではこうならない
- `rotate.context` を team.yaml で変えても、`up` までは `.runtime/team.json` の古い値が使われる (観測 3)。E2E では生成物の値を直接書き換えた

## 使用量 (`usage.jsonl`)

e1 (開発艦、依頼 4 件・判断 5 件・merge 3 本):

| 席 | shift | 秒 | in | out | cache 書き | cache 読み | 計 |
|---|---|---|---|---|---|---|---|
| impl-1 | 1 | 32 | 12 | 2,102 | 46,597 | 212,281 | 260,992 |
| impl-2 | 1 | 45 | 22 | 2,751 | 17,070 | 470,691 | 490,534 |
| impl-1 | 2 | 38 | 14 | 2,430 | 17,835 | 294,439 | 314,718 |
| pm | 1 | 208 | 46 | 5,938 | 60,543 | 995,379 | 1,061,906 |
| impl-1 | 3 | 34 | 16 | 1,929 | 15,834 | 337,538 | 355,317 |
| pm | 2 | 61 | 16 | 2,027 | 44,198 | 407,492 | 453,733 |
| memory-curate-pm | - | 6 | 2 | 361 | 2,806 | 854 | 4,023 |
| pm | 3 | 25 | 10 | 1,179 | 6,965 | 354,401 | 362,555 |
| pm | 4 | 29 | 10 | 1,499 | 6,914 | 388,997 | 397,420 |
| pm | 5 | 30 | 10 | 1,476 | 12,825 | 218,924 | 233,235 |
| impl-1 | 4 | 47 | 16 | 2,366 | 16,311 | 334,811 | 353,504 |
| pm | 6 | 31 | 10 | 1,522 | 14,506 | 212,922 | 228,960 |
| | | | | | | | **4,516,897** |

r1 (調査艦、問い 1 件):

| 席 | shift | 秒 | in | out | cache 書き | cache 読み | 計 |
|---|---|---|---|---|---|---|---|
| researcher | 1 (headless) | 27 | 12 | 1,937 | 13,437 | 59,371 | 74,757 |
| fact-checker | 1 (headless) | 27 | 14 | 1,988 | 10,409 | 73,807 | 86,218 |
| editor | 1 | 229 | 26 | 3,448 | 24,614 | 475,711 | 503,799 |
| editor | 2 | 21 | 6 | 951 | 52,110 | 100,582 | 153,649 |
| memory-curate-editor | - | 5 | 2 | 254 | 3,635 | 0 | 3,891 |
| editor | 3 | 31 | 14 | 1,862 | 7,337 | 395,399 | 404,612 |
| | | | | | | | **1,226,926** |

全体で約 574 万トークン (ほとんどが cache 読み)。headless の席は bg の席の 1/3〜1/5 (起動時の文脈が小さく、ターンが少ない)。resume の pm (#3・#4) は 1 ターンでも 35〜40 万で、入れ替えの後の新しいシフト (#5・#6) は 23 万だった。

## 見つかった問題と対処

### 直したもの (この PR)

- **A. links だけの判断を席の外から閉じると、開いた席に何も届かない** (シナリオ 1)。merge の判断は `--links` でタスクを止めないので、`decide close` の「止まりが解けたタスクの担当に send」の対象が無い。owner が端末から閉じると、判断を開いて待っている pm は知る手段が無かった。→ `deliver_close` が、判断を開いた席 (`opened_by`) にも「D-00N が決まった: <決めた人> の決定「<決定>」。結んだ項目: …」を send する (閉じた本人と、タスクの担当として既に送った席には送らない)。テスト 2 本 (`tests/test_decide.py`)
- **B. 日報の「稼働 A–B」の終わりが、まだ動いている席を見ていない** (シナリオ 1)。pm が自分の終業前に日報を作ると、最後の `shift_end` (impl-1 の 16:09) で切れていた。→ 席ごとの最後の shift の出来事が `shift_start` の席が 1 つでもあれば、今 (と日の終わりの早い方) までにする。テスト 1 本 (`tests/test_report.py`)

### 直していないもの (設計の判断が要る・大きい)

> C・D・E は次の PR (task-e2e-fixes) で直した。末尾の「追記: C・D・E の修正の確認」を参照。

- **C. 席の外 (owner の端末・yamato の定型文・curate の知らせ) から、生きていて idle の席を起こす道具が無い**。yamato は「宛先が生きていれば送り手が SendMessage で届ける」前提で、送り手が席でないときは inbox に記録して終わる。E2E では次の 3 か所で流れが止まり、driver のセッションから SendMessage で代わりに届けた
  - e1: owner が merge の判断を閉じた知らせ (A を直したあとの inbox) が idle の pm に届かない
  - r1: researcher / fact-checker の終わりの報告 (定型文) が idle の editor に届かない。**調査艦の既定の流れ (headless → persistent の editor) は、editor が生きている限り毎回ここで止まる**。e2e-headless でも既知として書かれていた
  - 止まっている席なら send が resume するので問題にならない (curate の知らせは pm / editor が止まっていたので届いた)
  - 案: (1) deadline watcher (asyncRewake の Stop hook) が inbox の未読も見て、席の外からの未読があれば exit 2 で起こす (2) headless の報告だけでも、yamato が `claude` の CLI 経由で届ける手段を探す (3) editor は割り当てたら終業する運用にする (役割プロンプト)。どれも仕組みか運用の選択なので leader / owner の判断
- **D. bg の席は daemon の環境で動くので、`env_unset` が効いていない** (安全網の穴)。`env -u GH_TOKEN FOO_E2E=hello claude --bg …` で起こした席のプロセスの環境 (`ps eww`) には `GH_TOKEN` があり、`FOO_E2E` は無かった。yamato の `seat_env` は `claude --bg` を呼ぶ側の環境から外しているだけで、実際の席は daemon (このマシンでは GH_TOKEN を持つ環境から起動済み) の環境を受け継ぐ。README の「席は env_unset を外して起動しているので、gh は環境変数のトークンを使えず」は事実と違う。headless (`claude -p`) は呼び出し側の環境で動くので効いている
  - 一方、席の settings の `env` は席の Bash に効いた (偽の gh の `YAMATO_GH` をこれで渡した)。対処の案: `env_unset` の名前を席の settings の `env` に空文字で書き出す (gh は空の GH_TOKEN を未設定と同じに扱う。ほかの変数では空と未設定が違いうる)。安全網の中身の決め方なので直していない
- **E. 同じ日の 2 回目以降の終業で、日報が更新されない**。pm は最初の終業 (16:11) で日報を書いて送った。そのあとの依頼 3・4、D-004 / D-005 (owner の判断待ち) は日報に載らず、`down` / `halt` の安全網は「その日の日報を送信済み」なので何もしない。captain には「その日の最後のシフト」かどうかが分からない。owner の判断待ちは `--urgent` の通知で届いたので実害は小さいが、`notify.decisions: digest` の判断は日報にしか載らないので漏れうる

## 観測したこと (直していない)

1. **pm は SendMessage で内容を受け取ると `yamato inbox` を読まないことがある** (P0 と同じ)。`status` に `未読 inbox: pm=5` が出続けた
2. **`rotate.context` が起動直後の文脈より小さいと、シフトのたびに入れ替わる** (シナリオ 4 の 40k)。新しいシフトは最初のターンの終わりで 45k 前後になる。Stop hook の促しは 1 シフトに 1 回なので暴走はしないが、閾値の目安 (「起動直後の文脈 + 余裕」) を team.yaml のコメントに書くとよい
3. **team.yaml の変更は `up` まで効かない**。`send` の rotate 判定も Stop hook も `.runtime/team.json` (up のときの写し) を読む。稼働中に閾値を変えるには `up` し直す
4. **Stop hook の入れ替えの促しは、seat-stop 中のターンの終わりでは出ない**。1 ターンで仕事を終えて止まる captain には促しが出ず、次の send の送信側の規則 (前のシフトの文脈量) で新しいシフトになる。どちらかで拾われるので漏れはない
5. **`ship create` はひな形の count のまま `seats/` を作る**。count を編集したあとも `seats/impl/` (e1) や `seats/researcher-1..3/` (r1) が空のまま残る。害はない
6. **`halt` / `down` で止める席が無いときは events に何も残らない**。緊急停止を打った記録を残すなら `halt` の行を足す
7. **最終受付の注意は、最終受付のあとの最初のターン (ここでは resume の SessionStart) に 1 回だけ注入された** (`last_call` の `hook: SessionStart`)。`extend` で deadline を延ばすと `lastCallAt` も同じだけ延びた
8. **per_task の席の終業処理中に届いた send は、止まるのを待って新しいシフトを起こした** (16:08:48 → 49、P0 の不具合 3 の対処)。impl-1 は D-001 を開いて終業しかけたところで、決定の知らせを新しいシフトで受け取った
9. 偽の gh と bare remote で `pr merge` を 2 本続けて打ち、艦のロックで 1 本ずつ入った。独立なファイルなので `gh pr view` の衝突確認は MERGEABLE のまま
10. driver のセッション (Claude Code) から、席の名前 (`e1.pm`・`r1.editor`) で SendMessage が届いた。owner が自分の Claude Code から席に話しかける経路として使える
11. researcher は 27 秒・WebFetch 1 回で終えた。出典は 1 ページで足りる問いだったので、dontAsk の allow の不足で止まる操作 (curl など) を試す場面は無かった

## 片付け

- 作ったセッション: e1 の 8 件 (pm ×3、impl-1 ×4、impl-2 ×1)、r1 の editor 1 件、env の調べ用 1 件 (`e2eprobe.env`)。全て stop + rm した。headless のシフト (researcher・fact-checker・curate) は `claude -p` なので一覧に残らない
- `claude agents --json --all` に e1 / r1 / e2eprobe の名前は 0 件。scratchpad を参照するプロセスも 0
- worktree: pm が T-001〜T-003 を片付け、halt で残った T-004 は `worktree rm --force` で消した
- 副作用: scratchpad の repo (`e1/repo`) と調査艦のフォルダ (`r1/ship`) を `~/.claude.json` の workspace trust に追加した

## 追記: C・D・E の修正の確認 (2026-09-26 16:39–16:42)

- 実施: fleet の driver (task-e2e-fixes) / Claude Code 2.1.283 / 全席 `sonnet`。調査艦 `x1` (research ひな形、researcher の count 1) を 1 回だけ動かした
- 直したもの
  - **C**: Stop hook の deadline watcher (`hook wait-deadline`、asyncRewake。1 席 1 本のまま) が inbox も数秒おきに見る。**送り手が席でない未読** (owner・`yamato` の定型文など、誰も SendMessage で届けないもの) が増えたら exit 2 で席を起こし、「inbox に未読があります (N 件、<送り手> から)。`yamato inbox` で読んで対応してください」と伝える。同じ未読では 1 回だけ (`.runtime/inbox-wake-<席>.json`)。送り手が席のものは送り手が SendMessage で届けるので起こさない。終了時刻を過ぎたら終業の指示だけ
  - **D**: `env_unset` の名前を席の settings の `env` に空文字で書き出す (bg・headless・`--cwd` の席は同じ settings を使う)
  - **E**: その日の日報を作った・送ったあとに、日報に載る出来事 (board・判断・PR・異常の events。席のシフトの始まり・終わりは数えない) があれば、`report daily` と `down` / 強制停止の安全網は「一言」「明日」を残して事実の節を作り直す。送るかどうかは「最後に送ったあとに変化があるか」で決め、2 回目は件名に「(更新)」を付ける。captain の役割プロンプトは「終業のたびに日報を書き、2 回目以降は一言・明日を書き直して送り直す」に直した
  - 観測 2: ひな形の `rotate.context` のコメントに目安 (起動直後の文脈 + 余裕。sonnet で 45k 前後) を書いた

| 確認 | 結果 | 根拠 |
|---|---|---|
| D: bg の席の Bash で `GH_TOKEN` / `GITHUB_TOKEN` が効かない | ✅ | editor (bg の席) が Bash で打った結果 (`work/env-check.md`): `printenv GH_TOKEN \| wc -c` → `1` (空の値 + 改行)、`GITHUB_TOKEN` も `1`。`gh auth status` は `Logged in ... (keyring)` だけで、`(GH_TOKEN)` の行が消えた (呼び出し側の端末では `GH_TOKEN` と keyring の 2 行が出る) |
| D: gh は空の `GH_TOKEN` をどう扱うか | ✅ 未設定と同じ | 端末で `GH_TOKEN= GITHUB_TOKEN= gh auth status` → keyring の認証だけを使った。`env -u` と同じ結果 |
| D: 席の claude のプロセス自体の環境 | ⚠️ 残る | `ps eww <editor の pid>` には daemon の `GH_TOKEN=<値>` があった。settings の `env` は席のツール (Bash) に効くだけで、プロセスの環境は変えない。同じユーザーの `ps eww` で読めるので、完全に渡さないなら daemon を `GH_TOKEN` の無い環境で起動する (owner の手元の運用。README に書いた) |
| C: headless の researcher の報告 → idle の editor が起きる | ✅ | 16:40:07 researcher の定型文が editor の inbox #2 に入り、16:40:11 watcher が起こした (log「inbox watcher: 席の外からの未読 1 件 (#2 まで) → 起こす」)。editor は findings を読んで 16:40:17 に fact-checker へ回した |
| C: 2 回目 (fact-checker の報告) | ✅ | 16:40:42 定型文 → 16:40:44 起こした → editor が報告書 `reports/python313-removed.md` を書いて 3 項目を done、日報を送って 16:41:17 seat-stop。**driver からの SendMessage の代行は 0 回** (e2e-p1 のシナリオ 2 では 2 回必要だった) |
| C: 送り手が席の未読では起こさない | unit test のみ | この E2E では editor 宛ての send は定型文 2 回と owner の 1 回だけで、席からの send は無かった (`test_wait_deadline_inbox_wakes_once_and_skips_seat_senders`) |
| E: captain の送信のあとの seat-stop で送り直さない | ✅ | editor は 16:41:06 に日報を送り、16:41:17 に seat-stop (`shift_end`)。そのあとの `down --force` の安全網は送り直さなかった (シフトの終わりは変化に数えない)。2 回目の終業の更新は unit test で確かめた (`test_second_stop_of_the_day_refreshes_facts_and_sends_an_update`) |

> 注 (2026-09-27, D-003): 上の D の検証は、ひな形が `env_unset: [GH_TOKEN, GITHUB_TOKEN]` を既定で持っていた当時のもの。今はこの既定を外し、GH_TOKEN はそのまま席に渡す (gh の権限はトークンのスコープで絞る)。`env_unset` の仕組み自体 (settings の `env` に空文字で書き出す、bg の席には `env -u` が効かないなど、上の検証結果) は変わらず、艦が明示して使う道具として残る。

- 問いから報告書まで 2 分 4 秒 (16:39:13 → 16:41:17)。使用量は editor 748k (cache 読みがほとんど)
- 片付け: `down x1 --force` → editor の session を `claude rm`。`claude agents --json --all` に `x1.` の名前は 0 件。watchdog のプロセスは kill し、watcher のプロセスも 0。艦フォルダ (worktree の中の一時フォルダ) は消した。YAMATO_HOME は scratchpad に向け、owner の `~/yamato` には触れていない
