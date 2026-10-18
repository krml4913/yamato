# 移行手順 (migration)

v1.0.0 以降の変更のうち、**既存の艦・利用者が何かをしないと動かなくなる (または挙動が変わる) もの**の手順をここに集める。tag を打つときに「未リリース」の見出しを版名 (`## v1.2.0 → v1.3.0` など) に改め、その上に空の「未リリース」を作り直す。

## 移行手順が要る変更の種類
- team.yaml の形・既定値 (キーの追加・改名・削除、既定値の変更)
- 艦フォルダの記録の形式 (board・inbox・decisions・memory・events・roster・usage など)
- コマンドの名前・引数
- ひな形 (`roles/*.md`・team.yaml) の変更で、既存の艦の写しに反映が要るもの
- settings・hooks・permission 規則
- Python の下限・依存

内部の修正・テストだけの変更 (既存の艦が何もしなくてよいもの) は書かなくてよい。PR の本文には「移行手順: あり / なし」を書く。

## 項目の書き方
各項目に次を書く。
- 何が変わったか
- 既存の艦・利用者がやること (コマンド、編集するファイル、`yamato up` のやり直しが要るか)
- やらないと何が起きるか
- 該当 PR

## 未リリース

## v1.5.0 → v1.6.0

### 全体の手順
v1.6.0 は **web console の見た目と操作を直し、fleet の API に稼働 (running) を足す版**。team.yaml・艦フォルダの記録の形式・コマンドの名前は変わらない。既存の艦が必ずやることはない。

**移行不要**: #184 は内部の整理 (色の変数) とテストで、艦がやることはない。

手順:
1. 艦を止める: `yamato down <艦>`
2. yamato を更新する: `git -C ~/dev/yamato pull` (tag を使うなら `git -C ~/dev/yamato checkout v1.6.0`)
3. 艦を起こす: `yamato up <艦>`

### web console の見た目と操作が変わった (利用者向け)
- 何が変わったか: 席の表 (生存・最終・作業ログの最後・今日の tok) を読みやすくし、狭い幅でも右の列が切れない。艦の枠は広い画面で段組になり、board が空でも左の段が空かない。状態と席の表が後から出てレイアウトが跳ねることも直した。設定と md の画面は、値の書体・役割の表・節の移動・題と見出しの段・目次を整えた。判断の本文は枠の中で十分に見える。編集できるモード (`--edit`) では、席に送る・艦の操作・設定 (生の team.yaml・フォーム・保存バーと差分) を整えた。light モードは背景・線・影・警告色のコントラストと変えた印の位置を直した
- 既存の艦・利用者がやること: なし。dist は同梱で、node や npm は要らない
- やらないと: 何も起きない
- PR: #172・#173・#175・#177・#178・#179・#181・#182・#183・#185・#186・#187・#188・#189

### 艦のページの危ない操作は halt だけになった
- 何が変わったか: web console の艦のページに出る危ない操作を `halt` だけにした。画面からの `down --force` の入口は無くなった。API (`down-force`) は残っていて、`yamato down --force` も変わらない
- 既存の艦・利用者がやること: 画面から強制停止していた場合は、`yamato down --force <艦>` を使うか、API を直接呼ぶ
- やらないと: 画面に強制停止の操作が見つからない。動作は壊れない
- PR: #176

### fleet の API に稼働 (running) が増えた
- 何が変わったか: 艦隊の API (`/api/v1/` の fleet) の各艦に `running` (生きている席が 1 つでもあれば true。フォルダが無い・team.yaml を読めない・`claude agents` を読めないときは null) を足し、画面に稼働の列を出す。あわせてフォルダの省略表示と admiral の表を直した。既存のキーは変わらない
- 既存の艦・利用者がやること: なし。API を機械で読んでいる場合だけ、キーが増えたことを知っておく
- やらないと: 何も起きない
- PR: #180

## v1.4.0 → v1.5.0

### 全体の手順
v1.5.0 は **web console の見た目と流れを直し、`pr open --base`・`pr merge --strategy` と items の API を足す版**。team.yaml・艦フォルダの記録の形式・既存のコマンドの名前は変わらない。既存の艦が必ずやることはない。

**移行不要**: #159・#167・#168 は内部の整理・テスト・docs で、艦がやることはない。

手順:
1. 艦を止める: `yamato down <艦>`
2. yamato を更新する: `git -C ~/dev/yamato pull` (tag を使うなら `git -C ~/dev/yamato checkout v1.5.0`)
3. 艦を起こす: `yamato up <艦>`

### pr open --base と pr merge --strategy
`yamato pr open` に `--base <branch>` (PR の向き先。省略時は `git.base`)、`yamato pr merge` に `--strategy <merge|squash|rebase>` (省略時は `git.strategy`) を足した。省略したときの挙動は変わらない。既存の艦がやることはない。変わるのは、`pr_open` の events に `base` が増えることと、merge 後の衝突の知らせが merge した PR と同じ base の PR だけに出ること (今まで `git.base` 向けの PR だけを見ていた艦は、`git.base` 以外へ merge したときに知らせが出なくなる)。
版を出す手順: tag を打つ前に `cd frontend && npm ci && npm run build` で `src/yamato/web/dist/` を作り直して commit する (develop 向けの PR は dist を変えず、main 向けの CI がこの鮮度を見る)。
- PR: #154・#155

### web console の見た目と流れが変わった (利用者向け)
- 何が変わったか: 艦のページに状態と操作を集め、危ない操作は違いを書き分けて上に置いた。一覧は頭を軽くし、古い異常を畳み、判断ごとに閉じるフォームを出す。判断は本文を画面で読んでから閉じる。設定ページはまとまりと目次つきで、値を主役にした。狭い幅でも崩れない。`/ship/` 以下の画面は拡張子つきの URL でも開ける。これに合わせて `GET /api/v1/ships/{name}/items/{id}` (board の項目 1 件の本文と frontmatter を返す) が増えた。既存の API は変わらない
- 既存の艦・利用者がやること: なし。dist は同梱で、node や npm は要らない
- やらないと: 何も起きない
- PR: #156・#157・#158・#160・#161・#162・#163・#164・#165・#169・#170

### halt で止めた headless の席は強制停止として記録される
- 何が変わったか: `yamato halt` で止めた headless の席の outcome が強制停止になり、日報に二重に載らない。`down --force` と同じ扱いに揃えた
- 既存の艦がやること: なし。halt の後の events・日報を機械で読んでいる場合だけ、`force_stop` の扱いが `down --force` と同じになったことを知っておく
- やらないと: 何も起きない
- PR: #166

## v1.3.0 → v1.4.0

### 全体の手順
v1.4.0 は **web console (dashboard の SPA 化と、画面からの書き込み・操作) を足す版**。team.yaml・艦フォルダの記録の形式・コマンドの名前は変わらない。既存の艦が必ずやることはない。

**既存の艦の写しに反映するもの (任意)**: `roles/impl.md`・`roles/reviewer.md` の「rebase して push」の言い回しと、ひな形の文の番号の除去。`yamato ship upgrade` で写せる。

**移行不要**: #123〜#128・#135・#138・#142・#143・#150 は web console の土台 (API スキーマ・フロントの土台)・CI・テスト・docs・内部の変更で、艦がやることはない。

手順:
1. 艦を止める: `yamato down <艦>`
2. yamato を更新する: `git -C ~/dev/yamato pull` (tag を使うなら `git -C ~/dev/yamato checkout v1.4.0`)
3. 写したいなら `yamato ship upgrade <艦>` (任意)
4. 艦を起こす: `yamato up <艦>`

### dashboard は SPA になった (node は不要、dist は同梱)
- 何が変わったか: `yamato dashboard` の画面が、サーバーが HTML を組む形から、React の SPA (一覧・艦ごとの設定・markdown・艦隊) に変わった。API は `/api/v1/` の JSON。SPA の build 済みの成果物 (`src/yamato/web/dist/`) はリポジトリに同梱している
- 既存の艦・利用者がやること: なし。node や npm は要らない (build が要るのは frontend を開発する人だけ)。`yamato dashboard` をこれまでどおり起動する
- やらないと: 何も起きない。古い HTML の画面は無くなった
- PR: #129・#130・#131・#132・#134

### `yamato dashboard --edit` で画面から書き込みと操作ができる
- 何が変わったか: `--edit` を付けて起動すると、画面から team.yaml (フォームと生の編集)・`roles/*.md`・charter の編集 (差分を見てから確定)、席への send、判断を閉じる、席の seat-stop・rotate、艦の up・down・extend・halt ができる。付けなければ今までどおり読むだけ。書き込みはトークン・Host・Origin を検査し、ハッシュで競合を見て、1 世代退避してから原子的に書く
- 既存の艦・利用者がやること: 画面から編集・操作したいときだけ `yamato dashboard --edit` で起動する。ローカルでの利用が前提 (外へ公開しない)
- やらないと: 読むだけの画面のまま。動作は壊れない
- PR: #133・#136・#139・#140・#141・#144・#145・#146・#151

### docs と README から task・判断の番号を除いた
- 何が変わったか: docs と README の説明から、艦の board の項目番号 (T-数字) と判断の番号 (D-数字) を除いた。判断の出典は「owner の決定 (日付)」の形に直した。挙動は変わらない。ひな形の文も同じ趣旨で変わる
- 既存の艦がやること: 移行は要らない。ひな形の文を写したい艦は `yamato ship upgrade` で写せば足りる (任意)
- やらないと: 艦の写しの文に古い番号が残るだけ
- PR: #147・#148・#149 (CI の検査は #150)

### pr merge の衝突通知は「base を merge して push」を案内する
- 何が変わったか: `pr merge` が衝突した PR の担当に送る定型文が「rebase して push してください」から「origin/<git.base> を merge して push してください (rebase はしない)」になった。ひな形の impl.md・reviewer.md の同じ言い回しも合わせた。board の列名 `rebase` は記録の形式なので変えていない
- 既存の艦がやること: 何も要らない。艦の `roles/impl.md`・`roles/reviewer.md` の写しに「rebase して push」とあれば、「base を merge して push」に書き換えるとよい (任意)
- やらないと: 写しの役割プロンプトの言い回しが古いままになるだけ。定型文は yamato が出す
- PR: #137

## v1.2.0 → v1.3.0

### 全体の手順
v1.3.0 は **v1.2.0 が警告つきで読み替えていた古い書き方を消す版** (#116)。**v1.2.0 で「書き換えろ」の警告が出ていた艦は、直してから上げる**。直さずに上げると team.yaml の検査で `yamato up` などが止まる。ひな形どおりの艦は何もしなくてよい。

**検査が拒否するようになった古い書き方**
- トップの `charter:`・`inject` のリストの `charter`・`inject.limits.charter` → `inject.files.charter` / `file:charter` / `inject.limits.files.charter`
- `memory.limits` の 4 キー → `inject.limits.memory` / `inject.limits.knowledge`

**既存の艦の写しに反映するもの**: `reviewer` 欄の `roles/pm.md` の手順 (reviewer 欄を書く一行。下の項目)。

**移行不要**: #111・#112・#114・#117・#118・#119・#121 は表示・docs・内部の変更で、艦がやることはない。

手順:
1. 艦を止める: `yamato down <艦>`
2. yamato を更新する: `git -C ~/dev/yamato pull` (tag を使うなら `git -C ~/dev/yamato checkout v1.3.0`)
3. 本命は `yamato ship upgrade <艦>` (template の記録があるので `--from` は不要。無ければ `--from v1.2.0`)。手で写すなら `git -C ~/dev/yamato diff v1.2.0 v1.3.0 -- src/yamato/templates/dev/` で差分を見る
4. 艦を起こす: `yamato up <艦>`

### board の項目に `reviewer` 欄を足す
- 何が変わったか: board の項目の frontmatter に任意の `reviewer` 欄 (assignee の直後)。`board set <id> reviewer=<席>` で書き、`reviewer=-` で消す。state=active の項目の assignee か reviewer がいる席は「担当あり」と数える (per_task の reviewer がレビュー中に「担当の無い席が生きている」と警告されなくなる)。dashboard の担当の欄に「T-xxx (レビュー)」、`board mine <席>` と注入の「自分の担当」にもレビュー中の項目を「(レビュー)」の印で出す。欄の無い既存の項目はそのまま読める
- 既存の艦がやること: 艦の `roles/pm.md` の「報告 (「PR を開いた」の知らせ) が来たら」の手順に、レビューを頼む前の `board set <ship> <id> reviewer=<reviewer の席> --by pm` を足す (ひな形 `src/yamato/templates/dev/roles/pm.md` の手順 4 を写す)。`yamato up` のやり直しは要らない
- やらないと: reviewer 欄が書かれず、レビュー中の reviewer が今までどおり警告 (黄) に出て、dashboard にもレビュー中と出ない。動作は壊れない
- PR: #115

### v1.2.0 の互換を消した: 古い `charter` の書き方と `memory.limits` は読み込みエラー
- 何が変わったか: v1.2.0 が警告つきで読み替えていた次の書き方を、team.yaml の検査が拒否する (エラー文に新しい書き方が出る)。トップの `charter:` / `inject.parts`・`roles.<role>.inject` の `charter` / `inject.limits.charter` (`charter` の互換) と、`memory.limits` (`memory_lines`・`memory_chars`・`knowledge_lines`・`knowledge_chars`。`memory.limits` の互換)。読み替えのコードと警告、team.schema.json の古いキーも消えた
- 既存の艦がやること: **v1.2.0 で「書き換えろ」の警告が出ていた艦は、直してから上げる**。(1) トップの `charter: <パス>` を消して `inject.files.charter: <パス>` に (2) リストの `charter` を `file:charter` に (3) `inject.limits.charter` を `inject.limits.files.charter` に (4) `memory.limits` を消し、`inject.limits.memory: [<memory_lines>, <memory_chars>]` と `inject.limits.knowledge: [<knowledge_lines>, <knowledge_chars>]` に。詳しくは下の v1.1.0 → v1.2.0 の `inject.files` と memory の上限の項。警告が出ていなければ何もしない
- やらないと: 古い書き方の艦は `yamato up` などの team.yaml の検査で止まる
- PR: #116

### Windows で claude の起動がコマンド長超過で失敗するのを直す
- 何が変わったか: 席の起動 (`--bg`・`-p`) の `--agents` に、その席の role だけを載せるようにした (今までは全 role の全文で、yamato-dev で 28.6K 文字)。Windows では、コマンドラインが上限 (`claude.exe` は 32767 文字、`claude.cmd` の shim は `cmd /c` 経由で 8191 文字) を超えるなら、起動前に大きい引数を示してエラーにする。npm の shim は `node cli.js` を直に叩く (見つかれば)
- 既存の艦がやること: なし。Windows で role プロンプトが長く shim の上限に当たる艦は、`claude.exe` の入れ方にするか roles/*.md を縮める
- やらないと: 動作は変わらない (macOS/Linux には影響なし)
- PR: #113

### `ship upgrade` の claude に ~/.claude/settings.json の `env` を渡す
- 何が変わったか: upgrade の claude は `--setting-sources local` なので user settings が丸ごと落ち、Bedrock 用の `env` (`CLAUDE_CODE_USE_BEDROCK`・`AWS_REGION`・`AWS_PROFILE` など) も届かなかった。user の settings.json (`CLAUDE_CONFIG_DIR` があればそこ) の `env` だけを、upgrade の claude の環境に渡すようにした (秘密が入りうるので控えの settings.json には書かない)。hooks・CLAUDE.md は今までどおり入らない
- 既存の艦がやること: なし。Bedrock を使う利用者は `~/.claude/settings.json` の `env` に書いてあれば upgrade がそのまま通る
- やらないと: 動作は変わらない (env を書いていない利用者には影響なし)
- PR: #110

## v1.1.0 → v1.2.0

### 全体の手順
v1.2.0 の項目のうち、**互換が 1 版だけあるもの (次の版で消える古い書き方)** は下の 2 つ。ひな形どおりの艦は何もしなくても今のまま動くが、warnings に「書き換えろ」と出る。次の版より前に直す。残りの項目は opt-in か表示の変更だけで、やることはない。

**互換を 1 版で消す古い書き方 (次の版で検査が拒否する)**
- トップの `charter:`・`inject` のリストの `charter`・`inject.limits.charter` → `inject.files.charter` / `file:charter` / `inject.limits.files.charter`
- `memory.limits` の 4 キー (`memory_lines` / `memory_chars` / `knowledge_lines` / `knowledge_chars`) → `inject.limits.memory` / `inject.limits.knowledge`

**すぐ検査で止まるもの**: `inject.limits` の `handoff`・`log_tail`・`last_report` に整数 1 個や 0・負の値、整数のキーに配列を書いていると `yamato up` が止まる (ひな形どおりなら該当しない)。

手順:
1. 艦を止める: `yamato down <艦>`
2. yamato を更新する: `git -C ~/dev/yamato pull` (tag を使うなら `git -C ~/dev/yamato checkout v1.2.0`)
3. 下の各項目のうち要るものだけ、艦の写しに反映する。本命は `yamato ship upgrade <艦>` (v1.1.0 以降に作った艦は template の記録があるので `--from` は不要。無ければ `--from v1.1.0`)。手で写すなら `git -C ~/dev/yamato diff v1.1.0 v1.2.0 -- src/yamato/templates/dev/` で差分を見る
4. 艦を起こす: `yamato up <艦>`。team.yaml を変えたときは、この `up` で `.runtime/team.json` に写って効く

### 新コマンド `yamato dashboard`: 全艦の owner 待ちと席の状況をローカルのブラウザで見る
- 何が変わったか: `yamato dashboard [--port N] [--no-open]` を足した。`127.0.0.1` だけで待ち受け (既定 port 8765)、GET だけ・艦フォルダには何も書かない。あわせて内部で `feed._ABNORMAL_KINDS` を `feed.ABNORMAL_KINDS` に改名した (dashboard と共有するため。表示は変わらない)
- 既存の艦がやること: なし。team.yaml・艦フォルダの形式・既存コマンドに影響しない。使うなら `~/dev/yamato` を pull して `yamato dashboard`
- PR: #102

### `status` が idle の `state: blocked` を赤くしなくなった
- 何が変わったか: 「人間の返事待ちの疑い」(idle で `state: blocked`) の `!!!` 行と、`ships` の「赤 N」への計上をやめた。`state` は Claude Code が最後の発言から付けるラベルで、誤検知があったため。表示が減るだけ。設定の変更なし
- 既存の艦がやること: なし。待機中かは席の行の `status=idle` で分かる。`waiting`・`failed`・stale・per_task の赤は従来どおり
- PR: #100

### `inject.limits` の形を検査で揃えた: `handoff`・`log_tail`・`last_report` は `[行数, 文字数]` だけ
- 何が変わったか: この 3 つに整数 1 個を書いても team.yaml の検査を通り、起動後の注入で `TypeError` で落ちていた。検査 (`team.py` と schema) で拒否するようにした。逆に `mine_items` など整数のキーに `[行, 文字]` の配列を書いても拒否する。`[行数, 文字数]` の値は正の整数 2 個
- 既存の艦がやること: `inject.limits` を見て次の 3 つを直す。ひな形どおりの艦は何もしない。(a) この 3 つに整数 1 個を書いている → `[行数, 文字数]` に (例: `handoff: [40, 2000]`)。(b) この 3 つに 0 や負の値の配列 (例: `[0, 2000]`) を書いている → 正の整数に。前は検査を通っていた。(c) `mine_items`・`inbox_messages`・`inbox_chars`・`total_chars`・`board_items`・`fleet_items` に配列を書いている → 正の整数 1 個に (例: `mine_items: 15`)。前は検査を通っていた
- やらないと: (a)(b)(c) のどれも、`yamato up` の検査が「inject.limits.<キー> は …」で止まる (a は前は起動後に落ちていた)
- PR: #97

### memory と knowledge の上限を `memory.limits` から `inject.limits.memory` / `knowledge` に移した
- 何が変わったか: 上限の書き場所が 1 つになった。`inject.limits.memory: [80, 4000]` と `inject.limits.knowledge: [120, 5000]` ([行数, 文字数]、正の整数 2 個。既定は前の `memory.limits` の既定と同じ)。`memory apply` が超える案を拒否する上限、注入で切る上限、`memory curate` の案・プロンプト・`memory status` の上限は、すべてここを読む。前の `memory.limits` の 4 キー (`memory_lines` / `memory_chars` / `knowledge_lines` / `knowledge_chars`) は `memory:` 節から外れた。ひな形 (dev・research・admiral) は新しい書き方にした
- 既存の艦がやること: **何もしなくても 1 版は動く**。`memory.limits` があれば `inject.limits.memory` / `knowledge` と読み替え (4 キーのうち書いていない方は既定のまま)、team.yaml を読むたびに warnings に「書き換えろ」と出る。両方に書いてあれば `inject.limits` が勝ち、warnings にそう出る。この互換は**次の版で消える**。直すとき: team.yaml の `memory.limits` を消し、`inject.limits` に `memory: [<memory_lines>, <memory_chars>]` と `knowledge: [<knowledge_lines>, <knowledge_chars>]` を書く (例: `memory_lines: 60` / `memory_chars: 3000` なら `memory: [60, 3000]`)。`yamato ship upgrade` でも取り込める
- やらないと: 古い書き方のままだと警告が出る (動きは同じ)。次の版で `memory.limits` が検査で拒否される。古い yamato (v1.1.0 以前) は `inject.limits.memory` / `knowledge` を「memory.limits に一本化した」と拒否して load で落ちる。`~/dev/yamato` を pull してから team.yaml を書く。`inject.limits.memory` / `knowledge` に整数 1 個や 0・負の値を書くと、up の検査が止まる
- PR: #99

### 注入に任意のファイルを足せるようにした: `inject.files` と `file:<名前>`。部品 `charter` はこれに置き換わった (決定 2026-10-08。先に足した部品 `charter`〔決定 2026-10-06〕を含む)
- 何が変わったか
  - team.yaml のトップの `inject.files` に `名前: パス` を書くと、`inject` のリスト (`inject.parts` と `roles.<role>.inject`) で `file:<名前>` と参照できる。入れた席の SessionStart の知見の hook (hook B) に、リストに書いた順で載る (`total_chars` で切られるのは末尾から)。パスは相対なら艦フォルダ基点、`~` と絶対パスも可、repo の中は `@<workspace の呼び名>/<パス>`
  - 上限は `inject.limits.files.<名前>` (`[行数, 文字数]`)、無ければ `inject.limits.files.default`、それも無ければ `[60, 3000]`。超えたら切って全文のパスを出す。ファイルが無い・空なら `(なし: <パス>)` の 1 行 (up の warnings にも出る)。`default` は名前に使えない (予約語)
  - 部品名 `charter`・トップの `charter:`・`inject.limits.charter` はなくなった。ひな形 (dev・research・admiral) は `inject.files.charter: charter.md` と `file:charter` に書き換えた。memory と knowledge は部品のまま
  - hook B の並びが変わった: 前は charter → memory → knowledge の固定順、今は `inject` のリストに書いた順。ひな形は `file:charter` を `memory` の前に置く (charter が先頭)
- 既存の艦がやること: **何もしなくても 1 版は動く**。古い書き方 (リストの `charter`・トップの `charter:`・`inject.limits.charter`) は `file:charter`・`inject.files.charter`・`inject.limits.files.charter` と読み替え、team.yaml を読むたびに warnings に「書き換えろ」と出る。inject.files に charter が無ければ `charter.md` (トップの `charter:` があればその値) で補う。この互換は**次の版で消える**。直すとき: (1) トップの `charter: <パス>` を消して `inject:` の下に `files: {charter: <パス>}` を書く (2) `roles.<role>.inject` と `inject.parts` の `charter` を `file:charter` に (hook B の先頭に載せたいなら `memory` の前) (3) `inject.limits.charter` があれば `inject.limits.files.charter` に。`yamato ship upgrade` でも取り込める。任意のファイルを足すなら `inject.files` に書いて `file:<名前>` を入れる
- やらないと: 古い書き方のままだと警告が出る (動きは同じ)。次の版で検査が拒否する。古い yamato (v1.1.0 以前) は `file:`・`inject.files` を読めず load で落ちる。`~/dev/yamato` を pull してから team.yaml を書く
- PR: #96、#98

## v1.0.0 → v1.1.0

### 全体の手順
v1.1.0 の項目は**すべて opt-in**。何もしなくても、艦は今のまま動く。使いたい機能・取り込みたいひな形の変更があるものだけ、次の手順で反映する。
1. 艦を止める: `yamato down <艦>`
2. yamato を更新する: `git -C ~/dev/yamato pull` (v1.1.0 の tag を使うなら `git -C ~/dev/yamato checkout v1.1.0`)
3. 下の各項目のうち要るものだけ、艦の写しに反映する
   - 本命は `yamato ship upgrade <艦> --from v1.0.0`。`roles/` と `team.yaml` をひな形の新しい版と突き合わせ、対話の claude が変更を 1 件ずつ owner に出して、決まったものだけ写しに入れる。役割プロンプトや team.yaml を書き換えている艦でも、書き換えを壊さずに取り込める。v1.0.0 で作った艦には template の記録がないので `--from v1.0.0` が要る (dev 以外のひな形は `--template research` なども)
   - 手で写すなら、`git -C ~/dev/yamato diff v1.0.0 v1.1.0 -- src/yamato/templates/dev/` で差分を見て、艦フォルダの `roles/` と `team.yaml` の同じ箇所に写す
4. 艦を起こす: `yamato up <艦>`。team.yaml を変えたときは、この `up` で `.runtime/team.json` に写って効く

### team.yaml に `up_seats` を足した: up で captain と一緒に起こす席を艦ごとに決める
- 何が変わったか: team.yaml のトップに `up_seats` (席のリスト) が増えた。`yamato up` が captain に加えてこの席も起こす (`--seats` との和。重複と hub は除く)。既定は空 = captain だけで、今と同じ
- 既存の艦がやること: なし。常に起こしておきたい席がある艦だけ、team.yaml に `up_seats: [reviewer]` のように書き、次の `yamato up` から効く
- やらないと: 動作は変わらない (captain だけが起きる)
- PR: #90

### team.yaml の `workspace` を配列にできる: 1 艦で複数 repo
- 何が変わったか: `workspace` が文字列かパスの配列になった (共通ライブラリとアプリなど)。先頭が席の cwd、残りは席の `--add-dir` に足される。各 repo の呼び名はフォルダ名で、かぶったら読み込みエラー。trust と存在は全 repo で確かめる。`ship create --workspace` は複数回渡せる。`worktree add` / `path` / `rm` は `--repo <呼び名>` で repo を選び (省略時は先頭、`add` は繰り返せる)、worktree は `<艦>/worktrees/<id>/<呼び名>/` に repo ごとに切る。`pr open` / `merge` も repo ごとに動く
- 既存の艦がやること: なし。`workspace` を文字列のまま (要素 1 つの配列と同じ扱い) にしておけば、挙動も記録の形も変わらない。複数 repo にしたい艦だけ、team.yaml の `workspace` を配列に書き換え、追加する repo を Claude Code に trust させて、`yamato down` → `yamato up` する (席の起動引数は次の起動から変わる)。役割プロンプトの worktree の使い方は、ひな形の変更を `ship upgrade` で取り込む
- やらないと: 動作は変わらない (1 repo のまま)
- PR: #91、#92、#93

### team.yaml に `template` を足した・`yamato ship upgrade` で艦の写しを新しい版に上げられる
- 何が変わったか: team.yaml のトップに `template: {name: dev, version: 1.1.0}` (写しの元のひな形と yamato の版) が増えた。`ship create` が書く。実行時には読まない。あわせて `yamato ship upgrade <艦> [--from <版>] [--template <名>]` と `ship upgrade-done` が増えた。upgrade は `roles/` と `team.yaml` を `<艦>/.upgrade/` に控え、upgrade 専用の対話 claude が変更を 1 件ずつowner に出して、決まったものだけ写しに入れる
- 既存の艦がやること: v1.0.0 で作った艦は `template` の記録がない (書かなくても動く)。今後の版に上げるときは `yamato ship upgrade <艦> --from v1.0.0` を流す (dev 以外のひな形は `--template research` なども)。終わると `template` の行が足される。記録だけ先に足したい艦は `yamato ship upgrade-done <艦> --version 1.0.0` でもよい。以降、この文書の「ひな形の写しに関わる項目」の手で写す手順は、`yamato ship upgrade` で取り込める
- やらないと: 動作は変わらない。次の版からの upgrade で `--from` を毎回渡す
- PR: #94

### impl は PR のあと merge まで席に残る: ひな形の roles/{impl,reviewer,pm}.md
- 何が変わったか: ひな形 (`src/yamato/templates/dev/roles/`) の impl.md は、PR を開いて報告したあと終業せず待つ (差し戻し・「rebase して push」は同じ会話で直し、「merge 済み」で終業)。reviewer.md は「merge 済み」を captain に加えて担当の impl にも送る。pm.md は PR を出して merge 待ちの impl に新しい task を割り当てない。コードと §0 B3 は変えない
- 既存の艦がやること: 艦フォルダの `roles/impl.md` (仕事の進め方 10・「シフトの終わり」の頭・git の規律の worktree の行)、`roles/reviewer.md` (「merge 済み」を送る行)、`roles/pm.md` (「仕事の進め方」2) に、ひな形の同じ箇所の文を手で写す。次のシフトから効く (`yamato up` のやり直しは要らない)。今までどおり PR 後に終業させたい艦は写さなくてよい
- やらないと: 動作は変わらない。impl は今までどおり PR のあと終業し、差し戻しは新しいシフトで始まる
- PR: #89

### ひな形の roles/{impl,reviewer,pm}.md に「移行手順」の決まりを足した
- 何が変わったか: ひな形 (`src/yamato/templates/dev/roles/`) の impl.md・reviewer.md・pm.md に、移行手順 (この docs/migration.md) の決まりを足した。impl は該当する変更の PR で「未リリース」に手順を書き PR 本文に「移行手順: あり / なし」を書く。reviewer は観点に足す。pm は task の完了条件に入れる
- 既存の艦がやること: 艦フォルダの `roles/impl.md`・`roles/reviewer.md`・`roles/pm.md` に、ひな形の同じ箇所 (impl.md の「仕事の進め方」5 の下、reviewer.md の「確かめること」の docs の行の下、pm.md の「仕事の進め方」1 の下の箇条) の文を手で足す。足したら席は次のシフトから読む (`yamato up` のやり直しは要らない)。この決まりが要らない艦は足さなくてよい
- やらないと: 動作は変わらない。その艦の席が移行手順を書かない・見ないだけ
- PR: #87

### team.yaml に `setting_sources` を足した: 席に user の設定を opt-in で読ませる
- 何が変わったか: team.yaml のトップに `setting_sources` (`user` / `project` / `local` のリスト) が増えた。席の起動の `--setting-sources` に渡す (bg・headless・memory 棚卸しの `-p`)。既定は今と同じ `[project, local]`
- 既存の艦がやること: なし。書かなければ挙動は変わらない。席に `~/.claude/CLAUDE.md` を読ませたい艦だけ、team.yaml に `setting_sources: [project, local, user]` を足し、次の `yamato up` と新しいシフトから効く (resume した席には `--setting-sources` が引き継がれず user 設定が戻る。止めたい値は team.yaml の `settings:` に書く。`--settings` は resume でも効き user 設定に勝つ)
- やらないと: 動作は変わらない
- 注意 (足す艦): user の hooks・plugin hooks・env・`remoteControlAtStartup` も席に入る。design.md §4.1 を読むこと
- PR: #88
