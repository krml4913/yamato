# 移行手順 (migration)

v1.0.0 以降の変更のうち、**既存の艦・利用者が何かをしないと動かなくなる (または挙動が変わる) もの**の手順をここに集める。tag を打つときに「未リリース」の見出しを版名 (`## v1.1.0 → v1.2.0` など) に改め、その上に空の「未リリース」を作り直す。

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

## v1.1.0 → v1.2.0

### 全体の手順
v1.2.0 の項目のうち、**互換が 1 版だけあるもの (次の版で消える古い書き方)** は下の 2 つ。ひな形どおりの艦は何もしなくても今のまま動くが、warnings に「書き換えろ」と出る。次の版より前に直す。残りの項目は opt-in か表示の変更だけで、やることはない。

**互換を 1 版で消す古い書き方 (次の版で検査が拒否する)**
- トップの `charter:`・`inject` のリストの `charter`・`inject.limits.charter` → `inject.files.charter` / `file:charter` / `inject.limits.files.charter` (T-075)
- `memory.limits` の 4 キー (`memory_lines` / `memory_chars` / `knowledge_lines` / `knowledge_chars`) → `inject.limits.memory` / `inject.limits.knowledge` (T-076)

**すぐ検査で止まるもの**: `inject.limits` の `handoff`・`log_tail`・`last_report` に整数 1 個や 0・負の値、整数のキーに配列を書いていると `yamato up` が止まる (T-073。ひな形どおりなら該当しない)。

手順:
1. 艦を止める: `yamato down <艦>`
2. yamato を更新する: `git -C ~/dev/yamato pull` (tag を使うなら `git -C ~/dev/yamato checkout v1.2.0`)
3. 下の各項目のうち要るものだけ、艦の写しに反映する。本命は `yamato ship upgrade <艦>` (v1.1.0 以降に作った艦は template の記録があるので `--from` は不要。無ければ `--from v1.1.0`)。手で写すなら `git -C ~/dev/yamato diff v1.1.0 v1.2.0 -- src/yamato/templates/dev/` で差分を見る
4. 艦を起こす: `yamato up <艦>`。team.yaml を変えたときは、この `up` で `.runtime/team.json` に写って効く

### 新コマンド `yamato dashboard`: 全艦の元帥待ちと席の状況をローカルのブラウザで見る (T-079, D-093)
- 何が変わったか: `yamato dashboard [--port N] [--no-open]` を足した。`127.0.0.1` だけで待ち受け (既定 port 8765)、GET だけ・艦フォルダには何も書かない。あわせて内部で `feed._ABNORMAL_KINDS` を `feed.ABNORMAL_KINDS` に改名した (dashboard と共有するため。表示は変わらない)
- 既存の艦がやること: なし。team.yaml・艦フォルダの形式・既存コマンドに影響しない。使うなら `~/dev/yamato` を pull して `yamato dashboard`
- PR: T-079

### `status` が idle の `state: blocked` を赤くしなくなった (T-078)
- 何が変わったか: 「人間の返事待ちの疑い」(idle で `state: blocked`) の `!!!` 行と、`ships` の「赤 N」への計上をやめた。`state` は Claude Code が最後の発言から付けるラベルで、誤検知があったため。表示が減るだけ。設定の変更なし
- 既存の艦がやること: なし。待機中かは席の行の `status=idle` で分かる。`waiting`・`failed`・stale・per_task の赤は従来どおり
- PR: T-078

### `inject.limits` の形を検査で揃えた: `handoff`・`log_tail`・`last_report` は `[行数, 文字数]` だけ (T-073)
- 何が変わったか: この 3 つに整数 1 個を書いても team.yaml の検査を通り、起動後の注入で `TypeError` で落ちていた。検査 (`team.py` と schema) で拒否するようにした。逆に `mine_items` など整数のキーに `[行, 文字]` の配列を書いても拒否する。`[行数, 文字数]` の値は正の整数 2 個
- 既存の艦がやること: `inject.limits` を見て次の 3 つを直す。ひな形どおりの艦は何もしない。(a) この 3 つに整数 1 個を書いている → `[行数, 文字数]` に (例: `handoff: [40, 2000]`)。(b) この 3 つに 0 や負の値の配列 (例: `[0, 2000]`) を書いている → 正の整数に。前は検査を通っていた。(c) `mine_items`・`inbox_messages`・`inbox_chars`・`total_chars`・`board_items`・`fleet_items` に配列を書いている → 正の整数 1 個に (例: `mine_items: 15`)。前は検査を通っていた
- やらないと: (a)(b)(c) のどれも、`yamato up` の検査が「inject.limits.<キー> は …」で止まる (a は前は起動後に落ちていた)
- PR: T-073

### memory と knowledge の上限を `memory.limits` から `inject.limits.memory` / `knowledge` に移した (T-076, D-089)
- 何が変わったか: 上限の書き場所が 1 つになった。`inject.limits.memory: [80, 4000]` と `inject.limits.knowledge: [120, 5000]` ([行数, 文字数]、正の整数 2 個。既定は前の `memory.limits` の既定と同じ)。`memory apply` が超える案を拒否する上限、注入で切る上限、`memory curate` の案・プロンプト・`memory status` の上限は、すべてここを読む。前の `memory.limits` の 4 キー (`memory_lines` / `memory_chars` / `knowledge_lines` / `knowledge_chars`) は `memory:` 節から外れた。ひな形 (dev・research・admiral) は新しい書き方にした
- 既存の艦がやること: **何もしなくても 1 版は動く**。`memory.limits` があれば `inject.limits.memory` / `knowledge` と読み替え (4 キーのうち書いていない方は既定のまま)、team.yaml を読むたびに warnings に「書き換えろ」と出る。両方に書いてあれば `inject.limits` が勝ち、warnings にそう出る。この互換は**次の版で消える**。直すとき: team.yaml の `memory.limits` を消し、`inject.limits` に `memory: [<memory_lines>, <memory_chars>]` と `knowledge: [<knowledge_lines>, <knowledge_chars>]` を書く (例: `memory_lines: 60` / `memory_chars: 3000` なら `memory: [60, 3000]`)。`yamato ship upgrade` でも取り込める
- やらないと: 古い書き方のままだと警告が出る (動きは同じ)。次の版で `memory.limits` が検査で拒否される。古い yamato (v1.1.0 以前) は `inject.limits.memory` / `knowledge` を「memory.limits に一本化した」と拒否して load で落ちる。`~/dev/yamato` を pull してから team.yaml を書く。`inject.limits.memory` / `knowledge` に整数 1 個や 0・負の値を書くと、up の検査が止まる
- PR: T-076

### 注入に任意のファイルを足せるようにした: `inject.files` と `file:<名前>`。部品 `charter` はこれに置き換わった (T-075, D-089。T-072, D-086 の charter を含む)
- 何が変わったか
  - team.yaml のトップの `inject.files` に `名前: パス` を書くと、`inject` のリスト (`inject.parts` と `roles.<role>.inject`) で `file:<名前>` と参照できる。入れた席の SessionStart の知見の hook (hook B) に、リストに書いた順で載る (`total_chars` で切られるのは末尾から)。パスは相対なら艦フォルダ基点、`~` と絶対パスも可、repo の中は `@<workspace の呼び名>/<パス>`
  - 上限は `inject.limits.files.<名前>` (`[行数, 文字数]`)、無ければ `inject.limits.files.default`、それも無ければ `[60, 3000]`。超えたら切って全文のパスを出す。ファイルが無い・空なら `(なし: <パス>)` の 1 行 (up の warnings にも出る)。`default` は名前に使えない (予約語)
  - 部品名 `charter`・トップの `charter:`・`inject.limits.charter` はなくなった。ひな形 (dev・research・admiral) は `inject.files.charter: charter.md` と `file:charter` に書き換えた。memory と knowledge は部品のまま
  - hook B の並びが変わった: 前は charter → memory → knowledge の固定順、今は `inject` のリストに書いた順。ひな形は `file:charter` を `memory` の前に置く (charter が先頭)
- 既存の艦がやること: **何もしなくても 1 版は動く**。古い書き方 (リストの `charter`・トップの `charter:`・`inject.limits.charter`) は `file:charter`・`inject.files.charter`・`inject.limits.files.charter` と読み替え、team.yaml を読むたびに warnings に「書き換えろ」と出る。inject.files に charter が無ければ `charter.md` (トップの `charter:` があればその値) で補う。この互換は**次の版で消える**。直すとき: (1) トップの `charter: <パス>` を消して `inject:` の下に `files: {charter: <パス>}` を書く (2) `roles.<role>.inject` と `inject.parts` の `charter` を `file:charter` に (hook B の先頭に載せたいなら `memory` の前) (3) `inject.limits.charter` があれば `inject.limits.files.charter` に。`yamato ship upgrade` でも取り込める。任意のファイルを足すなら `inject.files` に書いて `file:<名前>` を入れる
- やらないと: 古い書き方のままだと警告が出る (動きは同じ)。次の版で検査が拒否する。古い yamato (v1.1.0 以前) は `file:`・`inject.files` を読めず load で落ちる。`~/dev/yamato` を pull してから team.yaml を書く
- PR: T-072, T-075

## v1.0.0 → v1.1.0

### 全体の手順
v1.1.0 の項目は**すべて opt-in**。何もしなくても、艦は今のまま動く。使いたい機能・取り込みたいひな形の変更があるものだけ、次の手順で反映する。
1. 艦を止める: `yamato down <艦>`
2. yamato を更新する: `git -C ~/dev/yamato pull` (v1.1.0 の tag を使うなら `git -C ~/dev/yamato checkout v1.1.0`)
3. 下の各項目のうち要るものだけ、艦の写しに反映する
   - 本命は `yamato ship upgrade <艦> --from v1.0.0`。`roles/` と `team.yaml` をひな形の新しい版と突き合わせ、対話の claude が変更を 1 件ずつ owner に出して、決まったものだけ写しに入れる。役割プロンプトや team.yaml を書き換えている艦でも、書き換えを壊さずに取り込める。v1.0.0 で作った艦には template の記録がないので `--from v1.0.0` が要る (dev 以外のひな形は `--template research` なども)
   - 手で写すなら、`git -C ~/dev/yamato diff v1.0.0 v1.1.0 -- src/yamato/templates/dev/` で差分を見て、艦フォルダの `roles/` と `team.yaml` の同じ箇所に写す
4. 艦を起こす: `yamato up <艦>`。team.yaml を変えたときは、この `up` で `.runtime/team.json` に写って効く

### team.yaml に `up_seats` を足した: up で captain と一緒に起こす席を艦ごとに決める (T-064)
- 何が変わったか: team.yaml のトップに `up_seats` (席のリスト) が増えた。`yamato up` が captain に加えてこの席も起こす (`--seats` との和。重複と hub は除く)。既定は空 = captain だけで、今と同じ
- 既存の艦がやること: なし。常に起こしておきたい席がある艦だけ、team.yaml に `up_seats: [reviewer]` のように書き、次の `yamato up` から効く
- やらないと: 動作は変わらない (captain だけが起きる)
- PR: #90 (T-064)

### team.yaml の `workspace` を配列にできる: 1 艦で複数 repo (T-066・T-067・T-068, D-071)
- 何が変わったか: `workspace` が文字列かパスの配列になった (共通ライブラリとアプリなど)。先頭が席の cwd、残りは席の `--add-dir` に足される。各 repo の呼び名はフォルダ名で、かぶったら読み込みエラー。trust と存在は全 repo で確かめる。`ship create --workspace` は複数回渡せる。`worktree add` / `path` / `rm` は `--repo <呼び名>` で repo を選び (省略時は先頭、`add` は繰り返せる)、worktree は `<艦>/worktrees/<id>/<呼び名>/` に repo ごとに切る。`pr open` / `merge` も repo ごとに動く
- 既存の艦がやること: なし。`workspace` を文字列のまま (要素 1 つの配列と同じ扱い) にしておけば、挙動も記録の形も変わらない。複数 repo にしたい艦だけ、team.yaml の `workspace` を配列に書き換え、追加する repo を Claude Code に trust させて、`yamato down` → `yamato up` する (席の起動引数は次の起動から変わる)。役割プロンプトの worktree の使い方は、ひな形の変更を `ship upgrade` で取り込む
- やらないと: 動作は変わらない (1 repo のまま)
- PR: #91 (T-066)、#92 (T-067)、#93 (T-068)

### team.yaml に `template` を足した・`yamato ship upgrade` で艦の写しを新しい版に上げられる (T-070, D-081)
- 何が変わったか: team.yaml のトップに `template: {name: dev, version: 1.1.0}` (写しの元のひな形と yamato の版) が増えた。`ship create` が書く。実行時には読まない。あわせて `yamato ship upgrade <艦> [--from <版>] [--template <名>]` と `ship upgrade-done` が増えた。upgrade は `roles/` と `team.yaml` を `<艦>/.upgrade/` に控え、upgrade 専用の対話 claude が変更を 1 件ずつowner に出して、決まったものだけ写しに入れる
- 既存の艦がやること: v1.0.0 で作った艦は `template` の記録がない (書かなくても動く)。今後の版に上げるときは `yamato ship upgrade <艦> --from v1.0.0` を流す (dev 以外のひな形は `--template research` なども)。終わると `template` の行が足される。記録だけ先に足したい艦は `yamato ship upgrade-done <艦> --version 1.0.0` でもよい。以降、この文書の「ひな形の写しに関わる項目」の手で写す手順は、`yamato ship upgrade` で取り込める
- やらないと: 動作は変わらない。次の版からの upgrade で `--from` を毎回渡す
- PR: T-070

### impl は PR のあと merge まで席に残る: ひな形の roles/{impl,reviewer,pm}.md (T-062, D-073)
- 何が変わったか: ひな形 (`src/yamato/templates/dev/roles/`) の impl.md は、PR を開いて報告したあと終業せず待つ (差し戻し・「rebase して push」は同じ会話で直し、「merge 済み」で終業)。reviewer.md は「merge 済み」を captain に加えて担当の impl にも送る。pm.md は PR を出して merge 待ちの impl に新しい task を割り当てない。コードと §0 B3 は変えない
- 既存の艦がやること: 艦フォルダの `roles/impl.md` (仕事の進め方 10・「シフトの終わり」の頭・git の規律の worktree の行)、`roles/reviewer.md` (「merge 済み」を送る行)、`roles/pm.md` (「仕事の進め方」2) に、ひな形の同じ箇所の文を手で写す。次のシフトから効く (`yamato up` のやり直しは要らない)。今までどおり PR 後に終業させたい艦は写さなくてよい
- やらないと: 動作は変わらない。impl は今までどおり PR のあと終業し、差し戻しは新しいシフトで始まる
- PR: T-062

### ひな形の roles/{impl,reviewer,pm}.md に「移行手順」の決まりを足した (T-060)
- 何が変わったか: ひな形 (`src/yamato/templates/dev/roles/`) の impl.md・reviewer.md・pm.md に、移行手順 (この docs/migration.md) の決まりを足した。impl は該当する変更の PR で「未リリース」に手順を書き PR 本文に「移行手順: あり / なし」を書く。reviewer は観点に足す。pm は task の完了条件に入れる
- 既存の艦がやること: 艦フォルダの `roles/impl.md`・`roles/reviewer.md`・`roles/pm.md` に、ひな形の同じ箇所 (impl.md の「仕事の進め方」5 の下、reviewer.md の「確かめること」の docs の行の下、pm.md の「仕事の進め方」1 の下の箇条) の文を手で足す。足したら席は次のシフトから読む (`yamato up` のやり直しは要らない)。この決まりが要らない艦は足さなくてよい
- やらないと: 動作は変わらない。その艦の席が移行手順を書かない・見ないだけ
- PR: #87 (T-060)

### team.yaml に `setting_sources` を足した: 席に user の設定を opt-in で読ませる (T-061)
- 何が変わったか: team.yaml のトップに `setting_sources` (`user` / `project` / `local` のリスト) が増えた。席の起動の `--setting-sources` に渡す (bg・headless・memory 棚卸しの `-p`)。既定は今と同じ `[project, local]`
- 既存の艦がやること: なし。書かなければ挙動は変わらない。席に `~/.claude/CLAUDE.md` を読ませたい艦だけ、team.yaml に `setting_sources: [project, local, user]` を足し、次の `yamato up` と新しいシフトから効く (resume の席は保存済みのオプションのまま)
- やらないと: 動作は変わらない
- 注意 (足す艦): user の hooks・plugin hooks・env・`remoteControlAtStartup` も席に入る。design.md §4.1 を読むこと
- PR: #88 (T-061)
