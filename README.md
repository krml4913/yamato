# yamato

役割の違う複数の AI エージェントが「艦 (ship)」として協調して仕事を進める、常設チームのためのシステム。
Claude Code の background session の上に薄く乗る。agent-fleet の後継。

- 艦 (ship): チーム。役割の構成は `team.yaml` で自由に定義する
- captain: 艦の司令塔 (仕事を分けて割り振り、回収する)
- admiral: 窓口。艦の出撃と帰投、全艦の一望
- owner: 人間

状態: P0 (記録・席の起動・send・シフトの終わり・時間の上限)。設計書は [docs/design.md](docs/design.md) (冒頭 §0 が最新の決定)。

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
```

| コマンド | 内容 |
|---|---|
| `ship create <name> --workspace <path> [--path <dir>] [--template dev]` | ひな形から艦フォルダを作る |
| `up <ship> [--for 3h]` | `.runtime/` を作り直し、deadline を書き、captain の席を起動 (persistent なら resume) |
| `down <ship> [--force]` | 終業 / 強制停止 |
| `status [<ship>]` | 席ごとの状態。権限の確認で止まっている席は `!!! 詰まり` と出る |
| `send <ship> <seat\|owner> "<msg>" [--from <seat>]` | inbox に記録 → 宛先が生きていれば何もしない (送り手が SendMessage で届ける) / 止まった persistent は resume / per_task と未起動は新しいシフト |
| `inbox <ship> <seat> [--all]` | 未読を全文で表示して既読にする |
| `board add\|set\|show\|list\|mine` | board の操作。frontmatter はコマンド経由でのみ変わり、値を検証する。done は `board/archive/` へ |
| `log <ship> <seat> "<text>"` | 席の作業ログに 1 行 |
| `worktree add <ship> <item> [--branch <b>] [--base <ref>] [--path <dir>]` | 項目の作業場所を `git worktree add` で作り、パスを出す (既にあればそのパス。何度呼んでもよい)。場所の既定は艦フォルダの `worktrees/<item>/`、ブランチは `--branch` → 項目の `branch` → `yamato/<ship>/<item>`、起点は `origin/<git.base>` (fetch してから。無ければ `<git.base>`)。項目に `worktree` と `branch` を書く |
| `worktree path <ship> <item>` / `worktree list <ship>` | 項目の worktree のパス (無ければ失敗) / 艦の worktree の一覧 (未 commit・未 push の件数つき) |
| `worktree rm <ship> <item> [--force]` | 片付ける。未 commit の変更か、どのリモートにもない commit があれば断る (`--force` で外す) |
| `pr open <ship> <item> [--title] [--body] [--draft]` | 項目の `branch` から `gh pr create` (base は `git.base`)。項目に `pr` を書き、列に `review` があれば動かし、項目の `reviewer` (無ければ hub) に send する |
| `pr merge <ship> <item>` | `git.merge_requires` を確かめて `gh pr merge --<git.strategy>`。艦ごとに 1 本ずつ (ロック)。呼び出し元を `merged_by` に残す (誰が打てるかは検査しない)。そのあと他の開いた PR の衝突を `gh pr view` で確かめ、衝突したものは列 `rebase` (あれば) に動かし、`git.conflict` の宛先に send する |
| `seat-stop <ship> <seat> [--delivered]` | (席が使う) handoff.md の更新を確認して遅延 stop |
| `hook <event> <ship> <seat>` | (Claude Code の hook から呼ばれる) session-start / user-prompt-submit / stop / wait-deadline / deny-dialog / log-denied |

team.yaml の項目: `name` / `hub` / `workspace` / `roles` (役割ごとに `model`・`shift: per_task|persistent`・`count`・`inject`) / `time_limit` / `grace` / `deny` / `env_unset` / `settings` (席の settings.json に重ねる) / `seat_stop` (終業前の確認) / `inject` (注入の中身と上限) / `notify` (owner 宛ての通知経路) / `board` (`kinds`・`columns`・`fields`・`archive_on_done`) / `git` (`base`・`strategy`・`merge_requires`・`merge_decision`・`conflict`。worktree と pr の道具が読む)。
運用の方針 (deny の中身、外す環境変数、worktree の使い方、git の流れ) はコードに持たず、ひな形の team.yaml と役割プロンプト (`roles/<role>.md`) に書いてある。艦ごとに変えてよい。

worktree と pr (design-p1 §8): yamato は道具を出すだけで、誰がいつ使うか (タスク = ブランチ、worktree で作業する、push してよいのは自分のブランチ、merge は owner の了承のあと captain が打つ、など) は dev ひな形の `roles/*.md` と team.yaml の `deny` に書いてある。
- `pr` は `gh` を**呼び出し元の環境のまま**呼ぶ (`$YAMATO_GH` で差し替えられる)。席から呼ぶと、席は `env_unset` (ひな形の既定 `GH_TOKEN`, `GITHUB_TOKEN`) を外して起動しているので、gh は環境変数のトークンを使えず、`gh auth login` で保存した認証 (keyring / `~/.config/gh/hosts.yml`) だけで動く。保存した認証が無ければ席からの `pr open/merge` は失敗する。そのときは、owner が端末から打つ、gh に認証を保存する、艦の `env_unset` から外す、のどれかにする。`git push` は gh と別で、git の認証 (ssh / credential helper) を使う
- `merge_requires` の `review` は項目の `review=approved`、`ci` は `gh pr checks` (チェックの無い repo は通ったとみなす)、`decision` は項目を `links` に持つ `kind: decision`・`category: merge` の項目が done であること
- 「worktree を cwd にして新しいシフトを起こす」(`send --cwd`、design-p1 §8.2 の 2) はまだ無い。今はシフトの中で `worktree add` が出したパスに `cd` する

艦フォルダ: `team.yaml`・`charter.md`・`knowledge.md`・`roles/<role>.md`・`board/{items,archive}/`・`seats/<seat>/` (`handoff.md`・`log/<date>.md`・`inbox.jsonl`・`inbox.cursor`・`memory.md`・`memory-inbox.md`)・`roster.json` (席ごとの今のシフトと `lastActive`)・`usage.jsonl` (シフトごとの使用量)・`worktrees/<item>/` (`yamato worktree add` の既定の場所)・`events.jsonl` (艦の出来事の追記ログ。形式は [docs/events.md](docs/events.md))・`.runtime/` (生成物)。

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
- `PushNotification` は方式にしない (席の外から出せない。[verify-p1-d](docs/verify-p1-d.md) V11)

テスト: `python3 -m unittest discover`。E2E の手順と結果は [docs/e2e-p0.md](docs/e2e-p0.md)。

## docs
- [design.md](docs/design.md) — 設計書
- [research-claude-primitives.md](docs/research-claude-primitives.md) — Claude Code の仕組みの調査 (2.1.282)
- [spike-zellij-attach.md](docs/spike-zellij-attach.md) — zellij 表示層の検証
- [review-da-v0.md](docs/review-da-v0.md) — 設計書 v0 への devil's advocate レビュー
- [verify-p0-a.md](docs/verify-p0-a.md) / [verify-p0-b.md](docs/verify-p0-b.md) — P0 の実機検証 (起動レシピ)
- [e2e-p0.md](docs/e2e-p0.md) — P0 実装の E2E
- [events.md](docs/events.md) — events.jsonl の行の形式と読み方、lastActive
