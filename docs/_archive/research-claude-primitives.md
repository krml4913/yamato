# 調査報告: 常設・ロール制・ハブ型エージェントチームに使える Claude Code / Agent SDK のプリミティブ

- 調査日: 2026-09-25
- 実機で確認したバージョン: `claude --version` → **2.1.282 (Claude Code)**(macOS)
- 主な一次資料: 公式ドキュメント https://code.claude.com/docs/en/ (llms.txt 索引から全関連ページを取得して通読)。加えて、この調査セッション自身が持っているツール定義(SendMessage / ListAgents / CronCreate / Monitor / RemoteTrigger / FetchInboxMessage)と、`claude --help`・`claude agents --json`・ListAgents の実出力を証拠として使った。
- 注意: ドキュメント本文にバージョン注記が大量にある(v2.1.18x〜v2.1.27x)。**この領域は週単位で変わっている**。以下の記述は 2.1.282 時点の話であり、下で「research preview / experimental / beta」と書いたものは特に変わりやすい。

---

## 0. 結論(先に 5 行)

1. **「シフト」には `claude --bg`(background session)か `claude -p`(headless)が合う。** 常設ロールの「席」は *名前付き background session*、シフトは *そのセッションの 1 回の起動〜終了*、または *毎回新規の `-p` 実行* として表現できる。どちらも transcript は `~/.claude/projects/` に残り、`--resume <session-id>` で再開できる。
2. **エージェント間メッセージはネイティブにある(cross-session messaging: `ListAgents` + `SendMessage`)。idle なセッションに届くと新しいターンが始まる = wake できる。** ただし「プロセスが生きていて inbox ソケットを bind しているセッション」限定で、止まったセッションは起こせない。background session のプロセスが止められるのは「finished / waiting のまま未 attach で約 1h」の場合だけで、**working 扱い(`/loop` の合間、subagent・workflow・Monitor が動作中)や pin 済みなら止められない**。そのため、席に `/loop` か Monitor を持たせて生かし続ければ、ネイティブ機構だけで「常駐 + SendMessage で wake」を組める。ただしこの常駐には使用量コスト、Monitor の 30 分再アーム、`/loop` の 7 日失効が伴い、メモリ逼迫時には止められる。**止まった席のための fallback(記録への永続化 + resume)は依然必要**(§2・§8 で詳述)。
3. **Agent teams(実験機能)は「リード 1 + teammate N、共有タスクリスト、mailbox」をそのまま持っているが、常設チームの土台には向かない。** 1 セッション 1 チーム・resume で in-process teammate が復元されない・ネスト不可・リード固定・`-p`/SDK では teammate を spawn できない、と「長期・使い捨てシフト」モデルと正面衝突する。
4. **スケジュール系は 3 種類ある。** (i) session スコープ(`/loop`・CronCreate。繰り返しは 7 日で失効、原則ディスクに残らない。background 化すると `/loop` は background session に引き継がれる)、(ii) **Desktop scheduled tasks**(ローカル実行、再起動をまたいで永続、最短 1 分、毎回新規セッションを起動、タスクごとに permission mode を設定、スリープで逃した分は復帰時に 1 回だけ catch-up。Desktop アプリ起動中かつマシン稼働中のみ)、(iii) cloud の Routines(research preview、最短 1 時間、日次 run 上限)。**「イベント駆動の wake」はどれにもないが、(ii) を使えば「1 分ごとに inbox を見て、新着があればシフトを起動する」ポーリング型のローカル wake はネイティブで組める。**
5. **よって新システムが自作すべき薄い層は: (a) 記録ストア(タスクボード/ジャーナル/ハンドオフ/決定ログ/ロール別メモリ)、(b) ロール→セッションの台帳と wake-on-message(まず記録に永続化し、席が生きていれば SendMessage、止まっていれば `claude --resume <id> --bg "<msg>"` か新規シフト起動。台帳は `claude agents --json --all` で照合)、(c) PM のディスパッチループ、(d) 人間エスカレーション経路。** 実行・会話・観測・隔離は Claude Code に丸投げできる。

---

## 1. セッションの起動方法(Q1)

### 何があるか

| 方式 | 起動 | 生存 | 再開 | 人間が覗ける/入れるか |
|---|---|---|---|---|
| 対話 (`claude`) | 端末に紐づく | 端末を閉じると終了(`/bg` で background 化は可) | `--resume <id>` / `--continue` / `--name` で名前指定 | そのまま |
| **background (`claude --bg "<prompt>"`)** | supervisor(常駐デーモン)配下の独立プロセス | 端末なしで動き続ける。**finished / 入力待ちのまま未 attach で約 1 時間経つと supervisor がプロセスを止める**(会話はディスクに残り、attach か返信で再開)。working(`/loop` の合間、subagent・workflow・Monitor が動作中)、ダイアログ待ち、pin 済み(agent view の `Ctrl+T`。CLI フラグはない)は止められない。ただしメモリ逼迫時は pin 済みの idle セッションも止められ得る | `claude attach <id>`、`claude --resume <sessionId> --bg "<msg>"`(v2.1.257+ は同じ ID で継続) | `claude agents`(agent view)で一覧・peek・返信・attach。`claude logs <id>` |
| headless (`claude -p`) | 1 プロンプト実行して終了 | 実行中のみ | `--resume <id>` / `--continue`。`--session-id <uuid>` で ID を事前指定可 | attach はできない(stream-json で外から追う)。ただし inbox ソケットを bind するので(`--bare` を除く)ListAgents に出て、実行中も SendMessage を受けられる。終了後は `claude --resume` で対話的に開ける |
| Agent SDK (Python/TS) | ライブラリが `claude` CLI をサブプロセスとして起動 | `query()` 1 回 or `ClaudeSDKClient`/streaming input で長寿命 | `resume` / `continue` / `fork`、`SessionStore` で transcript を外部 DB にミラー | ホスト側の実装次第 |

根拠:
- `--bg` の挙動、`claude agents/attach/logs/stop/respawn/rm`、supervisor の 1 時間停止ルール、pin、`--resume <id> --bg` の同一 ID 継続: [agent view](https://code.claude.com/docs/en/agent-view)(「How background sessions are hosted」「From your shell」節)。`claude --help` 実出力にも `--bg, --background … Prints the id that claude attach, logs, stop and rm take` と `--resume <session-id>` との組み合わせが明記。
- `--bg` と `-p` は併用不可(`--print never starts the interactive session`): 同上。
- `-p` の各種挙動(`--output-format json` に `total_cost_usd`、`--json-schema`、SIGTERM で exit 143 と `SessionEnd` フック、背景 subagent を最大 10 分待つ): [headless](https://code.claude.com/docs/en/headless)。
- SDK のセッション(continue/resume/fork、session_id の取得)、streaming input が推奨、SessionStore: [SDK sessions](https://code.claude.com/docs/en/agent-sdk/sessions)、[streaming input](https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode)、[session storage](https://code.claude.com/docs/en/agent-sdk/session-storage)、[hosting](https://code.claude.com/docs/en/agent-sdk/hosting)(「Hybrid sessions」= 記録から水和して終わったら書き戻す、がまさにシフトモデル)。
- `claude agents --json` が外部スクリプトから状態を読む「supported way」(`state`: working/blocked/done/failed/stopped、`status`: busy/waiting/idle、`waitingFor`)。`~/.claude/jobs/<id>/` のファイルは安定インターフェースではない: [agent view](https://code.claude.com/docs/en/agent-view#read-session-state-from-a-script)。**`--all` を付けないと完了済みセッションが出ない**(docs は「Poll `claude agents --json --all`」と明記)ので、台帳の照合には `--json --all` を使う。`working` の定義には「自分で回している作業の合間(`/loop` の繰り返しや CI 待ちなど)」も含まれる。実機で `claude agents --json` を叩くと、pid を持たない(=プロセス停止済み)background セッションが `state: blocked` 等で並ぶことを確認した。

### 「シフト」(記録から起動 → 作業 → 記録を書く → 終了)にどれが合うか

- **第一候補: `claude --bg --name <team>-<role> --agent <role> "<shift prompt>"`**。理由: 人間が `claude agents` で全員を一覧・peek・返信・attach できる(要件「人間がいつでも覗いて入れる」を満たす唯一のローカル手段)。`--agent` でロール定義(`.claude/agents/<role>.md`)をメインエージェントとして使え、resume/restart 時もロールとツール制限が復元される。background session は編集前に自動で worktree に移る(後述)。
- **第二候補: `claude -p --session-id <uuid> --output-format stream-json`**。完全に機械駆動したい短いシフト(QA の 1 回実行、fact-check 1 件など)向け。`--max-budget-usd` でシフトごとの予算上限を掛けられる。無人実行では **`--permission-prompts none`**(v2.1.259+)を付ける。承認が必要な操作は待たずに deny し(`PermissionRequest` フックが許可したものは除く)、`AskUserQuestion` のように人間の回答が要るツールを外す([headless「Turn off permission prompts in unattended runs」](https://code.claude.com/docs/en/headless))。実行中でも SendMessage は受けられるが、attach はできない。覗くには終了後 `claude --resume`。
- **SDK は「自分でホスト/サーバ化するなら」**。ただし [SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) に「Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products … Use the API key authentication」とある。**個人ツールとしてサブスクリプション枠で回したいなら、SDK より `claude` CLI(`--bg` / `-p`)を叩く設計の方が安全**。SDK を使う場合は API キー課金になる前提で考えるべき(ここは規約解釈を含むので要確認事項として残す)。
- `--bare` は `-p` で hooks/CLAUDE.md/auto-memory/OAuth を読まない最小モードで、「将来 `-p` のデフォルトになる」と明記されている([headless](https://code.claude.com/docs/en/headless#start-faster-with-bare-mode))。**bare だとサブスク認証が使えず API キー必須、inbox ソケットも bind しない**(= メッセージを受けられない、[cross-session](https://code.claude.com/docs/en/cross-session-messaging#non-interactive-sessions))。`-p` でシフトを組むなら、このデフォルト変更は将来リスク。

---

## 2. エージェント間メッセージ(Q2)

### 何があるか

**Cross-session messaging**(v2.1.224+、macOS/Linux はデフォルト有効)。ツールは `ListAgents`(宛先発見)と `SendMessage`(名前宛て送信)。同じ `SendMessage` がサブエージェント・teammate・別セッションすべてに使える。出典: [cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging)。この調査セッション自身にも両ツールがあり、ListAgents を実行すると「このセッションの名前(他セッションからの宛先)」+ ローカル interactive / Remote Control / cloud の peer 一覧が返った(実機確認)。

- **wake できるか: できる(条件付き)。** 「受信側が idle なら Claude Code がそのメッセージで新しいターンを開始する。作業中ならツール呼び出しの合間に読む」。→ **生きている idle セッションは SendMessage で起こせる。**
- **止まったセッションは起こせない。** ローカル peer は「inbox ソケットを bind したセッションだけ」一覧に出る。supervisor が 1 時間ルールで止めた background セッションはプロセスがないのでソケットもない。実機でも、pid のない background セッション(例: `fleet-q-guard-designer`)は ListAgents 上ローカル peer としては出ず、Remote Control 経由の `offline` としてだけ見えた。offline 宛ては「そのマシンが再接続したら届く」とあるが、これは別マシンの RC 切断を想定した記述で、**supervisor に止められたローカル background セッションが RC 経由メッセージで再起動されるかはドキュメントに記述がなく、未検証**。
- **席を止めさせない(keep-alive)ネイティブの手段**([agent view「The supervisor process」](https://code.claude.com/docs/en/agent-view#the-supervisor-process)): 止められるのは finished/waiting のまま未 attach で約 1h のセッションだけ。「A running subagent, workflow, or monitor counts as working」とあり、`claude agents --json` の `working` には「`/loop` の繰り返しの合間」も含まれる。つまり次のどれかで席は生き続け、SendMessage で起こせる。
  1. **Monitor で inbox を watch する**: イベント駆動なので、新着が 1 行出ればその場で通知される。ただし Monitor は最大 30 分で失効するので(ツール定義)、そのたびに再アームするターンが要る。1 日 48 回程度の小さなターンになる(トークンは少ないが 0 ではない)。
  2. **`/loop` / CronCreate で定期的に inbox を見る**: 繰り返しは 7 日で失効する。空振りのティックも毎回ターンを消費する。
  3. **pin(`Ctrl+T`)**: ターンを消費しない。ただし agent view の TUI キーとしてしか文書化されておらず、`claude --bg` にも `claude agents` にも pin フラグはない(`--help` で確認)。**スクリプトからは pin できない。** さらに、メモリが逼迫すると supervisor は他を止めても足りない場合に pin 済みの idle セッションも止める([agent view troubleshooting](https://code.claude.com/docs/en/agent-view))。
  - 評価: PM のように常駐する価値が高い 1〜2 席なら、1(Monitor)か 3(pin)で常駐させるのは現実的。メンバー全員を常駐させると、ロール数 × 再アーム/ティック分の使用量がかかる。どの方法でも、メモリ逼迫・マシン再起動・auto-update 時の再起動では止まり得るため、**「止まった席への fallback」は省略できない**。
- **SendMessage を使わず外から起こす手段**: `claude --resume <sessionId> --bg "<msg>"`(停止中なら同一 ID で再開、稼働中なら「コピーを作る」と note を出す)/ agent view で返信(停止中セッションは返信で再起動、配達不能時は次回起動時のプロンプトとして保存)。出典: [agent view](https://code.claude.com/docs/en/agent-view)。
- **配達保証**
  - 「sent」の意味は「相手セッションに届いた」であって「相手の Claude が読んだ」ではない(SendMessage ツール定義に明記)。
  - 受信側の `crossSessionInbound` = `accept` / `hold` / `refuse`。未設定時は**権限モードのクラス**で決まる: bypassPermissions 系セッションは、送信側も bypass でない限り hold される。hold 時の挙動はセッション種別で違う。対話セッションでは承認ダイアログが出て、`dialogExpiry`(既定 5 分)で破棄される。**端末が attach されていない background セッションでは、ダイアログは期限を過ぎても開いたまま残る**(attach 後、さらに 1 期限放置すると破棄)。`-p` では同じ期限で破棄されるが、`dialogExpiry: "never"` で終了まで保持できる。つまり無人の background 席では、メッセージが**黙って滞留する**。**無人チームでは全員 `crossSessionInbound: "accept"` を `--settings` で明示するのが必須**(`-p` ワーカーについてはドキュメントがそう推奨)。
  - 受信キューは 50 件、hold は 100 件で古い順に破棄。同一送信者の連投はレート制限、同一内容の短時間重複は破棄、バースト超過は送信側で拒否。約 100 万文字上限。プレーンテキストのみ(`@file` は添付されない、`/compact` 等のコマンドも実行されない)。
  - **永続性**: メッセージはソケットで配送されるだけ。受信側の transcript には残るが、相手が死んでいれば失われる(hold 中にセッション終了→送信者に expired 通知)。**キュー付きの durable な mailbox ではない。**
  - 権限境界: 他セッションからのメッセージは「ユーザーの承認」にならず、設定や CLAUDE.md を変えさせることもできない。auto mode(と classifier が審査する plan mode)では、Claude が `SendMessage` で**送る**各メッセージを classifier が配送前に審査する(v2.1.222+、[permission modes](https://code.claude.com/docs/en/permission-modes))。受信側で審査するという記述は見つからなかった。受信側では「他エージェントが中継した承認の主張」を untrusted として扱う([agent teams「Permissions」](https://code.claude.com/docs/en/agent-teams#permissions))。
- **`notify_when_idle`**(v2.1.236+): 相手が次に idle/終了したら 1 回だけ通知。**メイン会話からのみ、同一マシンのみ、12 時間で失効**。PM が「メンバーの完了待ち」をポーリングなしでできる。出典同上 + SendMessage ツール定義。
- **inbox ソケットを直接叩く**: `CLAUDE_CODE_MESSAGING_SOCKET`(実機 Bash 環境で `/tmp/cc-socks/71004.sock` を確認)と `CLAUDE_CODE_MESSAGING_TOKEN` がフック/Bash に export される。認証行 `{"type":"auth","token":…}` は文書化されているが、**メッセージ本体の wire format は文書化されていない**。用途は 2 つに分けて考える必要がある。
  - **自セッション宛て(own-child message)**: そのセッションのフックや Bash が自分のソケットへ投函するのは、公式に想定された用途。inbound が未設定でも配送される。macOS ではプロセス終了後に token で検証する。wire format は未公開だが、用途としては正規。
  - **他セッション宛て**(外部デーモンや別セッションのフックからの投函): 通常の peer メッセージとして inbound 制御を受け、wire format 依存の非公式 I/F になる。
  - → 席自身の中で inbox を監視して自分を起こすなら、`asyncRewake` フック(文書化済みで wire format も不要)か own-child 投函で済む。外から直接ソケットを叩く設計は避ける。
- 別マシン/クラウド宛ては Remote Control 接続が必要、claude.ai ログイン必須(API キーや Bedrock 等では不可)。

### 他の「メッセージが入ってくる」経路

- **Agent teams の mailbox**: `~/.claude/teams/{team}/inboxes/{agent}.json` の JSON ファイル。書き込み成功で sent 扱い。チーム内限定([agent teams](https://code.claude.com/docs/en/agent-teams#architecture))。
- **Channels**(research preview): MCP サーバが稼働中セッションにイベントを push(Telegram/Discord/iMessage 同梱、自作可)。双方向で、**権限プロンプトをチャネルへ中継**(`claude/channel/permission`)もできる。「セッションが開いている間だけ届く」([channels](https://code.claude.com/docs/en/channels)、[channels reference](https://code.claude.com/docs/en/channels-reference))。
- **Remote Control の session inbox**(`FetchInboxMessage`): チャット(Slack 等)から RC 経由で届くメッセージ。本文は untrusted 扱い(このセッションのツール定義より)。
- **`asyncRewake` フック**: バックグラウンドで走るフックが exit 2 で終わると idle でも即座に Claude を起こす([hooks](https://code.claude.com/docs/en/hooks) の handler fields / async hooks 節)。→ **「ファイル inbox を監視して、新着があれば起こす」ローカル wake をフックで組める**(ただしセッションが生きている間だけ)。
- **Monitor ツール**: スクリプトの stdout 1 行 = 1 通知。最大 30 分で失効、再アームが必要(Monitor ツール定義)。

---

## 3. サブエージェント / カスタムエージェント(Q3)

- **定義**: `.claude/agents/<name>.md`(project)/ `~/.claude/agents/`(user)/ managed / `--agents <json>`。frontmatter で `tools`・`model`・`permissionMode`・`mcpServers`・`skills`・`hooks`・`isolation: worktree`・`background`・`memory`・`omitClaudeMd`・`maxTurns` 等。本文がシステムプロンプト。ファイル変更は数秒で反映。出典: [sub-agents](https://code.claude.com/docs/en/sub-agents)。
- **ロール定義の置き場としては最適**: 同じ定義を (a) サブエージェント、(b) agent team の teammate、(c) `claude --agent <name>` / `claude --agent <name> --bg` でセッションのメインエージェント、の 3 通りに使い回せる([agent view](https://code.claude.com/docs/en/agent-view#from-your-shell)、[agent teams](https://code.claude.com/docs/en/agent-teams#use-subagent-definitions-for-teammates))。
- **ロール別メモリがネイティブにある**: `memory: project|user|local` → `.claude/agent-memory/<agent>/MEMORY.md` 等。起動時に先頭 200 行/25KB を注入、読み書きツール自動付与、`project` スコープは VCS 共有推奨。**ただし auto memory を切ると無効**。「curated per-role memory」の土台にそのまま使える。
- **寿命**: サブエージェントは親セッションの中でしか生きない。完了後も agent ID/名前宛ての `SendMessage` で transcript から resume できる。transcript は `cleanupPeriodDays`(既定 30 日)で削除される。保存パスについて、sub-agents ページの「Resume subagents」節には「`~/.claude/projects/{project}/{sessionId}/subagents/` に `agent-{agentId}.jsonl` として保存」と**文書化されている**(fact-check では見落とされていたが、同ページに記述がある)。ただし、このマシンの `~/.claude/projects` を深さ 4 まで探しても `agent-*.jsonl` は見つからなかった。**文書化はされているが実機では未確認なので、このパスを I/F として当てにしない**。
- **並列**: 同時 20 本(`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`)。ネスト既定 3 層(`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`)。
- **観測・介入**: 対話セッションならサブエージェントパネル/`/tasks` から transcript を開いて打ち込める。background subagent の権限プロンプトはメインに上がる。`-p` では `--forward-subagent-text` で本文もストリームに出せる。
- **チームロールとして使えるか**: **短命な下請け(PM が 1 件投げて要約を受け取る)には使える。常設ロールには不向き。** 親セッションが死ねば消える、人間が別端末から直接入れない、`-p`/SDK では入れ子の background subagent を待たない、という制約がある。**常設ロール = 別セッション、そのセッションが内部で下請けにサブエージェントを使う**、の 2 段構えが自然。
- **Agent teams(experimental, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`)**: リード + teammate(各自独立セッション)、共有タスクリスト(pending/in progress/completed、依存関係、ファイルロックで claim)、mailbox、`TeammateIdle`/`TaskCreated`/`TaskCompleted` フック、tmux/iTerm2 分割表示。**制限(公式 Limitations 節)**: in-process teammate は `/resume`・`/rewind` で復元されない / タスク状態が遅れる / 1 セッション 1 チーム / ネスト不可 / リード固定 / teammate の権限モードは spawn 時に個別指定不可 / 非対話(`-p`・SDK)では teammate を spawn しない / teammate の worktree 隔離なし / チーム設定(`~/.claude/teams/{team}/config.json`)はセッション終了で削除。ただし**タスクリスト(`~/.claude/tasks/{team}/`)はローカルに残り、resume 後も保持される**(保持期間は `cleanupPeriodDays`)。team 名は `session-<session ID の先頭 8 文字>` で自動生成され、プロジェクト側で定義することはできない。→ タスクリストはセッションに紐づく内部状態なので、**チームを超えて長期間使う共有ボードとしては流用しない方がよい**(複数のシフト=セッションにまたがって共有できず、形式も非公開)。出典: [agent teams](https://code.claude.com/docs/en/agent-teams)。第三者の解説記事も同じ制限を挙げ「無人インフラではなく観察付きの協調レイヤ」と評している([alexop.dev](https://alexop.dev/posts/from-tasks-to-swarms-agent-teams-in-claude-code/)、[prodfeat.ai](https://www.prodfeat.ai/en/blog/2026-02-25-claude-code-agent-teams))。ただしこれらは公式ドキュメントの要約が主で、独立した検証としては弱い。

---

## 4. スケジューリング / トリガ(Q4)

| 仕組み | どこで動く | 永続性 | 制約 | 「メッセージで起こす」に使えるか |
|---|---|---|---|---|
| `/loop`, `CronCreate`, `ScheduleWakeup` | 起動中セッション内 | **session スコープ、原則ディスクに書かない**(このセッションの CronCreate ツール定義には「`durable` は効果なし」とある)。ただし公式ドキュメントの Limitations には「feature-flag fetching を切っている場合、セッションをまたいで残したいタスクは `.claude/scheduled_tasks.json` に保存される」とあり、**条件付きで食い違う**。`--resume` で未失効分は復元。**セッションを background 化すると `/loop` タスクは background session に引き継がれ、端末なしで回り続ける** | 繰り返しは **7 日で自動失効**、idle 時のみ発火、ジッタ(最大 30 分遅延)、取りこぼしの catch-up なし、1 セッション 50 件 | 間接的(定期的に inbox を見に行くポーリング)。セッションが死ねば止まる。一方で、回している間は席が working 扱いになり supervisor に止められない(keep-alive 効果) |
| **Desktop scheduled tasks** | ローカル(Desktop アプリ) | **永続**(再起動をまたぐ) | **最短 1 分**。毎回新規セッションを起動する。ローカルファイル・ツールにアクセス可。タスクごとに permission mode と model を設定でき、worktree 隔離のトグルもある。Desktop アプリ起動中かつマシン稼働中のみ発火(スリープ中は skip し、復帰時に直近 7 日分の取りこぼしのうち最新 1 回だけ catch-up)。数分の決定的な遅延あり。**Desktop のセッション面からセッション間メッセージは送受信できない**(公式ドキュメントの記述)。Manual mode で未許可のツールに当たると、承認されるまで止まる | **ポーリング型のローカル wake に使える**: 「1 分ごとに記録ストアの inbox を見て、新着がなければ即終了、あればシフトを起動する/自ら処理する」。空振りでも毎回 1 セッション分の起動コストがかかる。Desktop アプリへの依存(CLI だけでは完結しない)が弱点 |
| **Routines**(research preview) | Anthropic クラウド(or 自前 self-hosted env) | 永続 | 最短 1 時間間隔、**アカウント日次 run 上限**、repo は GitHub clone、ローカルファイル不可、権限プロンプトなしで自律実行 | **API トリガ(bearer token で POST)**・GitHub イベントで起動可 → クラウド側でなら「メッセージで起こす」になる |
| hooks | セッション内 | 設定ファイルに永続 | セッションが生きている間だけ | `asyncRewake` で idle セッションを起こせる / `SessionStart`・`Stop`・`SessionEnd` でシフト開始・終了の自動化 |

出典: [scheduled tasks](https://code.claude.com/docs/en/scheduled-tasks)、[routines](https://code.claude.com/docs/en/routines)、[desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)、[hooks](https://code.claude.com/docs/en/hooks)。

**シフト自動化に効くフック**(いずれも [hooks](https://code.claude.com/docs/en/hooks)):
- `SessionStart`(matcher: `startup`/`resume`/`clear`/`compact`/`fork`): 記録ストアからボード・ハンドオフを読んで `additionalContext` として注入 = 「記録から起動」。`compact` にもマッチさせれば compaction 後に再注入できる。`agent_type`(`--agent` 名)も受け取れるのでロール別に出し分け可能。
- `Stop`: 応答終了ごと。exit 2 で「止まるな」と押し戻せる(= 記録を書くまで終わらせない、の強制に使える)。入力に `background_tasks` と `session_crons` があり「本当に終わったか/待機中か」を判別できる。`last_assistant_message` も取れる。
- `SessionEnd`: 終了理由付き、transcript アーカイブに使える(SIGTERM でも走る)。
- `TeammateIdle` / `TaskCompleted`: agent teams 使用時の品質ゲート。
- `PreCompact` / `PostCompact`: compaction の前後。
- `Notification`: agent view 経由で `agent_needs_input` / `agent_completed` が発火(v2.1.198+、[agent view version history](https://code.claude.com/docs/en/agent-view#version-history))。→ **メンバーが入力待ち/完了になったら PM や人間に知らせる配線点**。

**結論**: ローカルに「イベント駆動で、止まったセッションも起こせる永続的な wake-on-message」の組み込みはない。ネイティブで組める近似は 2 つ。
1. **常駐席 + イベント wake**: 席を `/loop` か Monitor で working に保つか、pin で常駐させ、SendMessage や `asyncRewake` で即時に起こす。
2. **ポーリング wake**: Desktop scheduled task が最短 1 分で inbox を見て、シフトを起動する。

どちらにも「止まり得る」「Desktop に依存する」「空振りにも使用量がかかる」という穴があるため、**「記録に永続化してから配送し、届かなければ resume/新規起動する」薄い配送層は自作する**(§8)。出典: [scheduled tasks](https://code.claude.com/docs/en/scheduled-tasks#limitations)、[desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)、[agent view](https://code.claude.com/docs/en/agent-view#the-supervisor-process)。

---

## 5. コンテキストとメモリ(Q5)

- **コンテキスト長**: 現行の Fable 5.x / Sonnet 5 / Opus 4.7 以降は Anthropic API 上で全プランで 1M トークン窓([model config](https://code.claude.com/docs/en/model-config)、[context window](https://code.claude.com/docs/en/context-window))。`--autocompact <tokens>` / `/autocompact 500k` で早めに compaction させられる(`claude --help` にも `--autocompact <auto|tokens>` あり)。
- **Compaction で残るもの**([context window「What survives compaction」](https://code.claude.com/docs/en/context-window#what-survives-compaction)): システムプロンプト、ルート CLAUDE.md とスコープなし rules(ディスクから再注入)、auto memory(再注入)、plan mode のプラン、直近に触ったファイル最大 5 本、スキル本文(上限付き)、`SessionStart(compact)` フックの出力。**会話でだけ伝えた指示は要約に溶ける。**
- **CLAUDE.md 階層**: managed → user(`~/.claude/CLAUDE.md`)→ project(ルート、上位ディレクトリ)→ `CLAUDE.local.md` → サブディレクトリ(遅延ロード)、`.claude/rules/`(`paths:` でスコープ)、`@path` インポート(4 段まで)。AGENTS.md も直接読む(v2.1.277+)。200 行以下推奨、4MiB まで読む。出典: [memory](https://code.claude.com/docs/en/memory)。
- **auto memory**: `~/.claude/projects/<project>/memory/MEMORY.md` + トピックファイル。先頭 200 行/25KB を毎回ロード、トピックは必要時に読む。git repo 単位で worktree 共有、**マシンローカル**。`autoMemoryDirectory` で置き場所を変えられる(→ チームの記録フォルダに向けることも可能)。
- **サブエージェント/ロールの memory**: 上述の `memory:` フィールド。
- **公式が推す引き継ぎ方法**: 公式の答えは「会話ごと続けたいなら resume/fork、指示は CLAUDE.md に書け、会話だけの指示は compaction で消える」。**「ロールは永続・セッションは使い捨て」の記録ベース引き継ぎを公式に定型化したものはない**。一番近いのは SDK hosting の「Hybrid sessions: SessionStore から水和して終わったら書き戻す」パターン([hosting](https://code.claude.com/docs/en/agent-sdk/hosting#hybrid-sessions))と、Projects の「project memory を全スレッドが共有」([projects](https://code.claude.com/docs/en/claude-projects))。
- **推奨(私見、根拠は上記の仕様)**: 記録の *正本* は自前ストア(Markdown/JSON、git 管理可)。シフト起動時に `SessionStart` フックで「ボード抜粋 + 自ロールのハンドオフ + 直近の決定ログ」を注入、ロール別メモリは subagent `memory: project`(または `autoMemoryDirectory` を記録フォルダへ)、終了時は `Stop` フックで「ハンドオフを書いたか」を検査して未記入なら exit 2 で押し戻す。**resume は同一シフト内の中断復帰にだけ使い、シフトをまたぐ継続は記録で行う**(resume は古い会話全体をキャッシュ切れで再送するので高くつく。SessionStart の resume 入力に `estimated_cache_write_usd` があるのはまさにこのため)。

---

## 6. 隔離と可観測性(Q6)

- **worktree 隔離**: `claude -w/--worktree [name]`、`--tmux` 併用。background session は**編集前に自動で `.claude/worktrees/` の worktree に移動**し、以後 git リダイレクト等も含めて隔離を強制。既に linked worktree にいる場合はスキップ。`worktree.bgIsolation: "none"` で無効化。サブエージェントは `isolation: worktree`。agent teams の teammate は隔離されない(ファイル分割で回避せよ、と公式)。出典: [worktrees](https://code.claude.com/docs/en/worktrees)、[agent view](https://code.claude.com/docs/en/agent-view#how-file-edits-are-isolated)。
  - 注意: background session は「worktree で変更したら commit・push・draft PR まで自発的にやる(main 直 push・force push・merge はしない)。ただし CLAUDE.md 等に git は自分でやると書けばそれに従う」という既定動作を持つ。チーム側の git 規律と衝突しないよう CLAUDE.md で明示すべき。
- **権限モード**: `manual`/`default`、`acceptEdits`、`plan`、`auto`(classifier が各アクションと、送信する `SendMessage` を配送前に審査)、`dontAsk`、`bypassPermissions`。`--restricted`、sandbox。出典: [permission modes](https://code.claude.com/docs/en/permission-modes)、`claude --help`。無人チームでは auto か bypass になるが、**bypass と非 bypass が混在すると cross-session メッセージが hold される**(§2)ので、チーム内は同一クラスに揃えるか `crossSessionInbound: accept` を明示。
- **コスト/使用量**:
  - `-p --output-format json` と SDK の result に `total_cost_usd` とモデル別内訳(クライアント側見積もり、resume すると会話全体の累計)([headless](https://code.claude.com/docs/en/headless)、[SDK cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking))。`--max-budget-usd` でシフト単位の上限(`-p` のみ)。
  - 対話/background には `/usage`(サブスクではプラン枠バーが主、ドル額は参考値)、status line に cost を出せる([costs](https://code.claude.com/docs/en/costs)、[statusline](https://code.claude.com/docs/en/statusline))。
  - OpenTelemetry で metrics/logs/traces を外部コレクタへ(`CLAUDE_CODE_ENABLE_TELEMETRY=1` 等、traces は beta フラグ)([monitoring](https://code.claude.com/docs/en/monitoring-usage)、[SDK hosting](https://code.claude.com/docs/en/agent-sdk/hosting#observability))。**チーム/ロール単位のコスト集計は OTel の resource attributes か自前集計が必要**。
  - background session は並列数に比例してサブスク枠を消費する、と agent view の Limitations に明記。
- **transcript**: `~/.claude/projects/<project>/<sessionId>.jsonl`。サブエージェント分は `.../<sessionId>/subagents/agent-<id>.jsonl` と文書化されているが、このマシンでは未確認(§3)。`cleanupPeriodDays`(既定 30 日)で削除 → **長期の監査ログは `SessionEnd` フックで記録フォルダにアーカイブすべき**。フックと status line は `transcript_path` を受け取る。`/export` で人間向けテキスト。出典: [sessions](https://code.claude.com/docs/en/sessions)、[sub-agents](https://code.claude.com/docs/en/sub-agents#resume-subagents)。
- **スーパーバイザ(PM/人間)がメンバーを見る方法**: 人間 → `claude agents`(一覧・peek・返信・attach・PR 番号表示)、`claude logs <id>`、Remote Control/モバイル。PM(エージェント)→ `claude agents --json --all` をポーリング(公式に「別の Claude セッションが background 作業を監督する用途」として挙げられている)、`notify_when_idle`、transcript JSONL を読む、Notification フック。

---

## 7. 能力マトリクス(要件 → ネイティブ機構 → 成熟度・制約 → 出典)

| 要件 | ネイティブ機構 | 成熟度 / 制約 | 出典 |
|---|---|---|---|
| ロール定義(プロンプト・ツール・モデル・権限) | `.claude/agents/*.md`、`--agent`、`--agents <json>` | **GA 相当・安定**。teammate として使うと `skills` は適用されない等の差異あり | [sub-agents](https://code.claude.com/docs/en/sub-agents)、[agent teams](https://code.claude.com/docs/en/agent-teams#use-subagent-definitions-for-teammates) |
| ロール別の常設セッション(席) | `claude --bg --name … --agent …`(background session / agent view) | **research preview**。finished/waiting のまま未 attach で約 1h 経つとプロセス停止。working(`/loop`・Monitor・subagent 動作中)と pin 済みは停止対象外。pin は TUI 限定でスクリプトから設定できず、メモリ逼迫時は pin 済みも止まり得る。ローカルのみ(マシン停止で止まる) | [agent view](https://code.claude.com/docs/en/agent-view) |
| 使い捨てシフト | `claude -p`(`--session-id`, `--max-budget-usd`, `--permission-prompts none`)、`--bg` 新規起動、SDK | `-p` は安定。実行中も inbox を bind する(bare を除く)。`--bare` が将来 `-p` の既定になる予定(サブスク認証不可・inbox なし) | [headless](https://code.claude.com/docs/en/headless) |
| セッション再開 | `--resume <id>`/`--continue`/`--fork-session`、SDK `resume`/`fork`、`SessionStore` | 安定。resume はキャッシュ切れで高コスト | [sessions](https://code.claude.com/docs/en/sessions)、[SDK sessions](https://code.claude.com/docs/en/agent-sdk/sessions) |
| エージェント間メッセージ | `SendMessage`/`ListAgents`(cross-session) | v2.1.224+ でデフォルト有効。プレーンテキスト、非 durable、inbound 制御と権限モードで hold され得る | [cross-session](https://code.claude.com/docs/en/cross-session-messaging) |
| idle メンバーを起こす | 生きている idle セッションへの SendMessage、`asyncRewake` フック、Channels。席を生かし続けるには `/loop`・Monitor(working 扱い)か pin を使う | **生きているプロセス限定**。keep-alive には使用量・再アーム(Monitor 30 分)・7 日失効(`/loop`)のコストがかかる | 同上、[hooks](https://code.claude.com/docs/en/hooks)、[channels](https://code.claude.com/docs/en/channels) |
| 止まっているメンバーを起こす | `claude --resume <id> --bg "<msg>"`、agent view の返信 | CLI で可能だが「送信」専用コマンドはない。SendMessage では不可 | [agent view](https://code.claude.com/docs/en/agent-view) |
| 完了待ち(PM が待つ) | `notify_when_idle`、`claude agents --json`、Notification フック(`agent_completed`) | notify はメイン会話のみ・同一マシンのみ・12h・1 回 | [cross-session](https://code.claude.com/docs/en/cross-session-messaging#get-a-notice-when-another-session-goes-idle) |
| 共有タスクボード | agent teams の共有タスクリスト(`~/.claude/tasks/{team}/`、resume 後も残る) | **experimental**、1 セッションのチームに閉じる(team 名はセッション ID 由来)、状態遅延あり | [agent teams](https://code.claude.com/docs/en/agent-teams) |
| ハブ型分解・割当・回収 | agent teams のリード / サブエージェント / dynamic workflows | teams は experimental・リード固定・resume 不可。workflows は 1 回のバッチ向け | [agent teams](https://code.claude.com/docs/en/agent-teams)、[workflows](https://code.claude.com/docs/en/workflows) |
| 長期・日跨ぎのチーム | **Projects**(クラウド、スレッド = cloud session、project memory 共有) | **public beta、Pro/Max のみ**、GitHub repo とアップロードファイル限定、ローカルツール不可 | [projects](https://code.claude.com/docs/en/claude-projects) |
| ロール別メモリ | subagent `memory:`、auto memory、`autoMemoryDirectory` | 安定。先頭 200 行/25KB、マシンローカル | [sub-agents](https://code.claude.com/docs/en/sub-agents#enable-persistent-memory)、[memory](https://code.claude.com/docs/en/memory) |
| 記録ストア(ボード/ジャーナル/ハンドオフ/決定ログ) | **なし**(CLAUDE.md/memory は近いが用途が違う) | 自作 | — |
| シフト開始・終了の自動化 | `SessionStart` / `Stop` / `SessionEnd` / `PreCompact` フック | 安定 | [hooks](https://code.claude.com/docs/en/hooks) |
| 定期起動 / ポーリング wake | `/loop`・Cron(session、7 日失効、background 化で引き継ぎ)、**Desktop scheduled tasks**(ローカル・永続・最短 1 分・毎回新規セッション)、Routines(cloud) | Desktop は Desktop アプリ起動中かつマシン稼働中のみ。Routines は research preview・最短 1h・日次上限 | [scheduled tasks](https://code.claude.com/docs/en/scheduled-tasks)、[desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)、[routines](https://code.claude.com/docs/en/routines) |
| 外部イベントで起動 | Routines API トリガ / GitHub トリガ、Channels(稼働中セッションへ push) | どちらも research preview | 同上、[channels](https://code.claude.com/docs/en/channels) |
| 人間 = チームメンバー(配送) | agent view 返信、Remote Control/モバイル、Channels(Telegram 等、権限中継つき)、`PushNotification`、`--brief`(SendUserMessage)、`AskUserQuestion` | 部分的。**「人間宛てメッセージを durable に待つ受け箱」はない** | [remote control](https://code.claude.com/docs/en/remote-control)、[channels reference](https://code.claude.com/docs/en/channels-reference)、`claude --help`(`--brief`) |
| エスカレーション方針(チーム別) | なし(auto mode classifier / 権限ルールは「何を実行してよいか」であって「何を人間に上げるか」ではない) | 自作 | [permission modes](https://code.claude.com/docs/en/permission-modes) |
| 覗く・割り込む | `claude agents`/`attach`/`logs`、teams の分割ペイン、subagent パネル、Remote Control | background session なら十分。`-p` は実行中 attach 不可(メッセージは受けられる) | [agent view](https://code.claude.com/docs/en/agent-view) |
| ファイル競合回避 | `--worktree`、background の自動 worktree、subagent `isolation: worktree` | 安定。teams は隔離なし | [worktrees](https://code.claude.com/docs/en/worktrees) |
| コスト把握 | `total_cost_usd`、`--max-budget-usd`、`/usage`、OTel | 見積もり値。チーム/ロール集計は自作 | [costs](https://code.claude.com/docs/en/costs)、[monitoring](https://code.claude.com/docs/en/monitoring-usage) |
| 監査ログ | transcript JSONL(30 日で削除)、`SessionEnd` でアーカイブ | 長期保管は自作 | [sessions](https://code.claude.com/docs/en/sessions) |

---

## 8. 推奨アーキテクチャ・スケッチ

### 土台にするネイティブ機構

1. **ロール = `.claude/agents/<role>.md`**(チームフォルダ内に置き `--add-dir` か project scope で読ませる)。`memory: project` でロール別メモリ。
2. **席 = 名前付き background session**: `claude --bg --name <team>.<role> --agent <role> --settings <team-settings.json> "<boot prompt>"`。人間の可視性・介入は agent view に丸投げ。
   - `team-settings.json` には最低限 `crossSessionInbound: "accept"`、チーム共通の permission mode / allow ルール、フック(下記)、必要なら `autoMemoryDirectory`。
3. **PM ⇄ メンバーの会話 = `SendMessage`**(生存中)、完了待ち = `notify_when_idle` + `claude agents --json`。
4. **シフト境界 = フック**: `SessionStart` で記録から水和、`Stop` で「ハンドオフ/ジャーナル記入済みか」を検査(未記入なら exit 2)、`SessionEnd` で transcript をアーカイブ、`PreCompact` でジャーナルに中間メモを吐かせる。
5. **ファイル隔離 = background session の自動 worktree**(実装系ロール)。調査系ロールは隔離不要。
6. **サブエージェント = ロール内部の下請け**(implementer が test-runner を使う、researcher が fetch/verify を並列化する等)。

### 自作が必要な薄い層

- **(a) 記録ストア**(チームフォルダ、任意で git 管理): `board.md|json`(タスク・担当・状態・依存)、`roles/<role>/journal.md`・`handoff.md`、`decisions.md`、`escalations/`(人間宛て未決事項)、`roster.json`(ロール → 現在の sessionId/短縮 ID/状態)。**正本はここ**。Claude 側の state(`~/.claude/jobs`、teams、tasks)は非公開 I/F かつ揮発なので正本にしない。
- **(b) wake-on-message(配送層)**: `send(team, role, msg)` を 1 本用意する。
  1. 記録ストアの inbox に**まず永続化**する(Claude のメッセージングは durable でないため)。
  2. `claude agents --json --all` と `roster.json` を照合し、該当席のプロセスが生きていれば PM セッションから `SendMessage` する。あるいは、席の側に `asyncRewake` フックか Monitor を置き、自分の inbox を監視させて自分で起きる形でもよい(wire format に依存しない正規経路)。
  3. 止まっていれば `claude --resume <sessionId> --bg "inbox に新着あり: …"`、席がなければ新規シフトを `--bg` 起動する。
  4. **ネイティブで代替できる部分とその評価**:
     - **常駐席の keep-alive**: `/loop` か Monitor で working 状態を保つか、pin する(§2)。ステップ 2 が当たる確率を上げる手段であって、ステップ 1・3 を不要にはしない。
     - **Desktop scheduled task による 1 分ポーリング**: ステップ 3 の「止まっている席を起こす」をスケジューラに任せる手段。CLI だけで完結しない(Desktop アプリ常駐が前提)、空振りでも毎回セッションが起動する、Desktop セッションは SendMessage を送受信できない、という弱点がある。「PM とメンバー数人のチームで、即時性より簡素さを優先する」なら、`send` を「inbox への永続化 + 生きていれば SendMessage」だけにして、止まった席の起動は Desktop タスクのポーリングに任せる構成も成立する。**推奨は自前の `send` 内で resume/起動まで行う方**。こちらは CLI だけで完結し、遅延がなく、空振りのコストもない。
  - 人間 = チームメンバーなので、宛先が human なら同じ inbox に置き、通知だけ別経路(PushNotification / Channels / Slack)にする。
- **(c) PM ディスパッチループ**: PM は常設席として、起動時にボードを読み、分解・割当(ボード更新 + send)、`notify_when_idle` でメンバーの完了を待ち、成果物を回収して次を割り当てる。
  - **PM の常駐方法**: pin は TUI 操作でしか設定できない(スクリプト化不可)。なので、無人で起動するなら PM 自身に Monitor(inbox の watch、30 分ごとに再アーム)か `/loop`(ボード見直しのティック、7 日失効)を持たせて working 状態を保つのが現実的。人間が agent view から pin するのは補助として使う。いずれの方法でも、メモリ逼迫・マシン再起動・supervisor の再起動では止まり得るので、PM も (b) の fallback で起こせるようにしておく。
  - ループそのものは Claude のターンで回し、**状態はすべてボードに書く**(PM のセッション自体も使い捨てにできるように)。`/loop` の 7 日失効は、PM シフトが 7 日以内に交代すれば問題にならない。
- **(d) 人間エスカレーション**: チームごとの policy ファイル(例: 「設計の根幹」「破壊的操作」「予算超過」は human 宛て)。メンバー/PM はそれに該当したら `escalations/` に書き、human 宛て send。権限プロンプトは Claude Code 本体のもの(agent view / Channels の権限中継)で別途上がってくる、という二系統になることを設計で明示する。
- **(e) コスト/監査の集計**: シフトごとに `total_cost_usd`(`-p`)や OTel をロール/チームタグ付きで集計、`SessionEnd` で transcript を記録フォルダへコピー。

### 採らない方がよいもの(現時点)

- **Agent teams を土台にする**: 常設・シフト交代・resume と両立しない(§3 の制限)。「1 セッション内の短期スプリント」をチーム内部の手段として使うのは可。
- **SDK でのサーバ化**: 規約上サブスク認証を前提にできない可能性が高い(§1)。個人ツールなら CLI 駆動の方が筋がいい。
- **`~/.claude/jobs`・`~/.claude/teams`・inbox ソケット wire format への直接依存**: いずれも公式に「安定 I/F ではない / 未文書化」。

---

## 9. リスク・不安定要素

| 項目 | 状態 | 影響 |
|---|---|---|
| Agent view / background sessions | **research preview**(「interface and shortcuts may change」) | 席の土台がこれ。フラグや 1h 停止ルールが変わり得る。`claude agents --json` だけを I/F にして吸収する |
| Agent teams | **experimental, disabled by default** | 土台にしない |
| Routines | **research preview**、日次 run 上限、最短 1h | クラウド側 wake に使うなら上限が効く |
| Channels | **research preview**、claude.ai 認証 or Console API キー必須 | 人間配送に使うなら変更リスク |
| Projects | **public beta**(Pro/Max、段階展開) | クラウド完結なら有力な代替だがローカル不可 |
| Dynamic workflows | ページに成熟度表記なし(未確認) | バッチ用途なので影響小 |
| Cross-session messaging | デフォルト有効、ただし仕様変更が頻繁(v2.1.224〜2.1.271 で注記多数) | 配送層の抽象化で吸収 |
| `-p` のデフォルトが `--bare` に変わる予定 | 公式に予告 | bare だとサブスク認証不可・inbox なし・フック/CLAUDE.md 読まない → `-p` シフトは明示フラグで守る |
| finished/waiting のまま未 attach で約 1h 経つと background process 停止 | 仕様 | 停止中は SendMessage 不可 → (b) の fallback 必須。keep-alive(`/loop`・Monitor・pin)で停止を減らせるが、pin はスクリプトから設定できず、メモリ逼迫時は pin 済みも止まる |
| Desktop scheduled tasks | 永続だが Desktop アプリ依存。スリープ中は skip し、復帰時に 1 回だけ catch-up | ポーリング wake に使うなら「アプリ常駐・マシン稼働」が前提条件になる |
| CronCreate の永続性 | ツール定義(durable 無効)と docs(feature-flag fetching オフ時は `.claude/scheduled_tasks.json` に保存)が条件付きで食い違う | 当てにしない。永続スケジュールは Desktop tasks / Routines / 自前ストアで持つ |
| bypass/非 bypass 混在時のメッセージ hold | 仕様 | 無人チームで詰まる典型。`crossSessionInbound: accept` を明示 |
| transcript 30 日削除 | 仕様 | 監査はアーカイブ必須 |
| SDK の認証ポリシー | 公式注記 | SDK 採用時は API キー課金前提 |
| 使用量 | background も teammate もサブスク枠を並列数倍で消費 | ロール数 × シフト頻度で枠を見積もる |

---

## 10. 確認できなかったこと / 不確実な点

- **supervisor に停止されたローカル background セッションが、SendMessage(RC 経由の offline 宛て)で自動再起動されるか**: ドキュメントに記述なし、実機でも検証していない(他の稼働中セッションを巻き込むため試験を控えた)。本報告では「起こせない前提で fallback を持つ」とした。
- **inbox ソケットのメッセージ本体フォーマット**: 未文書化。
- **keep-alive の実効性**: 「`/loop` や Monitor を回している background 席は supervisor に止められない」は、docs の working の定義(subagent・workflow・monitor が動作中、`/loop` の合間)からの推論。1 時間を超えて放置する実測はしていない。Monitor の再アーム(30 分ごと)の間にターンが途切れた瞬間に idle 判定されるか、といった境界の挙動も未確認。
- **subagent transcript のパス**: sub-agents ページに `~/.claude/projects/{project}/{sessionId}/subagents/agent-{agentId}.jsonl` と文書化されているが、このマシンでは該当ファイルを見つけられなかった。
- **Desktop scheduled tasks**: ドキュメントのみに依拠しており、実機(Desktop アプリ)では試していない。
- **Agent SDK を個人がサブスク認証で使ってよいか**: 公式注記は「third party developers … for their products」についての制限で、個人利用の扱いは明文化されていない。規約解釈なので要確認。
- **Dynamic workflows の成熟度表記**: 取得したページ本文には preview 表記が見当たらなかったが、断定はしない。
- **二次情報の独立性**: 検索で見つかった解説記事(alexop.dev、prodfeat.ai、各種 cross-session 解説ブログ)はほぼ公式ドキュメントの要約で、独立した実測や反証は見つからなかった。本報告の主張は基本的に公式ドキュメントと実機出力に依拠している。

---

## 出典一覧

公式ドキュメント(2026-09-25 取得):
- Run agents in parallel: https://code.claude.com/docs/en/agents
- Agent view / background sessions: https://code.claude.com/docs/en/agent-view
- Agent teams: https://code.claude.com/docs/en/agent-teams
- Cross-session messaging: https://code.claude.com/docs/en/cross-session-messaging
- Subagents: https://code.claude.com/docs/en/sub-agents
- Dynamic workflows: https://code.claude.com/docs/en/workflows
- Projects: https://code.claude.com/docs/en/claude-projects
- Worktrees: https://code.claude.com/docs/en/worktrees
- Headless (`claude -p`): https://code.claude.com/docs/en/headless
- Sessions: https://code.claude.com/docs/en/sessions
- Scheduled tasks (`/loop`, cron): https://code.claude.com/docs/en/scheduled-tasks
- Routines: https://code.claude.com/docs/en/routines
- Desktop scheduled tasks: https://code.claude.com/docs/en/desktop-scheduled-tasks
- Channels / Channels reference: https://code.claude.com/docs/en/channels , https://code.claude.com/docs/en/channels-reference
- Remote Control: https://code.claude.com/docs/en/remote-control
- Hooks reference: https://code.claude.com/docs/en/hooks
- Memory: https://code.claude.com/docs/en/memory
- Context window / compaction: https://code.claude.com/docs/en/context-window
- Model config: https://code.claude.com/docs/en/model-config
- Permission modes: https://code.claude.com/docs/en/permission-modes
- Costs: https://code.claude.com/docs/en/costs
- Monitoring (OTel): https://code.claude.com/docs/en/monitoring-usage
- Env vars: https://code.claude.com/docs/en/env-vars
- Agent SDK overview / sessions / streaming input / session storage / hosting / cost tracking: https://code.claude.com/docs/en/agent-sdk/overview , https://code.claude.com/docs/en/agent-sdk/sessions , https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode , https://code.claude.com/docs/en/agent-sdk/session-storage , https://code.claude.com/docs/en/agent-sdk/hosting , https://code.claude.com/docs/en/agent-sdk/cost-tracking

実機証拠(このマシン、2026-09-25):
- `claude --version` → 2.1.282。`claude --help` と `claude agents|attach|logs|stop|respawn|rm|project --help` の出力。
- `claude agents --json` の出力(background/interactive の混在、pid なし = プロセス停止済み background の存在)。
- この調査セッションで `ListAgents` を実行した結果(自セッション名 + ローカル/Remote Control/cloud の peer 一覧、停止中 background が RC `offline` として見えること)。
- この調査セッションの Bash 環境に `CLAUDE_CODE_MESSAGING_SOCKET` が export されていること。
- この調査セッションが持つツール定義の記述(SendMessage、CronCreate の「durable は効果なし、7 日失効」、Monitor の 30 分上限、FetchInboxMessage、RemoteTrigger)。

二次情報(公式の要約が中心で、独立性は低い):
- https://alexop.dev/posts/from-tasks-to-swarms-agent-teams-in-claude-code/
- https://www.prodfeat.ai/en/blog/2026-02-25-claude-code-agent-teams
- https://thepromptshelf.dev/blog/claude-code-cross-session-messaging-guide-2026/

---

# fact-check 結果(fact-checker, 2026-09-25)

**判定: changes-requested**(ブロッキング 2 件 + 軽微な正確性の指摘数件)。全体としてはよく裏取りされている。主要な主張の 9 割以上は公式ドキュメント(`https://code.claude.com/docs/en/*.md` を直接取得)と `claude --help`(2.1.282)で確認できた。差し戻しの理由は、アーキテクチャの結論(§0-2/§0-4/§0-5、§8(b)(c))を左右する 2 つの観点が抜けていること。

## 確認できたもの(抜粋)
cross-session messaging の各仕様(v2.1.224+、idle なら新ターン開始、inbox socket を bind したセッションだけが一覧に出る、accept/hold/refuse と permission-mode クラス、キュー 50 件・hold 100 件、約 100 万文字、プレーンテキスト、`notify_when_idle` はメイン会話のみ・同一マシン・12h、bare は socket を bind しない、`-p` ワーカーは `--settings` で accept 推奨)/ agent view(research preview、約 1h で停止、Ctrl+T で pin、`--resume <id> --bg` は v2.1.257+、返信が届かなければ次回プロンプトとして保存、`claude agents --json` が公式に supported、`~/.claude/jobs` は非安定、`--agent` は resume 時に復元、worktree 自動移動・`bgIsolation`・draft PR まで自発的に作る既定動作、Notification `agent_needs_input/agent_completed` は v2.1.198)/ agent teams の Limitations 全項目 / subagent の `memory:`(200 行/25KB、auto memory を切ると無効)、同時 20・深さ 3 / scheduled tasks(7 日失効、最大 50 件、ジッタ、catch-up なし)/ Routines(research preview、最短 1h、日次上限、API・GitHub トリガ、self-hosted env)/ headless(`--bare` が将来 `-p` の既定になる、SIGTERM で exit 143 と SessionEnd、背景 subagent を 10 分待つ、`--forward-subagent-text`、`total_cost_usd`)/ SDK overview の認証注記(引用は正確)/ hooks(`asyncRewake`、SessionStart の matcher と `agent_type`、resume 時の `estimated_cache_write_usd`、Stop の `background_tasks`/`session_crons`)/ 1M context(Opus 4.7 以降は全プラン)、compaction 後に再読込されるファイルは最大 5 本、AGENTS.md は v2.1.277+、import は 4 hops / Projects は public beta で Pro/Max のみ。

## ブロッキング

**B1. supervisor の 1h 停止ルールの説明が片面的で、「wake 層を自作する前提」の根拠が弱い(§0-2、§1 の表、§2「止まったセッションは起こせない」、§8(b)(c)「PM は pin」)**
- agent view の「The supervisor process」節によると、止められるのは *finished / waiting で未 attach* のセッションだけ。*working* 扱いのセッションは止められず、そこに「A running subagent, workflow, or **monitor** counts as working」と明記されている。さらに `claude agents --json` の `working` 状態の定義に「a `/loop` iteration」のように「自分で回している作業の合間」も含まれる(agent-view.md「read session state」表)。
  → つまり、**Monitor で inbox を watch している席や `/loop` を回している席は、pin しなくてもプロセスが生き続け、SendMessage で届く可能性が高い**。これはネイティブ機構だけで組める「常駐 + wake-on-message」の有力な選択肢で、推奨アーキテクチャ(b) の fallback 設計の前提(席はいずれ止まる)を変える。調査報告として、使えるかどうかの評価(Monitor の 30 分上限と再アームのコスト、`/loop` の 7 日失効、使用量)を加えるべき。
- 「PM は pin 推奨」について。pin は agent view の TUI キー(`Ctrl+T`)としてしか文書化されておらず、`claude agents --help` にも `--bg` にも pin フラグはない。つまり**スクリプトからは pin できない**。加えて「メモリが逼迫すると supervisor は pin 済みの idle セッションも止める」(agent-view.md 861 行付近)。この 2 点を書かずに pin を前提にした設計は誤解を招く。
- 細かい点: 外部から台帳を作るなら `claude agents --json --all` を使う必要がある(`--all` を付けないと終了済みセッションが出ない。docs は「Poll `claude agents --json --all`」と明記)。§1 と §8(b) で `--all` に触れていない。

**B2. スケジューリングの結論が Desktop のローカル定期タスクを落としている(§0-4、§4 の表、§4「ローカルに永続的な wake の組み込みはない」)**
- §0-4 はスケジュール系を「session スコープか cloud」の 2 つにまとめているが、scheduled-tasks と desktop-scheduled-tasks の比較表には 3 つ目の選択肢がある。Desktop scheduled tasks は **ローカル実行・再起動をまたいで永続・最短 1 分間隔・ローカルファイルにアクセス可・毎回新規セッションを起動・タスクごとに permission mode を設定可・スリープで逃した分は 1 回だけ catch-up・worktree 隔離トグルあり**。制約は「Desktop アプリが起動中でマシンが起きていること」と「Desktop のセッション間メッセージは送受信できない」。
  → 「1 分ごとに記録ストアの inbox を見て、新着があればシフトを起動する」というポーリング型のローカル wake がネイティブで組めるので、§0-4・§4 の結論と §8(b) の比較対象に入れるべき(採らない理由を書くのでも構わない)。§4 の表の「Desktop アプリ前提 / 時刻起動のみ」だけでは不十分。
- 同じ段落の細部: 「CronCreate は durable 不可・ディスクに書かない」は常に正しいわけではない。scheduled-tasks の Limitations に「feature-flag fetching を切っている場合、セッションをまたいで残したいタスクは `.claude/scheduled_tasks.json` に保存される」とある。ツール定義(このセッション)と公式ドキュメントで条件付きで食い違っていることを書き添えるべき。あわせて「セッションを background 化すると `/loop` タスクは background session に引き継がれる」(同 Limitations、agent-view 409 行付近)も、PM を常設席にする設計に直接効くので記載を推奨。

## 非ブロッキング(正確性・出典の nit)
- **N1 (§2 配達保証)**: 「bypass 系は hold(承認ダイアログ、`dialogExpiry` 既定 5 分で破棄)」は background セッションには当てはまらない。docs には「端末が attach されていない background セッションでは、ダイアログは期限を過ぎても開いたまま」とある。`-p` 向けには `dialogExpiry: "never"` もある。無人チームの挙動に関わるので正確に書くこと。
- **N2 (§2 最終段落 / §6)**: 「auto mode では classifier が**送受信**メッセージを検査」とあるが、permission-modes.md(280 行付近)に書かれているのは「Claude が `SendMessage` で**送る**各メッセージを配送前に審査する」で、送信側の話。「受信側で審査」を裏付ける記述は見つからなかった。
- **N3 (§3 寿命・§6 transcript)**: subagent transcript のパス `~/.claude/projects/{project}/{sessionId}/subagents/agent-{id}.jsonl` は、出典として挙げた sub-agents ページにも sessions ページにも見当たらない(sub-agents ページにあるのは「`cleanupPeriodDays`(既定 30 日)で削除」だけ)。このマシンの `~/.claude/projects` 配下にも `subagents` パスは 1 件もなかった。「未文書化・未検証」と明記するか、出典を差し替えること。
- **N4 (§2 inbox socket)**: 「外部スクリプトから投函する設計は非公式 I/F 依存」とあるが、docs は「hook や Bash から**自セッションの** socket に投函する」ことを own-child messages として明示的に文書化している(inbound 既定でも配送され、macOS ではプロセス終了後は token で検証する)。本文の wire format が未文書化なのは正しい。ただ「自セッション宛ての投函は公式に想定された用途」という区別を書くと、asyncRewake 案との比較がしやすくなる。
- **N5 (§1 / §9)**: 無人の `-p` シフトには `--permission-prompts none`(v2.1.259+、承認が必要な操作は待たずに deny し、AskUserQuestion を除去する)が直接効くのに言及がない。§1 の `-p` 行に「実行中は覗けない」とあるが、`-p` セッションも inbox socket を bind し一覧に出てメッセージを受けられる(cross-session「Non-interactive sessions」)。§1 にも一言あると、第二候補の評価が公平になる。
- **N6 (§3 agent teams)**: 「チーム設定はセッション終了で削除」は正しいが、タスクリスト(`~/.claude/tasks/{team}/`)は残り、resume 後も保持される。team 名は `session-<id 先頭 8 文字>` で自動生成される。「共有タスクボードとして流用できるか」の評価に関わる。
- **N7 (二次情報)**: alexop.dev / prodfeat.ai / thepromptshelf.dev は独立性が低いと報告内で自己申告済み。結論はどれにも依存していないので問題ないが、出典一覧から外しても情報は失われない。

## 研究者への依頼(要約)
1. B1: keep-alive の条件(working の定義、monitor・`/loop`)、pin が TUI 限定でメモリ逼迫時には効かないこと、`--json --all` を反映し、§0-2・§8(b)(c) の wake 設計を見直す。
2. B2: Desktop scheduled tasks をローカル wake の候補として §0-4・§4・§8(b) に加え、CronCreate の durable の条件付き挙動と `/loop` の background 引き継ぎを追記する。
3. N1〜N6 を修正する(本文の書き換えは研究者側で)。

---

# 研究者による改訂(iteration 2, 2026-09-25)

fact-check の指摘を本文に反映した。反映箇所は次のとおり。

- **B1**: keep-alive の条件を §0-2・§1 の表と根拠・§2(新項目「席を止めさせないネイティブの手段」)・§7・§8(b)(c)・§9 に反映した。条件は、working の定義(`/loop` の合間、subagent・workflow・Monitor が動作中)、ダイアログ待ち、pin。pin は TUI 限定でスクリプト不可、メモリ逼迫時は pin 済みも停止されることも明記した。§8(c) は「PM は pin 推奨」から「PM 自身に Monitor か `/loop` を持たせて working を保ち、pin は人手の補助」に変更した。`claude agents --json --all` を §0-5・§1・§6・§8(b) に反映した。**結論は「fallback 層は依然必要」で変わらないが、常駐席でネイティブに wake できる範囲は広がった。**
- **B2**: Desktop scheduled tasks をローカルのポーリング wake 候補として §0-4・§4(表と結論)・§7・§8(b)・§9 に追加し、採否の比較を載せた。推奨は自前の `send` で resume/起動まで行う方で、Desktop 1 分ポーリングは簡素構成の代替として記載した。CronCreate の永続性について、ツール定義と docs の条件付きの食い違い(`.claude/scheduled_tasks.json`)と、`/loop` の background 引き継ぎを §4 と §9 に追記した。
- **N1**: hold 時の挙動をセッション種別ごとに書き分けた。未 attach の background ではダイアログが開いたまま残り、メッセージは黙って滞留する。`-p` では `dialogExpiry: "never"` を使える(§2)。
- **N2**: classifier が審査するのは送信側の `SendMessage` であり、受信側で審査するという記述は見つからなかった旨に修正した(§2・§6)。
- **N3**: **指摘の一部に異議あり**。subagent transcript のパスは sub-agents ページの「Resume subagents」節に文書化されている(「find IDs in the transcript files at `~/.claude/projects/{project}/{sessionId}/subagents/`. Each transcript is stored as `agent-{agentId}.jsonl`」)。ただし実機で見つからないのは fact-checker の報告どおりで、私の探索でも見つからなかった。そこで「文書化済み・実機では未確認・I/F として当てにしない」と書き換えた(§3・§6・§10)。
- **N4**: 自セッション宛て(own-child、公式に想定された用途)と他セッション宛て(非公式 I/F 依存)を区別して書いた(§2)。
- **N5**: `--permission-prompts none`(v2.1.259+)を §1 に追記した。`-p` セッションも inbox を bind して実行中にメッセージを受けられる点を、§1 の表と §7 に反映した。
- **N6**: agent teams のタスクリストが resume 後も残ること、team 名がセッション ID 由来であること、共有ボードとしての流用は勧めないことを §3・§7 に追記した。
- **N7**: 二次情報はすでに「独立性が低い」と明記しており、結論はどれにも依存していない。出典一覧に残すが、参考扱いとする。

追加で確認した一次資料: [agent view「The supervisor process」/ troubleshooting](https://code.claude.com/docs/en/agent-view)、[desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)(比較表・Missed runs・Permissions)、[scheduled tasks Limitations](https://code.claude.com/docs/en/scheduled-tasks#limitations)、[headless「Turn off permission prompts in unattended runs」](https://code.claude.com/docs/en/headless)、[CLI reference `--permission-prompts`](https://code.claude.com/docs/en/cli-reference)、[permission modes](https://code.claude.com/docs/en/permission-modes)(SendMessage 審査は v2.1.222+)。

---

# fact-check 結果(iteration 2, fact-checker, 2026-09-25)

**判定: approved**

- **B1・B2**: 反映を確認した(§0-2/§0-4/§0-5、§1 の表、§2 の keep-alive 項、§4 の表と結論、§7、§8(b)(c)、§9)。working の定義、pin が TUI 限定でメモリ逼迫時は効かないこと、`--json --all`、Desktop scheduled tasks の条件と弱点、CronCreate の永続性の食い違い、`/loop` の background 引き継ぎは、どれも docs の記述と一致する。keep-alive が 1 時間を超えて効くかは推論であり、§10 で未実測と明記されている。この扱いで妥当。
- **N3 は私の誤り**。sub-agents.md の「Resume subagents」節(1110 行付近)に `~/.claude/projects/{project}/{sessionId}/subagents/` と `agent-{agentId}.jsonl` が明記されている。研究者の異議は正しく、「文書化済み・実機では未確認」という書き換えで適切。
- **N1・N2・N4・N5・N6**: 反映を確認した。SendMessage を classifier が審査するのが v2.1.222+ である点も、permission-modes.md で確認した。
- **N7**: 研究者の判断(参考扱いで残す)で問題ない。
