# yamato.view — zellij で艦と席を覗く窓 (design §10)

`yamato view` として CLI に組み込み済み。艦フォルダは自分では読まず、P0 のモジュールを使う (艦の解決は `util.resolve_ship`、席の一覧は `team.runtime_team`、今のシフトは `roster`、`claude` 呼び出しは `claude.py`)。

## 使い方

```sh
# 艦ごとに 1 タブ、席ごとに 1 ペインの layout を作って開く (zellij を実際に呼ぶ)
./yamato view open dev research        # 艦名を省略すると登録されている全艦
./yamato view open                     # zellij の中なら、今のセッションに艦ごとタブを足す

# layout (KDL) だけ組み立てる。zellij は呼ばない
./yamato view layout dev research -o ~/yamato/view.kdl
zellij --session yamato-view --new-session-with-layout ~/yamato/view.kdl

# 1 席だけ (ペインの中で動かすもの)
./yamato view attach dev pm            # --poll 3 (秒) / 環境変数 YAMATO_VIEW_POLL
```

艦は名前か艦フォルダのパスで指定する。名前は `$YAMATO_HOME/ships.json` (`ship create --path` で作った艦の登録簿)、なければ `$YAMATO_HOME/<name>` (既定 `~/yamato/<name>`) の順に探す。`view open` で艦名を省略すると `ships.json` と `$YAMATO_HOME` 直下の全艦が対象になる。

layout の各ペインは `<yamato> view attach <艦フォルダの絶対パス> <席>` を動かす。ペインは zellij のサーバの環境で動くため、この shell の `YAMATO_HOME` に頼らないようパスで渡している。艦のタブの名前は `ship:<team.yaml の name>` (admiral のタブは `admiral`。艦の名前が admiral でもぶつからない)。接頭辞なしの古いタブが開いているセッションでは、次の `view` で `ship:` 付きに置き換える (zellij の中では rename)。

### `view open` の動き (§13)

- layout は毎回組み立て直す (キャッシュしない)。席の顔ぶれが変わっていれば次の `view open` からすぐ反映される
- zellij の外 (環境変数 `ZELLIJ` なし): layout を 1 ファイル (既定 `$YAMATO_HOME/view.kdl`、`-o` で変更可) に書き、`zellij list-sessions --short` でセッション (既定 `yamato-view`、`--session` で変更可) の有無を見る。あれば `zellij attach`、無ければ `zellij --session <name> --new-session-with-layout <file>`
- zellij の中: 艦ごとに 1 タブ分の layout を作って一時ファイルに書き、艦ごとに `zellij action new-tab --layout <file>` を呼ぶ (今のセッションにタブを足すだけで、attach し直さない)
- zellij バイナリは `YAMATO_ZELLIJ` 環境変数で差し替えられる (既定 `zellij`。`claude.py` の `YAMATO_CLAUDE` に倣う)

### 固まったとき・取り合うとき

- **原因 (推測を含む)**: ペインの `view attach` は子に `claude attach` を持つ。zellij がペインを閉じる (タブの作り直し、`delete-session`、サーバの終了) と、ペインの親プロセスに SIGHUP / SIGTERM が届く。以前はこれで `view attach` が即死し、子の `claude attach` が親なし (ppid 1) で席のセッションに繋がったまま残った。次の `view` で新しいペインが同じ席に attach すると、2 つの attach が互いを追い出し、追い出された側は「attach が終わった」で 1 秒後に付け直す、を繰り返す。固まる方は、サーバが生きているのに応答しない zellij へ `list-sessions` / `action` を投げて待ち続けるのが 1 つの形 (実機で再現はできていない)
- **直したもの**: `view attach` は SIGHUP / SIGTERM を受けたら子の `claude attach` を止めて抜ける。zellij への非対話の呼び出しは 15 秒で打ち切る (固まったサーバを待ち続けない)。attach がすぐ (10 秒未満) 終わるのが 3 回続いたら 30 秒待ち、取り合いの疑いを表示する。同じ席に別の `view attach` があれば起動時に警告する。セッションを作り直すときは標準エラーに書く
- **殺さないもの**: 残った孤児は `yamato view` が黙って止めない。`yamato view stale` で一覧し、`yamato view stale --kill` で SIGTERM を送る (親のいる attach と、利用者が自分の端末で動かした `claude attach` には触らない)。固まった zellij のセッションは、`zellij delete-session --force yamato-view` で消す (席は止まらない。ペインは `view attach` だけ)
- **固まったときの手順**: (1) `yamato view` が 15 秒で止まったらメッセージのとおり `zellij delete-session --force yamato-view` → もう一度 `yamato view`。(2) attach と detach を繰り返すなら、別の端末で `yamato view stale` → 出たものを `--kill` → `yamato view`

## 動き

- 席の「今のシフト」は `roster.json` の `seats.<seat>.sessionId` で決める (`--name` は一意でないため、名前では探さない)
- そのセッションが `claude agents --json --all` で生きている (`pid` がある) ときだけ attach する。止まったセッションに attach すると蘇るため
- 席がいなければ「待機中」と表示し、数秒ごとに確認する
- attach 中に roster が別の生きているセッションを指したら、今の attach を止めて付け直す。古いシフトは止めない (席の管理側の仕事)
- attach 先が止まると `claude attach` は自分で抜けるので、待機に戻る
- `claude attach` はペインのフォアグラウンドで動かす (macOS ではバックグラウンドだと落ちる)
- team に無い席名は起動時に断る (打ち間違いで待ち続けないように)。席の一覧は `.runtime/team.json` (`yamato up` が書く) を優先し、なければ team.yaml から読む

「今のシフト」の解決は差し替えられる (`attach.run(resolve, ...)` に任意の `resolve()` を渡す)。既定は `attach.roster_resolver`。

## 実機で分かったこと (Claude Code 2.1.283 / zellij 0.45.1)

- **`claude attach` はフルの sessionId を受け付けない** (`No job matching '<uuid>'` で exit 1)。短い `id` (`claude agents` の `id`) を渡す必要がある。照合はフルの id で行い、attach には短い id を使っている
