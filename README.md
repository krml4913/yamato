# yamato

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

# 2. workspace を Claude Code に trust させておく (git repo なら repo の root で。yamato は自動承認しない)
cd ~/dev/myapp && claude    # trust のダイアログで承認して終了

# 3. 依頼を送って起動する (captain の席だけが起動し、必要な席は captain が send で起こす)
./yamato send dev pm "calc.py に gcd(a, b) とテストを足して"
./yamato up dev --for 3h

# 4. 様子を見る / 終業する
./yamato status dev              # 生存 (pid)・最後に動いた時刻・waitingFor・deadline までの残り
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
| `send <ship> <seat\|owner> "<msg>" [--from <seat>]` | inbox に記録 → 宛先が生きていれば何もしない (送り手が SendMessage で届ける) / 止まった persistent は resume / per_task と未起動は新しいシフト。`--from` の席か呼び出した席の trust のプロファイルが `send: false` なら断る |
| `inbox <ship> <seat> [--all]` | 未読を全文で表示して既読にする |
| `board add\|set\|show\|list\|mine` | board の操作。frontmatter はコマンド経由でのみ変わり、値を検証する。done は `board/archive/` へ |
| `board note <ship> <item> "<text>" [--by <seat>]` | 項目の本文 (`## 経緯` の上) に追記し、経緯に 1 行残す。frontmatter (state・assignee など) は変えない。外を読む役割 (調査艦の researcher・fact-checker) が使う |
| `log <ship> <seat> "<text>"` | 席の作業ログに 1 行 |
| `worktree add <ship> <item> [--branch <b>] [--base <ref>] [--path <dir>]` | 項目の作業場所を `git worktree add` で作り、パスを出す (既にあればそのパス。何度呼んでもよい)。場所の既定は艦フォルダの `worktrees/<item>/`、ブランチは `--branch` → 項目の `branch` → `yamato/<ship>/<item>`、起点は `origin/<git.base>` (fetch してから。無ければ `<git.base>`)。項目に `worktree` と `branch` を書く |
| `worktree path <ship> <item>` / `worktree list <ship>` | 項目の worktree のパス (無ければ失敗) / 艦の worktree の一覧 (未 commit・未 push の件数つき) |
| `worktree rm <ship> <item> [--force]` | 片付ける。未 commit の変更か、どのリモートにもない commit があれば断る (`--force` で外す) |
| `pr open <ship> <item> [--title] [--body] [--draft]` | 項目の `branch` から `gh pr create` (base は `git.base`)。項目に `pr` を書き、列に `review` があれば動かし、項目の `reviewer` (無ければ hub) に send する |
| `pr merge <ship> <item>` | `git.merge_requires` を確かめて `gh pr merge --<git.strategy>`。艦ごとに 1 本ずつ (ロック)。呼び出し元を `merged_by` に残す (誰が打てるかは検査しない)。そのあと他の開いた PR の衝突を `gh pr view` で確かめ、衝突したものは列 `rebase` (あれば) に動かし、`git.conflict` の宛先に send する |
| `report daily <ship> [--date] [--facts-only] [--force]` | 日報の下書き `reports/daily/<日付>.md` を作る (LLM を使わない)。事実の節 (判断待ち・終わったもの・動いている/止まっているもの・異常・使用量) を board・events.jsonl・usage.jsonl から埋め、「一言」「明日」は captain が書く欄として空ける。60 行まで。`--facts-only` はその 2 欄を「captain が書けなかった」にしてすぐ通知する |
| `report send <ship> [--date]` | 日報の要約 (一言・判断待ち・異常、20 行まで) を `notify.via` で送る |
| `decide open <ship> --category <c> --title "<t>" [--blocks T-1,T-2] [--links T-3] [--due YYYY-MM-DD] [--body-file <f>] [--urgent] [--supersedes D-n]` | 判断の項目 `D-NNN` (`kind: decision`) を開く。decider は team.yaml の `decisions` から開いた時点で決めて固定する (表にない category は `default`、それも無ければ hub)。`--blocks` のタスクは `state: blocked` にして `blocked_on` に足す (`--links` は止めずに結ぶだけ)。decider が席なら send、owner なら owner の inbox に記録し、`notify.decisions: each` か `--urgent` のときだけ通知する (既定の `digest` は日報にまとめる)。開いた本人が decider なら送らない |
| `decide close <ship> <D> --choice "<決定>" --reason "<理由>" [--by <決めた人>]` | 項目の「## 決定」を書いて閉じ (`closed_by` = 呼び出し元、`on_behalf_of` = `--by` か呼び出し元)、`decisions/log.md` に追記する。decider 以外が閉じても断らず、events と項目に記録する。`blocked_on` が空になったタスクは元の state に戻し、担当 (無ければ hub) に send する。閉じた判断は書き換えない (覆すなら `--supersedes`) |
| `decide list <ship> [--decider <d>] [--stale 2d] [--all]` / `decide categories <ship>` | 待ちの判断の一覧 (待ち時間・期限・止めているタスクつき) / team.yaml の decisions の表 |
| `seat-stop <ship> <seat> [--delivered]` | (席が使う) handoff.md の更新を確認して遅延 stop |
| `view layout <ship>... [-o FILE]` | 艦ごとに 1 タブ、席ごとに 1 ペインの zellij layout (KDL) を出力する。艦は名前 (`ships.json` → `~/yamato/<name>`) かパス |
| `view attach <ship> <seat> [--poll SEC]` | (layout のペインの中身) 席の今のシフト (roster の sessionId) が生きていれば `claude attach`、シフトが替われば付け直す。止まっている席には attach しない |
| `run-headless <ship> <seat>` | (`send` が切り離して起動する) headless の席の 1 シフトを `claude -p` で回し、使用量・結果の判定・定型文の終了報告まで持つ。記録と結果は [docs/e2e-headless.md](docs/e2e-headless.md) |
| `hook <event> <ship> <seat>` | (Claude Code の hook から呼ばれる) session-start / user-prompt-submit / stop / wait-deadline / deny-dialog / log-denied |

team.yaml の項目: `name` / `hub` / `workspace` / `roles` (役割ごとに `model`・`shift: per_task|persistent|headless`・`count`・`inject`。headless は `max_duration`・`max_budget_usd`・`report_to` も) / `time_limit` / `grace` / `deny` / `env_unset` / `settings` (席の settings.json に重ねる) / `seat_stop` (終業前の確認) / `inject` (注入の中身と上限) / `notify` (owner 宛ての通知経路。`decisions: digest|each`) / `decisions` (判断の category → `decider` と `when`。`merge: owner` の短い書き方も可) / `board` (`kinds`・`columns`・`fields`・`archive_on_done`) / `git` (`base`・`strategy`・`merge_requires`・`merge_decision`・`conflict`。worktree と pr の道具が読む) / `report` (`daily: on_down|off`) / `watch` (`stale_after`。status / ships で赤く出す目安) / `talk_default` (talk の既定の席) / `profiles` (trust のプロファイル。下の「調査艦」)。役割には `trust` (プロファイルの名前) と `remote_control` (true なら `--remote-control` を付けて起こす。bg の席だけ) も書ける。
運用の方針 (deny の中身、外す環境変数、worktree の使い方、git の流れ) はコードに持たず、ひな形の team.yaml と役割プロンプト (`roles/<role>.md`) に書いてある。艦ごとに変えてよい。

worktree と pr (design-p1 §8): yamato は道具を出すだけで、誰がいつ使うか (タスク = ブランチ、worktree で作業する、push してよいのは自分のブランチ、merge は owner の了承のあと captain が打つ、など) は dev ひな形の `roles/*.md` と team.yaml の `deny` に書いてある。
- `pr` は `gh` を**呼び出し元の環境のまま**呼ぶ (`$YAMATO_GH` で差し替えられる)。席から呼ぶと、席は `env_unset` (ひな形の既定 `GH_TOKEN`, `GITHUB_TOKEN`) を外して起動しているので、gh は環境変数のトークンを使えず、`gh auth login` で保存した認証 (keyring / `~/.config/gh/hosts.yml`) だけで動く。保存した認証が無ければ席からの `pr open/merge` は失敗する。そのときは、owner が端末から打つ、gh に認証を保存する、艦の `env_unset` から外す、のどれかにする。`git push` は gh と別で、git の認証 (ssh / credential helper) を使う
- `merge_requires` の `review` は項目の `review=approved`、`ci` は `gh pr checks` (チェックの無い repo は通ったとみなす)、`decision` は項目を `links` に持つ `category: merge` の判断 (`decide open --category merge --links <item>`) が閉じていること
- `git.merge_decision: auto` (reviewer の承認で merge の判断を自動で開く) はまだ無い。今は captain が `decide open --category merge --links <item>` で開く (dev ひな形の `roles/pm.md`)
- 「worktree を cwd にして新しいシフトを起こす」(`send --cwd`、design-p1 §8.2 の 2) はまだ無い。今はシフトの中で `worktree add` が出したパスに `cd` する

調査艦 (`--template research`、design-p1 §7): editor (captain、persistent、`trust: clean`) + researcher ×3 と fact-checker (headless、`trust: external`)。流れと役割の決まりは `roles/*.md` にある。
- `profiles:` の中身から、yamato は席ごとの settings (`permissions.defaultMode` = `mode`、`allow`、`deny`) と役割の定義の `tools` を作るだけで、`external` / `clean` の中身は知らない。規則の中の `{{ship}}` / `{{yamato}}` / `{{seat}}` は艦フォルダ・yamato のコマンド・席の名前に置き換わる。コードが強制するのは `send: false` の席からの `send` を断ることだけ
- ひな形の既定値: `external` は `mode: dontAsk` + allow を yamato の決まったコマンド (inbox・board mine/show/list/note・log・memo・seat-stop) と `work/`・自分の handoff.md の書き込みだけ + `tools` の制限 + `send: false`。auto にすると allow で絞っても他の Bash が止まらない ([verify-p1-d](docs/verify-p1-d.md) V7)。dontAsk は allow に無い操作を確認なしで拒否するので、役割プロンプトに書いたコマンドが allow で通ることを `tests/test_research.py` で確かめている。`clean` は Web (WebFetch / WebSearch) を deny する
- `decisions.publish` (成果を艦の外に出す) の decider の既定は owner
- Haiku の警告は auto で動く席だけに出す (dontAsk の席は Haiku でも動く、V7)
- Remote Control: どちらのひな形も `settings.remoteControlAtStartup: false` で全席を切り、captain の役割だけ `remote_control: true` (V10)

艦フォルダ: `team.yaml`・`charter.md`・`knowledge.md`・`roles/<role>.md`・`board/{items,archive}/`・`seats/<seat>/` (`handoff.md`・`log/<date>.md`・`inbox.jsonl`・`inbox.cursor`・`memory.md`・`memory-inbox.md`)・`roster.json` (席ごとの今のシフトと `lastActive`)・`usage.jsonl` (シフトごとの使用量)・`worktrees/<item>/` (`yamato worktree add` の既定の場所)・`events.jsonl` (艦の出来事の追記ログ。形式は [docs/events.md](docs/events.md))・`reports/daily/<日付>.md` (日報)・`decisions/log.md` (閉じた判断の追記のみの記録。起動時には読まない)・`.runtime/` (生成物)。

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
- 日報 (design-p1 §2): `report.daily: on_down` (ひな形の既定) の艦では、`down`・強制停止のときにその日の日報が無ければ事実だけで作り、まだ送っていなければ要約を送る (captain が落ちていても owner に届く安全網)。captain が終業時に「一言」「明日」を書いて `report send` する流れは `roles/pm.md` に書いてある。captain の注入には `inject` の `last_report` で前回の日報の「一言」「owner の判断待ち」「明日」だけが入る
- `PushNotification` は方式にしない (席の外から出せない。[verify-p1-d](docs/verify-p1-d.md) V11)

テスト: `python3 -m unittest discover`。E2E の手順と結果は [docs/e2e-p0.md](docs/e2e-p0.md)。

## docs
- [design.md](docs/design.md) — 設計書
- [research-claude-primitives.md](docs/research-claude-primitives.md) — Claude Code の仕組みの調査 (2.1.282)
- [spike-zellij-attach.md](docs/spike-zellij-attach.md) — zellij 表示層の検証。実装と使い方は [src/yamato/view/README.md](src/yamato/view/README.md)
- [review-da-v0.md](docs/review-da-v0.md) — 設計書 v0 への devil's advocate レビュー
- [verify-p0-a.md](docs/verify-p0-a.md) / [verify-p0-b.md](docs/verify-p0-b.md) — P0 の実機検証 (起動レシピ)
- [e2e-p0.md](docs/e2e-p0.md) — P0 実装の E2E
- [events.md](docs/events.md) — events.jsonl の行の形式と読み方、lastActive
- [e2e-headless.md](docs/e2e-headless.md) — `shift: headless` と `run-headless` の実機確認
