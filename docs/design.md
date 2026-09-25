# yamato 設計書 (v1)

- 作成: 2026-09-25 / leader (main セッション)
- 位置づけ: user との相談 (redesign-consult.md) の合意を清書したもの。新 repo の最初のドキュメント候補
- 根拠資料: `research-claude-team-primitives.md` (Claude Code 調査, fact-check 済) / bmweb 記事「AIエージェントに記憶・作業記録を引き継がせる方式の調査」
- 名前: **yamato** (2026-09-25 決定)。本文中の「本システム」は yamato を指す

### 用語 (海軍の見立て, 2026-09-25 決定)

| 概念 | 名前 | 意味 |
|---|---|---|
| システム | yamato | |
| チーム | ship (艦) | |
| 司令塔 | captain (艦長) | チーム内のハブ。`team.yaml` の `hub:` で指定した役割。役割名はチームが自由に付ける (開発チームなら `pm`、調査チームなら `editor` など) |
| 窓口 | admiral (提督) | 艦に出撃と帰投を命じ、全艦を一望する。艦の中の指揮はしない (旧 leader) |
| 人間 | owner | |

本文の「PM」は、開発チームの例での captain を指す。
- 【未決】と書いた箇所はまだ決まっていない

---

## 0. DA レビュー後の決定 (v1, 2026-09-25)

DA レビュー (`da-yamato-design-v0.md`) を受けて、owner と合意した変更。**本文と食い違う箇所は、この節が優先する。**

**blocking の決定**
- **B1 配送**: 送り手が自分で届ける。`send` は記録 (inbox) に残し、宛先の席が止まっていれば起こす。生きている席への即時の配送は、送り手のエージェントが `SendMessage` ツールで行う (送り手は captain / admiral / メンバーで、いずれも Claude のセッション)。席の側で受信箱を監視する方式 (`asyncRewake` / Monitor) は、検証してから追加を考える
- **B2 権限**: 席の既定は auto モード + 「無人のときにやらせない操作」の deny リスト (チームごとに持つ)。外部の文章を読む役割 (調査など) と、merge などの権限を持つ役割を分ける
- **B3 シフトの終わり**: `per_task` の席は、終業処理のあとに自分を停止する。`per_task` 宛ての `send` は常に新しいシフトを起動する (再開はしない)。`persistent` 宛てだけ再開する
- **B4 時間の上限**: 終了時刻はプロセスではなくデータ (`.runtime/deadline`) として持つ。席の hook (SessionStart / Stop) と `send` が毎回確認し、過ぎていれば「引き継ぎを書いて止まれ」を返す。一度きりのタイマーは補助にとどめる (消えても上限は効く)
- **B5 シフトの方式**: 役割ごとに選べるようにする。`shift` に `headless` (`claude -p` の使い捨て。覗けないが、権限・予算・コストの扱いが単純) を追加する。覗いて割り込みたい役割は background のまま

**important の決定 (P1 まで)**
- I1: 書き込みはロック 1 本で直列化する。inbox の既読は別ファイルのカーソルで持つ
- I2: チームの git 規律を明示して、background session が自分で PR を作る既定の動作を上書きする。「タスク = ブランチ」を board の項目にする。merge と衝突解消の担当は team.yaml で決める
- I3: repo のないチームは `worktree.bgIsolation: none` 固定。自動 worktree が作業対象の repo の中に作られることは明記する
- I4: captain も記録から起き直す方式を基本にする。`team status` に各席の「最後に動いた時刻」を出す
- I5: 記録は**役割ごとではなく席ごと** (`seats/<seat>/handoff.md`)。memory の候補は `seats/<seat>/memory-inbox.md` に追記して溜める。仕事の続きは board の項目の本文に書く。未読 inbox と担当ビューにも上限を設ける
- I6: 当面、人間は captain とだけ話す。人間が captain 以外の役を担う場合の経路は後で決める
- I7: P0 から、シフトごとの使用量を 1 行記録する

**P0 の範囲を絞る**: board は task 1 段 + 固定の項目だけ。停止は「終業 + 強制」の 2 段。zellij・日報・memory の棚卸しは P1 以降。

**P0 は検証から始める**: (1) 生きている席への配送 (2) 無人の権限 (3) 起動フラグが再開後も効くか (4) 席が自分を停止できるか (5) worktree の中から記録フォルダに書けるか (6) captain 1 + impl 1 でタスク 1 件を無人で通す (使用量も記録)

## 1. 目的

役割の違う複数の AI エージェントが、**チーム**として協調して仕事を進めるシステムを作る。

- チームの構成 (どんな役割が何人いるか) を自由に定義できる
- チームは**常設**で、長期プロジェクトの backlog を毎日消化し続けられる
- 複数のチームを並べて動かせる (チーム間の連携は当面スコープ外)
- 人間の関与度はチームごとに変えられる。人間がチームの 1 役を担うことも、重要な判断だけすることも、すべて任せることもできる

fleet (leader → driver の 1 段構成) の後継という位置づけ。fleet は本システムの開発に使い、本システムが fleet の役目を果たせるようになったら引退させる。

## 2. 基本方針

| 方針 | 内容 |
|---|---|
| Claude 前提 | Claude Code の仕組みを積極的に使う。vendor 中立は目標にしない |
| 実行は Claude Code に任せる | セッションの起動・会話・隔離・観測は Claude Code のネイティブ機構を使う。自前で作るのは薄い層だけ |
| 正本は記録 | 仕事の連続性は記録ファイルが担う。Claude Code 側の状態 (セッション、`~/.claude` 配下) は正本にしない |
| 常駐プロセスなし | 配送や起動は、コマンドを呼んだときに同期的に行う。デーモンは持たない |
| 起動時に読む量に上限 | 何日動かしても、エージェントが起動時に読む量が一定以下に収まるようにする |

fleet からの方針転換:
- **自律を許す**: fleet は「完全自律は mission に反する」としていた。本システムでは、どこまで AI に任せるかを設定で決められる
- **中身を記録に残す**: fleet はポインタだけを運んでいた (pointer-not-payload)。本システムでは、エージェント同士のやり取りの中身を記録に残す (fleet の peer_review で起きていた「レビュー指摘がどこにも残らない」問題への答え)
- **multi-vendor をやめる**: 本システムでは Claude のみ

## 3. 全体像

```
人間
 ├─ leader ── チームの作成、起動と終業、全チームの状況を一望する (中身には踏み込まない)
 └─ 各チームの PM と直接話す (判断を返す、方針を伝える)

チーム (常設)
 ├─ PM ─── 司令塔。仕事を分けて割り振り、成果を回収し、日報を書く
 ├─ 役割 A, B, C ... ── PM から割り当てを受けて動く
 └─ 記録フォルダ ── board / 引き継ぎ / 作業ログ / 決定 / memory
```

- **協調の形は司令塔型**: PM が分解して割り振り、回収する。メンバー同士が勝手にタスクを取り合うことはしない (自己組織型は将来の拡張)
- **人間はメンバーの一種**: 人間もエージェントと同じ形で宛先になる。違うのは届け方だけ

## 4. 構成要素と Claude Code への対応

| 概念 | 実体 |
|---|---|
| 役割 (role) | Claude Code のカスタムエージェント定義 (`.claude/agents/<role>.md`)。プロンプト、ツール、モデル、権限を持つ |
| 席 (seat) | 役割を担う名前付き background session。`claude --bg --name <team>.<role> --agent <role> --settings <team-settings>` |
| シフト | 席のセッション 1 回分。起動 → 記録を読む → 働く → 引き継ぎを書く → 終わる |
| 会話 | 記録に残してから、Claude Code の `SendMessage` で届ける (§7) |
| 覗く・入る | Claude Code の `claude agents` と `claude attach`。表示は zellij でまとめる (§10) |
| ファイル隔離 | background session の自動 worktree 機能 |
| 下請け | 各役割の中で Claude Code の subagent を使ってよい (テスト実行、並列の調査など) |

### 4.1 起動場所と設定の渡し方 (2026-09-25)

- 席の作業ディレクトリ (cwd) は `team.yaml` の `workspace`。開発チームなら作業対象の repo、調査チームのように repo がないチームならチームフォルダ自身
- **チームの設定は全て起動時のフラグで渡す。作業対象の repo にも `~/.claude` にも書き込まない**
  - `--settings <チームフォルダ>/.runtime/settings.json`: hooks、`crossSessionInbound: "accept"`、権限
  - `--agents '<json>'`: 役割の定義。チームフォルダの役割ファイルから生成する (ファイルパス指定は `--print` のときだけなので、JSON 文字列で渡す)
  - `--add-dir <チームフォルダ>`: 記録を読み書きできるようにする
- hook のコマンドには、チーム名と役割名を**引数として埋め込む**。環境変数では渡さない。Claude Code の常駐 daemon が環境変数を焼き付ける問題があるため (fleet #315 の教訓)
- 作業対象の repo 自身の CLAUDE.md と設定は、そのまま効く (上乗せになる)。プロジェクトの規律はそちらが担う
- 【要検証 P0】`--resume --bg` による再開や supervisor による再起動のあとも、`--settings` と `--agents` が引き継がれるか (`--agent` は引き継がれると docs にある)
- 【要検証 P0】ユーザー設定 (`~/.claude/settings.json`) の hooks が席でも動くこと。邪魔なら `--setting-sources project,local` で外す
- チームフォルダの既定の場所: `~/yamato/<ship>/`。`team create --path` で任意の場所にも作れる

使わないもの:
- **Agent teams (実験機能)**: 1 セッションに 1 チームしか持てず、再開で復元されない。常設チームの土台にならない
- **Agent SDK でのサーバ化**: サブスク認証を前提にできない可能性がある。CLI を叩く
- **`~/.claude` 配下の内部ファイル**: 安定したインターフェースではない。状態は `claude agents --json --all` で読む

## 5. チーム定義

チームは `team.yaml` 1 本で定義する。ひな形 (開発チーム、調査チーム) から作れるようにする。

```yaml
name: dev
hub: pm                          # captain (司令塔) を務める役割
charter: charter.md              # 何のためのチームか (人間が書く)
workspace: ~/dev/myapp           # 作業対象の repo / ディレクトリ

roles:
  pm:       { agent: claude:opus,   shift: persistent }
  designer: { agent: claude:opus,   shift: per_task }
  impl:     { agent: claude:sonnet, shift: per_task, count: 2 }
  reviewer: { agent: claude:opus,   shift: per_task }
  qa:       { agent: claude:sonnet, shift: per_task }
  owner:    { agent: human }

decisions:                       # 判断の種類ごとに、誰が決めるか
  merge:        owner
  design:       pm
  scope_change: owner
  default:      pm

board:                           # チーム固有の board 設定 (§6.1)
  columns:
    - { name: backlog, state: open }
    - { name: design,  state: active }
    - { name: impl,    state: active }
    - { name: review,  state: active }
    - { name: qa,      state: active }
    - { name: done,    state: done }
  kinds: [feature, bug, chore]
  fields: [priority, branch, pr]
```

- 人間の関与度は `decisions` の書き方と、人間を役割に入れるかどうかで決まる。「全部任せる」なら `owner` を AI の役割に書き換える
- `shift` は役割ごとに選べる (§8)

## 6. 記録

### 6.1 フォルダ構成

```
teams/<team>/
  team.yaml
  charter.md
  knowledge.md              ← チーム共有の知見。全員が起動時に読む。PM が棚卸しで育てる
  .runtime/                 ← 生成物 (settings.json など)。起動時に作り直す
  board/
    items/T-042.md          ← 1 項目 = 1 ファイル
    archive/                ← 終わった項目の移動先。普段のビューには出さない
  decisions/log.md          ← 決まった判断の記録 (追記のみ)
  roles/<role>/
    handoff.md              ← 引き継ぎ。1 本を上書きする (常に「今の状態」)
    log/2026-09-25.md       ← 作業ログ。日付ごとに追記する。起動時には読まない
    memory.md               ← 役割の長期記憶。手入れして育てる
    inbox.jsonl             ← 届いたメッセージ (§7)
  reports/daily/2026-09-25.md  ← PM の日報
  roster.json               ← 役割 → 今のセッション ID と状態 (本システムが管理)
```

git で管理するかは利用者が決める。

### 6.2 board

**2 層に分ける**
- **仕組みが読む固定の項目** (全チーム共通): `id` / `title` / `kind` / `parent` / `assignee` / `state` (open・active・blocked・done の 4 つ) / `blocked_on` / `links`
- **チーム定義の中身**: 列、種類、追加の項目、完了の条件。チームの列は固定の `state` に対応させる

仕組みが読むのは固定の項目だけ。チーム独自の部分を読むのはエージェントと人間。

**階層**: charter → goal → milestone → task → subtask。全部同じファイル形式で、`parent` でつなぐ。人間は主に charter と goal を置き、そこから下は PM が分けて作る。

**項目ファイルの例**
```markdown
---
id: T-042
kind: feature
title: ログイン画面の実装
parent: M-003
assignee: impl-1
state: active
column: review
links: [PR#12]
priority: high
---
## 経緯
- 09-25 PM: M-003 から分割して impl-1 に割り当て
- 09-25 impl-1: PR#12 作成、review へ
```

**更新のルール**
- frontmatter の項目は **board コマンド経由でのみ**変更する (`board set T-042 state=done` など)。コマンドで値をチェックし、モデルが勝手に構造を書き換えるのを防ぐ
- 本文 (経緯、メモ) は自由に書いてよい
- done になった項目は archive に移す

**ビュー** (同じファイル群を別の角度で見せる)
- ツリー: goal ごとの進み具合。人間向け。「1 枚でわかる紙」はこれか日報
- カンバン: 列ごとの流れ。PM 向け
- 自分の担当: 自分に割り当てられた項目だけ。メンバーが起動時に読む

board に入れないもの: 「なぜそうしたか」は decisions、「今日何をしたか」は作業ログに書く。

### 6.3 引き継ぎ (handoff.md)

役割ごとに 1 本。シフトの終わりに**上書き**する。

```markdown
# impl-1 引き継ぎ (2026-09-25 18:00)
- 担当状況: T-042 は review 待ち (PR#12)。T-043 は未着手
- 途中の作業: なし
- 次にやること: レビュー指摘が来たら対応。来なければ T-043
- 詰まり: なし
- memory 候補: テストのモックは 30 日で期限が切れる
```

長さに上限を設ける (例: 40 行)。

### 6.4 作業ログ

`roles/<role>/log/<日付>.md` に追記する。監査用と、人間や PM が「何があったか」を追うときに使う。**起動時には読ませない。**

### 6.5 decisions

- 判断待ちは board の項目 (`kind: decision`) として扱う (§9)
- 決まったら、要点を `decisions/log.md` に追記する。「なぜこうなっているか」を後から追える

### 6.6 役割の memory

- `roles/<role>/memory.md` に置き、長さに上限を設ける
- **書き込むのは PM の週次の棚卸しだけ**。各役割は、引き継ぎに「memory 候補」として書くだけにする
- 自動で溜めて、次のプロンプトに自動で入れることはしない (腐るため)
- **自前で置く (2026-09-25 合意)**。Claude Code ネイティブの `memory: project` は使わない。理由: 保存先が作業対象の repo 側になる / 同じ repo で複数チームを動かすと同名の役割で混ざる / エージェントがいつでも書けてしまい、PM の棚卸しだけで書く方針とぶつかる。注入は handoff と同じ SessionStart hook に 1 ファイル足すだけで済む
- チーム全体で共有する知見は `knowledge.md` に置く。全員が起動時に読み、PM が棚卸しで育てる

### 6.7 共通ルール

- 秘密情報は記録に書かない (起動のたびにコンテキストに入る前提で扱う)
- 寿命の違う情報は別のファイルに分ける (今の状態 = 上書き / 経緯 = 追記 / 長期の知見 = 手入れ)

## 7. メッセージと起動 (`send`)

Claude Code の `SendMessage` は、待機中のセッションを起こせる。一方で、記録に残らず、止まったセッションには届かない。そこで配送コマンドを 1 本自作する。

`send <team> <role> "<msg>"` の処理:
1. 宛先の `inbox.jsonl` に追記する (まず記録に残す)
2. `roster.json` と `claude agents --json --all` を照らし合わせ、席のセッションが生きていれば `SendMessage` 相当で届ける
3. 止まっていれば `claude --resume <id> --bg` で再開する。席がなければ新しいシフトとして `claude --bg` で起動する
4. 宛先が人間 (`agent: human`) なら、inbox に書いたうえで通知 (push 通知や Slack) を飛ばす

- エージェントは Bash からこのコマンドを呼ぶ。呼んだ時点で配送と起動が終わるので、常駐プロセスは要らない
- チームの全ての席に `crossSessionInbound: "accept"` を設定する (設定がないと、無人の席でメッセージが黙って保留される)
- 起動したエージェントは、自分の inbox の未読を読む。既読の管理は inbox 側で持つ

## 8. シフト

### 8.1 粒度は役割ごとに選べる

| shift | 起きるとき | 終わるとき |
|---|---|---|
| `per_task` (日雇い) | 割り当てが届いたら、新しいセッションで起動 | 仕事が終わったら引き継ぎを書いて終了 |
| `persistent` (長期雇用) | セッションを使い続け、メッセージが来たら再開 | 人間が止めたとき。コンテキストが膨らんだら、引き継ぎを書いて新しいセッションに入れ替える |

- 1 日 1 回のようなシフトは、人間が手で起動・終業すればよい
- **どの粒度でも、シフトの終わりに引き継ぎを書く**。そのため、粒度を変えても、席が予期せず止まっても、記録から再開できる。会話の再開 (resume) は「楽をするための最適化」であって、連続性の本体ではない

### 8.2 起動時に読むもの (上限つき)

SessionStart hook で次を注入する:
- 役割のプロンプト (エージェント定義)
- 役割の memory
- チームの knowledge.md
- board の「自分の担当」ビュー
- 自分の handoff.md
- inbox の未読

PM はこれに加えて、カンバンビューと直近の日報を読む。

### 8.3 シフトの終わり

- 明示的な「終業」コマンドまたは skill で、handoff.md の上書きと作業ログの追記をさせる
- Stop hook は応答のたびに動くため、引き継ぎの強制には使わない。使うとしても、日雇いの席が引き継ぎを書かずに終わろうとしたときの安全網にとどめる
- SessionEnd hook で会話ログ (transcript) をチームフォルダに保存する (Claude Code 側では 30 日で消えるため)

## 9. 判断とエスカレーション

- 判断待ちは board の項目にする

```markdown
---
id: D-007
kind: decision
title: 認証を JWT にするかセッション方式にするか
decider: owner          # team.yaml の decisions 表から決まる
state: blocked
blocks: [T-042, T-043]  # これが決まるまで止まるタスク
---
## 選択肢と推し
## 決定
```

- decider が AI の役割なら、その役割に `send` で届く
- decider が人間なら、通知が飛び、PM の日報にも載る
- **人間が判断を返す相手は PM**。人間は PM の席に attach して直接話す。PM が決定を書き込み、`decisions/log.md` に転記し、止まっていたタスクを再開させる
- 「AI に上げる」と「人間に上げる」の違いは `decider` の値だけ

## 10. 表示 (zellij)

席そのものは background session として動く。zellij は、それを覗いて入るための**窓**にする。

```
zellij セッション
├─ タブ: dev
│   ├─ ペイン: pm        → その役割の今のセッションに attach
│   ├─ ペイン: impl-1
│   └─ ペイン: reviewer  → 非番なら「待機中」と表示
├─ タブ: research
└─ タブ: leader
```

- 各ペインでは小さなスクリプト (`seat-attach`) を動かす。`roster.json` からその役割の今のセッションを探して attach し、席が入れ替わったら付け直す
- レイアウトは zellij の KDL layout ファイルで宣言する。席を後から足すときは `zellij action new-pane` を使う
- zellij を閉じても、チームは動き続ける (表示と実行を分ける)

**spike `spike-zellij-attach` で確認済み (2026-09-25, zellij 0.45.1 / Claude Code 2.1.282)**
- `claude attach` は zellij のペインで正しく表示され、キー入力で返信もできる
- ペイン、タブ、zellij セッションのどれを閉じても、切断されるだけで席のセッションは生きている
- attach 先が stop / rm されると、`claude attach` は exit 0 で抜ける
- 付け直しスクリプトの試作は動いた (席 A を停止 → 待機 → 同じ名前の席 B を起動 → 自動で B に attach)

**設計上の注意 (spike で判明)**
- **止まっているセッションに attach すると、そのセッションが再起動する。** 古いシフトを蘇らせないよう、pid があるもの (生きているもの) にだけ attach する
- **`--name` は一意にならない。** 同じ名前のセッションを複数作れる。そのため「その席の今のシフト」の正本は `roster.json` に置き、名前からは探さない
- 同じ席を 2 つのペインで開くと、入力欄が共有される (片方に打った下書きが、もう片方にも出る)
- attach していれば、約 1 時間で止められるルールの対象から外れる (docs 上。1 時間の実測はしていない)。**zellij で窓を開いている席は常駐する**。ターンは消費しないが、メモリは食う
- 席の作業ディレクトリは、事前に Claude Code の workspace trust を通しておく必要がある
- attach はペインのフォアグラウンドで動かす (macOS ではバックグラウンドで動かすと落ちる)

## 11. admiral (窓口)

- 人間の窓口。仕事は**チームの作成、起動と終業、全チームの状況を一望すること**
- チームの中身 (判断、方針) には踏み込まない。それは人間と PM が直接やる
- 【未決】fleet の leader を admiral として流用するか、yamato 用に作り直すか

## 12. 1 日の回り方 (例)

1. 朝: 人間がチームを起動する → PM が起きて board を棚卸しし、計画を立てて割り振る (`send`)
2. 日中: 割り当てを受けた席が起き、働き、引き継ぎを書いて終わる。PM が成果を回収し、次を割り振る
3. 判断待ちは decision 項目になり、decider に届く
4. 夕方: PM が日報を書いて owner に送る (判断待ちの一覧つき)
5. 夜: 人間が終業する → 全ての席が引き継ぎを書いて止まる
6. 週 1 回: PM が memory 候補を棚卸しして、各役割の memory に反映する。終わった項目を archive に移す

### 12.1 稼働時間の上限 (2026-09-25 合意)

暴走は**予算ではなく時間で止める**。チームごとに稼働時間の上限を設定する。

```yaml
# team.yaml
time_limit: 3h        # team up からの稼働時間
last_call: 30m        # 終了の何分前から、新しい大きな割り当てを止めるか
grace: 20m            # 終了時刻のあと、キリのいいところまで待つ時間
```

`team up dev --for 3h` のように、起動するときに上書きもできる。

止まり方は 3 段階:

| 時刻 | 何が起きるか |
|---|---|
| 終了 30 分前 (最終受付) | PM に通知する。PM は新しい大きな割り当てをやめ、今の仕事を片付ける方向に切り替える |
| 終了時刻 (終業) | 全ての席に「キリのいいところで止めて、引き継ぎを書いて終われ」を送る |
| 終了時刻 + 猶予 (強制停止) | まだ残っている席を `claude stop` で止める。止められた席は `roster.json` に「引き継ぎなしで終了」と記録する。次に起動したとき、その席は handoff に加えて作業ログの末尾も読む |

- タイマーは `team up` のときに一度きりで仕掛ける (起動したら時刻が来るまで待って、それぞれの段階の処理を 1 回ずつ走らせる)。常駐のデーモンは作らない
- `team down` を手で打てば、その時点で終業の段階から始まる
- PC がスリープするとタイマーは遅れる。スリープ中はチームも止まっているので、実害はない

## 13. コマンド (案)

| コマンド | 内容 |
|---|---|
| `team create <name> --template dev` | ひな形からチームを作る |
| `team up <name> [--for 3h]` / `team down <name>` | 起動 (稼働時間つき) / 終業 |
| `team status [<name>]` | 席の状態、board の要約 |
| `send <team> <role> "<msg>"` | メッセージを送る (§7) |
| `board add / set / show / tree / kanban / mine` | board の操作と表示 |
| `shift end` | 終業処理 (引き継ぎと作業ログ)。エージェントが使う |
| `view` | zellij の表示を開く |

## 14. リスク

- Claude Code の background session と agent view は research preview で、仕様が週単位で変わる。Claude Code とのやり取りを 1 か所に閉じ込めて、変更をそこで吸収する
- 待機中のまま誰も attach しないで約 1 時間経つと、席のプロセスは止められる。シフト制と記録ベースの再開で吸収する
- 使用量は席の数とシフトの頻度に比例して、サブスクの枠を消費する
- 自律ループの暴走。稼働時間の上限で止める (§12.1)。予算の上限は掛けない

## 15. 未決事項

- 稼働時間の上限の各時刻の既定値 (最終受付、猶予時間)
- `persistent` の席を入れ替える条件 (コンテキストの量、時間)
- 人間宛て通知の経路 (push / Slack / その他)
- zellij で窓を開いている席は常駐する (attach で 1h 停止を免れる)。全席を開くか、見たい席だけ開くか
- fleet からの移行手順、leader の扱い

## 16. 作る順番 (案)

1. **P0 記録と起動**: チームフォルダ、`team.yaml`、board コマンド、席の起動 (`claude --bg` + SessionStart hook)、`send`
2. **P1 チームとして回す**: PM の役割プロンプト、終業、判断とエスカレーション、日報、ひな形 (開発、調査)
3. **P2 表示**: zellij の表示層、`team status`
4. **P3 運用**: コストと監査ログの集計、memory の棚卸しの支援、archive

P0 と P1 まで作った時点で、本システムの開発そのものを本システムの開発チームにやらせる (dogfooding)。
