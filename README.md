# yamato

> 初めての人 (エージェントも) は [docs/handoff.md](docs/handoff.md) から読む (今の状態・読む順番・dogfooding の始め方)。

役割の違う複数の AI エージェントが「艦 (ship)」として協調して仕事を進める、常設チームのためのシステム。
Claude Code の background session の上に薄く乗る。agent-fleet の後継。

- 艦 (ship): チーム。役割の構成は `team.yaml` で自由に定義する
- captain: 艦の司令塔 (仕事を分けて割り振り、回収する)
- admiral: 窓口。艦の出撃と帰投、全艦の一望
- owner: 人間

状態: P0 (記録・席の起動・send・シフトの終わり・時間の上限) と、zellij の表示層 (`yamato view`)。設計書は [docs/design.md](docs/design.md) (冒頭 §0 が最新の決定)。

## 使い方 (P0)

必要なもの: Python 3.11+、Claude Code (`claude`、2.1.283 で確認)。pip install は不要 (PyYAML は `vendor/` に同梱)。

```bash
# 1. 艦を作る (既定の場所は ~/yamato/<name>/。--path で変更可)
./yamato ship create dev --workspace ~/dev/myapp --template dev
#    → team.yaml (役割・model・shift・count・time_limit・grace・deny)、charter.md、roles/<role>.md を確認・編集する

# 2. workspace を Claude Code に trust させておく (git repo なら repo の root で。yamato は自動承認しない。
#    ~/.claude.json から確かめられないときは警告して起動し、claude が断れば手順を出して止まる)
cd ~/dev/myapp && claude    # trust のダイアログで承認して終了

# 3. 依頼を送って起動する (captain の席だけが起動し、必要な席は captain が send で起こす)
./yamato send dev pm "calc.py に gcd(a, b) とテストを足して"
./yamato up dev --for 3h

# 4. 様子を見る / 終業する
./yamato status dev              # 生存 (pid)・status / state・最後に動いた時刻・waitingFor・deadline までの残り
./yamato board list dev --all
./yamato down dev                # 終業を指示 (席は引き継ぎを書いて止まる。猶予を過ぎたら強制停止)
./yamato down dev --force        # 今すぐ止める (roster に「引き継ぎなしで終了」)

# 5. zellij で席を覗く (艦ごとに 1 タブ、席ごとに 1 ペイン。窓を閉じても席は動き続ける)
./yamato view layout dev -o ~/yamato/view.kdl
zellij --session yamato-view --new-session-with-layout ~/yamato/view.kdl
```

| コマンド | 内容 |
|---|---|
| `ship create <name> [--workspace <path>] [--path <dir>] [--template dev\|research]` | ひな形から艦フォルダを作る。`dev` (開発艦) は `--workspace` (作業対象の repo) が要る。`research` (調査艦) は repo なしで、艦フォルダ自身が席の作業ディレクトリ (下の「調査艦」) |
| `up <ship> [--for 3h] [--seats <seat,...>]` | `.runtime/` を作り直し、deadline を書き、captain の席を起動 (persistent なら resume)。`--seats` の席も一緒に起こす |
| `down <ship> [--force]` | 終業 / 強制停止 |
| `status [<ship>]` | 席ごとの状態。赤い席は `!!! <席>: ...` と出る (権限の確認待ち・API エラー (`state: failed`)・生きているのに `watch.stale_after` (既定 20m) より長く動いていない) |
| `ships` | (admiral) 全艦を 1 行ずつ: 稼働中か・残り時間・captain の最終・赤い席の数・owner の判断待ちの数・今日の使用量・最新の日報の日付 |
| `extend <ship> <期間>` | (admiral) deadline を延ばす (データの書き換えだけ。過ぎていれば今から数える) |
| `halt <ship>` | (admiral) 緊急停止。猶予なしで全席を強制停止し、日報の安全網を通す |
| `talk <ship> [<seat>]` | (admiral) 席に `claude attach` する (既定は team.yaml の `talk_default`、省略時 hub)。止まっている席は send と同じ規則で起こしてから。headless の席は attach できないので断る (send で頼む)。使い方は [docs/admiral.md](docs/admiral.md) |
| `send <ship> <seat\|owner> "<msg>" [--from <seat>] [--cwd <path>]` | inbox に記録 → 宛先が生きていれば何もしない (送り手が席なら SendMessage で届ける。送り手が席でない (owner・yamato の定型文など) ときは、宛先の席の Stop hook の watcher が数秒で idle の席を起こす) / 止まった persistent は resume。ただし役割の `rotate:` の条件 (入れ替えの印・前のシフトの文脈量・止まってからの時間・日付の変わり目) に当たれば resume せず新しいシフト (design-p1 §5.3) / per_task と未起動は新しいシフト。`--cwd` は次のシフトをその dir (項目の worktree など) で `bgIsolation: none` で起動する (per_task / headless。main repo が trust 済みなら worktree は trust 不要)。同じ送り手→宛先の送りすぎ・同じ本文の連続は events に残して警告するだけ (`watch.spin`)。最終受付のあとの captain の send は本文の先頭に「(終了まで X 分。片付く範囲で)」が付く。`--from` の席か呼び出した席の trust のプロファイルが `send: false` なら断る |
| `inbox <ship> <seat> [--all]` | 未読を全文で表示して既読にする |
| `board add\|set\|show\|list\|mine` | board の操作。frontmatter はコマンド経由でのみ変わり、値を検証する。done は `board/archive/` へ |
| `board note <ship> <item> "<text>" [--by <seat>]` | 項目の本文 (`## 経緯` の上) に追記し、経緯に 1 行残す。frontmatter (state・assignee など) は変えない。外を読む役割 (調査艦の researcher・fact-checker) が使う |
| `log <ship> <seat> "<text>"` | 席の作業ログに 1 行 |
| `worktree add <ship> <item> [--branch <b>] [--base <ref>] [--path <dir>]` | 項目の作業場所を `git worktree add` で作り、パスを出す (既にあればそのパス。何度呼んでもよい)。場所の既定は艦フォルダの `worktrees/<item>/`、ブランチは `--branch` → 項目の `branch` → `yamato/<ship>/<item>`、起点は `origin/<git.base>` (fetch してから。無ければ `<git.base>`)。項目に `worktree` と `branch` を書く |
| `worktree path <ship> <item>` / `worktree list <ship>` | 項目の worktree のパス (無ければ失敗) / 艦の worktree の一覧 (未 commit・未 push の件数つき) |
| `worktree rm <ship> <item> [--force]` | 片付ける。未 commit の変更か、どのリモートにもない commit があれば断る (`--force` で外す) |
| `pr open <ship> <item> [--title] [--body] [--draft]` | 項目の `branch` から `gh pr create` (base は `git.base`)。項目に `pr` を書き、列に `review` があれば動かし、項目の `reviewer` (無ければ hub) に send する |
| `pr merge <ship> <item>` | `git.merge_requires` を確かめて `gh pr merge --<git.strategy>`。艦ごとに 1 本ずつ (ロック)。呼び出し元を `merged_by` に残す (誰が打てるかは検査しない)。そのあと他の開いた PR の衝突を `gh pr view` で確かめ、衝突したものは列 `rebase` (あれば) に動かし、`git.conflict` の宛先に send する |
| `report daily <ship> [--date] [--facts-only] [--force]` | 日報の下書き `reports/daily/<日付>.md` を作る (LLM を使わない)。事実の節 (判断待ち・終わったもの・動いている/止まっているもの・異常・使用量) を board・events.jsonl・usage.jsonl から埋め、「一言」「明日」は captain が書く欄として空ける。60 行まで。`--facts-only` はその 2 欄を「captain が書けなかった」にしてすぐ通知する。その日の日報がすでにあれば、「一言」「明日」を残して事実の節だけを作り直す (最後に送ったあと日報に載る出来事が無ければ何もしない。一から作り直すのは `--force`) |
| `report send <ship> [--date]` | 日報の要約 (一言・判断待ち・異常、20 行まで) を `notify.via` で送る。同じ日に送るのが 2 回目以降なら件名に「(更新)」を付ける |
| `decide open <ship> --category <c> --title "<t>" [--blocks T-1,T-2] [--links T-3] [--due YYYY-MM-DD] [--body-file <f>] [--urgent] [--supersedes D-n]` | 判断の項目 `D-NNN` (`kind: decision`) を開く。decider は team.yaml の `decisions` から開いた時点で決めて固定する (表にない category は `default`、それも無ければ hub)。`--blocks` のタスクは `state: blocked` にして `blocked_on` に足す (`--links` は止めずに結ぶだけ)。decider が席なら send、owner なら owner の inbox に記録し、`notify.decisions: each` か `--urgent` のときだけ通知する (既定の `digest` は日報にまとめる)。開いた本人が decider なら送らない |
| `decide close <ship> <D> --choice "<決定>" --reason "<理由>" [--by <決めた人>]` | 項目の「## 決定」を書いて閉じ (`closed_by` = 呼び出し元、`on_behalf_of` = `--by` か呼び出し元)、`decisions/log.md` に追記する。decider 以外が閉じても断らず、events と項目に記録する。`blocked_on` が空になったタスクは元の state に戻し、担当 (無ければ hub) に send する。判断を開いた席にも (閉じた本人でなければ) 決定を send する (`--links` だけの merge の判断を owner が閉じたとき、待っている captain に届く)。閉じた判断は書き換えない (覆すなら `--supersedes`) |
| `decide list <ship> [--decider <d>] [--stale 2d] [--all]` / `decide categories <ship>` | 待ちの判断の一覧 (待ち時間・期限・止めているタスクつき) / team.yaml の decisions の表 |
| `seat-stop <ship> <seat> [--delivered] [--rotate]` | (席が使う) handoff.md の更新を確認して遅延 stop。`--rotate` は入れ替えの印を立てる (次の send で新しいシフト。その場では起こさない)。persistent の席の Stop hook は `rotate:` の条件 (文脈量・compaction・シフトの長さ) に当たると一度だけこれを促す |
| `rotate <ship> <seat>... \| --all [--by <人>]` | 止まっている persistent の席に、`seat-stop --rotate` と同じ入れ替えの印を外から立てる (T-024。チーム構成を変えたときなど、席の中に入らず次の send を新しいシフトにする)。`--all` は persistent の席すべて。生きている席・per_task・headless の席には立てず、理由を返す (生きている席は `seat-stop --rotate` を促すか、止まってから実行する)。events に残す (`by` は既定で呼び出し元) |
| `view layout <ship>... [-o FILE]` | 艦ごとに 1 タブ、席ごとに 1 ペインの zellij layout (KDL) を出力する。艦は名前 (`ships.json` → `~/yamato/<name>`) かパス |
| `view attach <ship> <seat> [--poll SEC]` | (layout のペインの中身) 席の今のシフト (roster の sessionId) が生きていれば `claude attach`、シフトが替われば付け直す。止まっている席には attach しない |
| `memo "<本文>" [--item T-1] [--scope role\|ship] [--ship <ship>] [--seat <seat>]` | memory の候補を 1 行、呼び出した席 (`$CLAUDE_CODE_SESSION_ID` の席) の `seats/<seat>/memory-inbox.md` に足す。席の外からは `--ship` と `--seat` で書く。席の中で `--seat` が違えば断る |
| `memory status <ship>` | 役割ごとの候補数・前回の棚卸し (`memory apply`) からの日数・案の有無・上限超え。`memory.applier` の役割の席には、その日の最初の起動時に注入される |
| `memory curate <ship> [<role>] [--wait]` | 役割ごとに棚卸しの headless シフト (`claude -p` 1 回。役割プロンプトは `roles/_memory-curator.md`、dontAsk・hooks なし) を切り離して走らせ、答えから `roles/<role>/memory.proposed.md` を作る。memory.md は変えない。終わると applier の席に定型文で知らせる。役割を省くと候補のある役割すべて |
| `memory apply <ship> <role> [--by <人>]` / `memory apply <ship> --knowledge` | 案を `roles/<role>/memory.md` (`--knowledge` は `knowledge.proposed.md` を `knowledge.md` に。このときはその時点の `knowledge-inbox.md` の候補を全部 `knowledge-inbox.done/` へ移す) に反映する。`memory.limits` を超える案は断る。呼び出し元は検査せず events と archive に残す。処理した候補は `memory-inbox.done/<日付>.md`、外れた行は `memory-archive.md`、knowledge の候補と `(ship)` の memo は `knowledge-inbox.md` へ |
| `memory migrate <ship>` | P0 の `seats/<seat>/memory.md` を `roles/<role>/memory.md` に追記で移す (元は `memory.md.migrated` に残す)。SessionStart の注入も読む前に同じことをする |
| `run-headless <ship> <seat>` | (`send` が切り離して起動する) headless の席の 1 シフトを `claude -p` で回し、使用量・結果の判定・定型文の終了報告まで持つ。記録と結果は [docs/e2e/e2e-headless.md](docs/e2e/e2e-headless.md) |
| `hook <event> <ship> <seat>` | (Claude Code の hook から呼ばれる) session-start (記録の注入) / session-start-knowledge (役割の memory と knowledge.md の注入) / user-prompt-submit / stop / wait-deadline / deny-dialog / log-denied |

team.yaml の項目: `name` / `hub` / `workspace` / `roles` (役割ごとに `model`・`shift: per_task|persistent|headless`・`count`・`inject`。headless は `max_duration`・`max_budget_usd`・`report_to` も) / `time_limit` / `grace` / `deny` / `env_unset` / `settings` (席の settings.json に重ねる) / `seat_stop` (終業前の確認) / `inject` (注入の中身と上限。`total_chars` は SessionStart hook 1 本あたり) / `notify` (owner 宛ての通知経路。`decisions: digest|each`) / `decisions` (判断の category → `decider` と `when`。`merge: owner` の短い書き方も可) / `board` (`kinds`・`columns`・`fields`・`archive_on_done`) / `git` (`base`・`strategy`・`merge_requires`・`merge_decision`・`conflict`。worktree と pr の道具が読む) / `report` (`daily: on_down|off`) / `watch` (`stale_after`。status / ships で赤く出す目安) / `talk_default` (talk の既定の席) / `profiles` (trust のプロファイル。下の「調査艦」)。役割には `trust` (プロファイルの名前) と `remote_control` (true なら `--remote-control` を付けて起こす。bg の席だけ) も書ける / `memory` (`applier`・`curate_every`・`curate_at`・`max_duration`・`limits`。棚卸し。`limits` は `memory_lines`・`memory_chars`・`knowledge_lines`・`knowledge_chars` で、`memory apply` の上限と注入で切る上限を兼ねる)。
運用の方針 (deny の中身、外す環境変数、worktree の使い方、git の流れ) はコードに持たず、ひな形の team.yaml と役割プロンプト (`roles/<role>.md`) に書いてある。艦ごとに変えてよい。

worktree と pr (design-p1 §8): yamato は道具を出すだけで、誰がいつ使うか (タスク = ブランチ、worktree で作業する、push してよいのは自分のブランチ、merge は owner の了承のあと captain が打つ、など) は dev ひな形の `roles/*.md` と team.yaml の `deny` に書いてある。
- `pr` は `gh` を**呼び出し元の環境のまま**呼ぶ (`$YAMATO_GH` で差し替えられる)。`env_unset` の既定は空で、席には `GH_TOKEN` / `GITHUB_TOKEN` をそのまま渡す (D-003)。gh にどこまでやらせるかは、その token 自体のスコープ (fine-grained personal access token など) で絞る。gh は `gh auth login` で保存した認証 (keyring / `~/.config/gh/hosts.yml`) や環境変数のトークンで動く。特定の環境変数を席から見せたくない艦だけが `env_unset` に列挙する。bg の席は Claude Code の daemon の環境で動くので、呼ぶ側の `env -u` だけでは外れず、yamato は同じ名前を席の settings の `env` に空文字で書いて起動する ([e2e-p1](docs/e2e/e2e-p1.md) の D)。席の Bash ではこれらが空になり、gh は空のトークンを未設定と同じに扱う。ただし席の claude のプロセス自体の環境には daemon の値が残るので、同じユーザーの `ps eww` で読める。完全に渡さないなら daemon をその環境変数の無い環境で起動する。席に環境変数を渡すときは team.yaml の `settings.env` を使う (席の Bash に効く)。保存した認証が無ければ席からの `pr open/merge` は失敗する。そのときは、owner が端末から打つか、gh に認証を保存する。`git push` は gh と別で、git の認証 (ssh / credential helper) を使う
- `merge_requires` の `review` は項目の `review=approved`、`ci` は `gh pr checks` (チェックの無い repo は通ったとみなす)、`decision` は項目を `links` に持つ `category: merge` の判断 (`decide open --category merge --links <item>`) が閉じていること
- `git.merge_decision: auto` (reviewer の承認で merge の判断を自動で開く) はまだ無い。今は captain が `decide open --category merge --links <item>` で開く (dev ひな形の `roles/pm.md`)
- 「worktree を cwd にして新しいシフトを起こす」(`send --cwd`、design-p1 §8.2 の 2) はまだ無い。今はシフトの中で `worktree add` が出したパスに `cd` する

調査艦 (`--template research`、design-p1 §7): editor (captain、persistent、`trust: clean`) + researcher ×3 と fact-checker (headless、`trust: external`)。流れと役割の決まりは `roles/*.md` にある。
- `profiles:` の中身から、yamato は席ごとの settings (`permissions.defaultMode` = `mode`、`allow`、`deny`) と役割の定義の `tools` を作るだけで、`external` / `clean` の中身は知らない。規則の中の `{{ship}}` / `{{yamato}}` / `{{seat}}` は艦フォルダ・yamato のコマンド・席の名前に置き換わる。コードが強制するのは `send: false` の席からの `send` を断ることだけ
- ひな形の既定値: `external` は `mode: dontAsk` + allow を yamato の決まったコマンド (inbox・board mine/show/list/note・log・memo・seat-stop) と `work/`・自分の handoff.md の書き込みだけ + `tools` の制限 + `send: false`。auto にすると allow で絞っても他の Bash が止まらない ([verify-p1-d](docs/verify/verify-p1-d.md) V7)。dontAsk は allow に無い操作を確認なしで拒否するので、役割プロンプトに書いたコマンドが allow で通ることを `tests/test_research.py` で確かめている。`clean` は Web (WebFetch / WebSearch) を deny する
- `decisions.publish` (成果を艦の外に出す) の decider の既定は owner
- Haiku の警告は auto で動く席だけに出す (dontAsk の席は Haiku でも動く、V7)
- Remote Control: どちらのひな形も `settings.remoteControlAtStartup: false` で全席を切り、captain の役割だけ `remote_control: true` (V10)

艦フォルダ: `team.yaml`・`charter.md`・`knowledge.md` (と `knowledge-inbox.md`・`knowledge.proposed.md`・`knowledge-archive.md`)・`roles/<role>.md`・`roles/<role>/` (`memory.md` = 役割の memory、`memory.proposed.md`・`memory-archive.md`・`curate/`)・`board/{items,archive}/`・`seats/<seat>/` (`handoff.md`・`log/<date>.md`・`inbox.jsonl`・`inbox.cursor`・`memory-inbox.md`・`memory-inbox.done/`)・`roster.json` (席ごとの今のシフトと `lastActive`)・`usage.jsonl` (シフトごとの使用量)・`worktrees/<item>/` (`yamato worktree add` の既定の場所)・`events.jsonl` (艦の出来事の追記ログ。形式は [docs/events.md](docs/events.md))・`reports/daily/<日付>.md` (日報)・`decisions/log.md` (閉じた判断の追記のみの記録。起動時には読まない)・`.runtime/` (生成物)。

## 通知 (notify)

owner 宛ての `send` は inbox に記録したうえで、`team.yaml` の `notify.via` に並べた方式すべてに通知する (省略か `[]` なら通知せず inbox にだけ残る)。件名・本文・重要度 (`success` / `waiting` / `progress` / `error` / `info`) を全方式に同じ形で渡す。

```yaml
notify:
  via: [slack, mac]        # slack / mac / windows / command (複数可)
  slack:
    webhook_env: YAMATO_SLACK_WEBHOOK   # incoming webhook の URL が入った環境変数の名前。URL は艦フォルダに書かない
  command: "mail-me"       # via に command を並べたときだけ実行する
```

| 方式 | 中身 |
|---|---|
| `slack` | incoming webhook に POST。重要度で色と絵文字が付く。環境変数が空・URL が http(s) でないときは失敗 |
| `mac` | `osascript` の `display notification` (macOS 以外では送らない) |
| `windows` | PowerShell の toast (Windows 以外では送らない)。クリックで開く先はまだ無い |
| `command` | `notify.command` をシェルで実行。件名・本文・重要度は stdin の JSON `{"title", "message", "level"}` と環境変数 `YAMATO_TITLE` / `YAMATO_MESSAGE` / `YAMATO_LEVEL`。メールなど上の 3 つ以外はこれで送る (方式は増やさない) |

- best-effort: どれかが失敗しても他は送り、`send` は失敗にならない。失敗は `send` の出力に `通知 <方式>: 失敗 (...)` と出て、`events.jsonl` に `notify_failed` として残る ([docs/events.md](docs/events.md))。webhook の URL は出力にも events にも書かない
- 注入 (design §8.2、design-p1 §3.5): SessionStart hook を 2 本に分ける。記録 (handoff・作業ログ・担当・日報・inbox・注記) の hook と、役割の memory と knowledge.md の hook。Claude Code は hook 1 本の出力を 10,000 文字まで受け取る ([verify-p0-c](docs/verify/verify-p0-c.md) Q1) ので、それぞれ `inject.limits.total_chars` (既定 9,500 文字) で切る。memory と knowledge は `memory.limits` (既定 memory 80 行 / 4,000 文字、knowledge 120 行 / 5,000 文字) で切る。切ったところには「全文は `<path>` を Read せよ」が付く
- 起動の確かめ (design-p1 §5.1): `claude --bg` は worker が起動前に落ちても exit 0 を返す (verify-p0-c Q5)。`up` と `send` は起動・resume のあとに `claude agents --json` で pid を確かめ、`state: failed` や pid なしなら失敗としてエラーを返す (roster の `launchFailed`、events の `launch_failed`)。`status` は `status: waiting` (`waitingFor` を出す) と、idle の `state: blocked` (人間の返事待ちの疑い) を赤く出す
- 日報 (design-p1 §2): `report.daily: on_down` (ひな形の既定) の艦では、`down`・強制停止のときにその日の日報が無ければ事実だけで作り、日報を作った・送ったあとに日報に載る出来事 (board・判断・PR・異常の events) があれば「一言」「明日」を残して事実の節を作り直し、まだ送っていないか、最後に送ったあとに変化があれば要約を送る (2 回目は件名に「(更新)」。captain が落ちていても owner に届く安全網。同じ日の 2 回目の終業にも効く)。captain が終業時に「一言」「明日」を書いて `report send` する流れは `roles/pm.md` に書いてある。captain の注入には `inject` の `last_report` で前回の日報の「一言」「owner の判断待ち」「明日」だけが入る
- `PushNotification` は方式にしない (席の外から出せない。[verify-p1-d](docs/verify/verify-p1-d.md) V11)

## テストの実行

```sh
python3 -m unittest discover                 # 全部 (これが正。目安 10 秒程度、約 400 件)
python3 -m unittest discover --durations 10  # 遅いテストの上位 10 件も出す
python3 -m tests.parallel                    # モジュールごとにプロセスを分けて並列に流す (目安 4〜5 秒)
python3 -m unittest tests.test_seat          # 1 モジュールだけ
```

unit test は速く保つ (遅いと開発の速さにそのまま響く)。全体で 10 秒程度、1 本 0.5 秒を超えたら理由があるものだけ。

- 新しいテストを足したら `--durations 10` の上位を見て、自分の足したテストが上がってきていないか確かめる (レビューでも見る)。表示は 1 本ずつの実時間で、setUpClass の時間は入らない
- 時間切れ・猶予・poll 間隔は実時間で待たない。モジュール定数 (`headless.POLL`・`headless.IDLE_POLL`・`seat.WATCHDOG_POLL`・`seat.WATCHDOG_MIN_SLEEP`・`memory.WATCH_POLL` など) を `mock.patch.object` で縮めるか、時計・関数を差し替える。待つなら固定の sleep より「条件が成り立つまで短い間隔で見る」
- 偽の claude (`tests/fake_claude.py`) は `ShipTestCase` では同じプロセスの中で動く (`tests/helpers.py` の `patch_fast`)。`-p` だけは本物のプロセス。本物のプロセスとして呼ぶのを確かめたいときは `real_claude_process = True`
- git を使うテストは `tests/test_worktree_pr.py` の `GitShipTestCase` のように、一時 repo を setUpClass で 1 回作ってテストごとにコピーする。git の起動 1 回がおよそ 10 ms、push は 40 ms かかる
- 本物のプロセス・本物の claude で確かめることは unit test ではなく E2E ([docs/e2e/e2e-p0.md](docs/e2e/e2e-p0.md)、[docs/e2e/e2e-p1.md](docs/e2e/e2e-p1.md)) でやる

## docs
初めての人はまず [docs/handoff.md](docs/handoff.md) (読む順番はそこの §2)。

- [design.md](docs/design.md) — 設計書
- [design-p1.md](docs/design-p1.md) — P1 の詳細設計
- [admiral.md](docs/admiral.md) — admiral を務めるときの約束とコマンド
- [events.md](docs/events.md) — events.jsonl の行の形式と読み方、lastActive
- [dogfood-knowledge.md](docs/dogfood-knowledge.md) — dogfooding の艦の knowledge.md のひな形
- [verify/](docs/verify/) — Claude Code の挙動の実機検証 (起動レシピの根拠): verify-p0-a / b / c、verify-p1-d
- [verify-win-plan](docs/verify/verify-win-plan.md) — Windows + Git Bash の実機検証 (W0) の手順書。道具は `tools/verify_win/`
- [e2e/](docs/e2e/) — 本物の claude で通した E2E の記録: e2e-p0 / e2e-p1 / e2e-headless / e2e-time-limit
- [_archive/](docs/_archive/) — 初期の検討の記録 (正本ではない)。中身は [_archive/README.md](docs/_archive/README.md)
