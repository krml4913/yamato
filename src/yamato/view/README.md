# yamato.view — zellij で艦と席を覗く窓 (design §10, P2 の先行実装)

P0 と衝突しないよう独立したモジュールにしてある。`yamato` CLI への組み込みは P0 の merge 後に別 task で行う。

## 使い方

```sh
# 艦ごとに 1 タブ、席ごとに 1 ペインの layout を作って開く
bin/yamato-seat-attach --layout dev research -o ~/yamato/view.kdl
zellij --session yamato-view --new-session-with-layout ~/yamato/view.kdl

# 1 席だけ (ペインの中で動かすもの)
bin/yamato-seat-attach dev pm          # --poll 3 (秒) / 環境変数 YAMATO_VIEW_POLL
```

艦は名前 (`$YAMATO_HOME/<ship>`, 既定 `~/yamato/<ship>`) かパスで指定する。

## 動き

- 席の「今のシフト」は `roster.json` の `seats.<seat>.sessionId` で決める (`--name` は一意でないため、名前では探さない)
- そのセッションが `claude agents --json --all` で生きている (`pid` がある) ときだけ attach する。止まったセッションに attach すると蘇るため
- 席がいなければ「待機中」と表示し、数秒ごとに確認する
- attach 中に roster が別の生きているセッションを指したら、今の attach を止めて付け直す。古いシフトは止めない (席の管理側の仕事)
- attach 先が止まると `claude attach` は自分で抜けるので、待機に戻る
- `claude attach` はペインのフォアグラウンドで動かす (macOS ではバックグラウンドだと落ちる)

「今のシフト」の解決は差し替えられる (`attach.run(resolve, ...)` に任意の `resolve()` を渡す)。既定は `attach.roster_resolver`。

## P0 に合わせるところ

艦フォルダを読む処理は `shipfiles.py` に集めてある。想定している形式はそこにコメントで書いた。

- `roster.json`: `{"seats": {"<seat>": {"sessionId": "<フルの id>", ...}}}` (P0 の `yamato.roster`)
- 席の一覧: `.runtime/team.json` の `seats` を優先する。無ければ `team.yaml` の `roles:` を簡易に読み、`count: n` を `<role>-1..n` に展開する (P0 の `yamato.team.expand_seats` と同じ規則)
- 艦の場所: P0 の registry (`--path` で作った艦) にはまだ対応していない。P0 の merge 後に `yamato.util.resolve_ship` に置き換える

## 実機で分かったこと (Claude Code 2.1.283 / zellij 0.45.1)

- **`claude attach` はフルの sessionId を受け付けない** (`No job matching '<uuid>'` で exit 1)。短い `id` (`claude agents` の `id`) を渡す必要がある。照合はフルの id で行い、attach には短い id を使っている
