# 移行手順 (migration)

v1.0.0 以降の変更のうち、**既存の艦・利用者が何かをしないと動かなくなる (または挙動が変わる) もの**の手順をここに集める。tag を打つときに「未リリース」の見出しを版名 (`## v1.0.0 → v1.1.0` など) に改め、その上に空の「未リリース」を作り直す。

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

### 注入の部品に `charter` を足した: captain と planner に艦の charter を読ませる (T-072, D-086)
- 何が変わったか: `inject` の部品に `charter` が増えた (opt-in。既定の部品には入らない)。入れた席の SessionStart の知見の hook (hook B) の先頭に、team.yaml の `charter:` が指すファイルの中身が入る。無い・空なら「(charter なし)」の 1 行。`inject.limits.charter` (`[行数, 文字数]`、省略時 `[60, 3000]`) を超えたら切って全文のパスを出す。ひな形は dev の pm・planner、research の editor、admiral の admiral に `charter` を足した
- 既存の艦がやること: `roles.<captain>.inject` と、planner がいる艦は `roles.planner.inject` に `charter` を足す (planner が inject を持たず既定で動いている艦は、既定の部品 `[handoff, log_tail, mine, inbox, memory, knowledge]` に `charter` を足して明示する)。`inject.limits.charter` はコードに省略時の値があるので、上限を変えたい艦だけ書く。次の `yamato up` から効く。`yamato ship upgrade` でも取り込める
- やらないと: 動作は変わらない (charter は注入されない)
- PR: T-072

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
