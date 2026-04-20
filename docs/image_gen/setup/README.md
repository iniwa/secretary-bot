# 画像生成機能 セットアップ マスターインデックス

> **このドキュメントを見ながら全部セットアップして** — Claude Code に渡す用のトップ文書。
> 各 PC で順番に作業する。ひとつの PC に Claude Code が入った状態で、そのマシン担当の節だけを実行すれば足りる構成。

---

## 0. 全体像

| PC | 役割 | IP | 担当ガイド |
|---|---|---|---|
| **Raspberry Pi** | Bot 本体・Dispatcher・WebGUI | — | [pi.md](pi.md) |
| **MainPC** | ComfyUI / kohya 実行・画像生成の主系 | 192.168.1.210 | [mainpc.md](mainpc.md) |
| **SubPC** | ComfyUI / kohya 実行・負荷分散 + LoRA 副系 | 192.168.1.211 | [subpc.md](subpc.md) |

構成図:

```
[Discord] [WebGUI] ──┐
                     ↓
               [Pi: Dispatcher]
                  ↙        ↘
      [MainPC Agent]   [SubPC Agent]   ← :7777
         ComfyUI          ComfyUI
         kohya_ss         kohya_ss
              ↘          ↙
                 [NAS]   ← SMB (outputs / models / LoRA)
```

---

## 1. 作業順序（必ずこの順で実施）

事前合意: **AGENT_SECRET_TOKEN と NAS 認証情報を先に決めておく**。3 台すべてで同じ値を使う。

| # | 作業 | 担当 PC | 参照 |
|---|---|---|---|
| 1 | NAS 初期ディレクトリ作成・権限・モデル正本配置 | 任意（Pi か MainPC） | [nas_setup.md](../nas_setup.md) |
| 2 | MainPC セットアップ（ComfyUI / kohya / Agent 起動） | MainPC | [mainpc.md](mainpc.md) |
| 3 | SubPC セットアップ（MainPC 完了後に差分実行） | SubPC | [subpc.md](subpc.md) |
| 4 | Pi セットアップ（bot の image_gen 有効化） | Pi | [pi.md](pi.md) |
| 5 | E2E 動作確認 | Pi から | [verify.md](verify.md) |

**並列化しないこと**: MainPC → SubPC の順序は固定（SubPC は MainPC の custom_nodes snapshot を取り込む）。Pi は MainPC/SubPC 両方が立ち上がってから。

---

## 2. 共通前提（どの PC からでも確認）

### 2.1 値の事前決定
以下 4 点を **最初に決め、3 台で共通化** する:

| キー | 例 | 用途 |
|---|---|---|
| `AGENT_SECRET_TOKEN` | ランダム 32 文字 | Pi ↔ Windows Agent 認証 |
| `NAS_SMB_HOST` | 192.168.1.20 | NAS ホスト |
| `NAS_SMB_SHARE` | `ai-image` | 共有名 |
| `NAS_SMB_USER` / `NAS_SMB_PASSWORD` | — | SMB 認証 |

### 2.2 リポジトリと Claude Code
- リポジトリ: `https://github.com/iniwa/secretary-bot`（実体は Gitea ミラーあり）
- Claude Code 起動時は **そのマシンの担当ガイドだけをコンテキストに入れる**
- 各ガイドは自己完結。マシン跨ぎの作業は必ず「次ステップ」節で次のガイドへ誘導される

---

## 3. Claude Code への依頼テンプレート

### MainPC / SubPC
```
docs/image_gen/setup/mainpc.md （または _subpc.md）を見て、記載の手順を順番に実行してください。
[要ユーザー確認] タグが付いた箇所だけ、その都度確認を求めてください。
途中でエラーが出たら、同ドキュメントの §9 トラブルシュート節を参照して切り分けてください。
```

### Pi
```
docs/image_gen/setup/pi.md を見て、記載の手順を順番に実行してください。
必要に応じて `docs/image_gen/nas_setup.md` の Pi 側手順を併用してください。
Portainer の操作とコード更新を含むので、[要ユーザー確認] タグでは必ず確認を取ってください。
```

### 全体動作確認
```
docs/image_gen/setup/verify.md の §1 E2E 7 段階チェック表を上から実行し、
失敗段階があれば同ドキュメントの §3 トラブルシュート対応節まで追ってください。
最後にチェック表を表形式で報告してください。
```

---

## 4. 設計・API 参照ドキュメント

手順書では説明を最小限にしているため、Claude Code が判断を迫られる場面では以下を参照させる:

| ドキュメント | 用途 |
|---|---|
| [design.md](../design.md) | 設計全体・状態機械・エラー階層 |
| [api.md](../api.md) | Windows Agent API 仕様（20 エンドポイント） |
| [nas_setup.md](../nas_setup.md) | NAS 初期化・マウント手順 |
| [preset_compat.md](../preset_compat.md) | Main/Sub capability 差分許容範囲 |

---

## 5. Phase 1 スコープ（ここで完成するもの）

- t2i（text-to-image）最小パス: WebGUI から prompt 投入 → MainPC ComfyUI 実行 → NAS 保存 → Gallery 表示
- 既定プリセット `t2i_base` のみ対応
- MainPC 優先 / SubPC 手動フェイルオーバー（自動は Phase 2）
- LoRA 学習・Discord スラッシュコマンド・プリセット管理 UI は **Phase 2 以降で別途実装**

Phase 1 未対応項目の一覧は [verify.md §6 既知の制約](verify.md) を参照。

---

## 6. チェックポイント（完了判定）

3 台すべてで以下が通れば Phase 1 セットアップ完了:

- [ ] `curl -H "X-Agent-Token:$T" http://192.168.1.210:7777/health` → 200
- [ ] `curl -H "X-Agent-Token:$T" http://192.168.1.211:7777/health` → 200
- [ ] Pi の WebGUI でサイドバー `Image Gen` が表示され、workflow に `t2i_base` がある
- [ ] 最小プロンプトで投入したジョブが `done` になり、Gallery に画像が表示される
- [ ] `/mnt/secretary-bot/ai-image/outputs/YYYY-MM/YYYY-MM-DD/` に PNG と `.sha256` サイドカーが保存される

すべて通ったら、このドキュメントの作業は終了。
