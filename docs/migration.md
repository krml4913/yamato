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
