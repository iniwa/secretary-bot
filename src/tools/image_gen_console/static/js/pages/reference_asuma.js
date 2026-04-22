/** Reference page — AsumaXL-Illustrious v4 固有メモ。
 *
 *  共通の NoobAI/Illustrious 知見は `/#/reference` へ。
 */

export function render() {
  return `
<div class="pc-card">
  <h3>📗 AsumaXL-Illustrious v4</h3>
  <ul class="ig-ref-list">
    <li>ファイル名: <code>asumaxlIllustrious_v4.safetensors</code></li>
    <li>サイズ: 6.46 GB（fp16 SafeTensor）</li>
    <li>SHA256: <code>6553ad47ffcc8edfa791fcfc1d2aec9493cdb99b1c0409f3293d7729001010f0</code></li>
    <li>ベース: <strong>Illustrious XL</strong>（NoobAI と同系なので共通知見はそのまま流用可）</li>
    <li>v4 の変更点: 色調・体のプロポーションを調整</li>
    <li>配布: <a href="https://civitai.com/models/1177258/asumaxl-illustrious" target="_blank" rel="noopener">Civitai #1177258</a></li>
  </ul>
  <div class="pc-note">
    本ボットの <strong>代替 checkpoint</strong>。Iniwa_VRC 以外のキャラ・汎用イラスト・風景などに。
  </div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>1. 作者公式の推奨設定</h3>
  <ul class="ig-ref-list">
    <li>Sampler: <code>Euler a</code>（= <code>euler_ancestral</code>）</li>
    <li>Steps: <code>25–30</code></li>
    <li>CFG: 明示なし（<code>5–6</code> で安全、NoobXL と同程度）</li>
    <li>解像度: 明示なし（<code>1024×1024</code> / <code>896×1152</code> / <code>832×1216</code> など SDXL 標準比）</li>
  </ul>
  <div class="pc-note">
    サンプルプロンプトは作者ページに記載なし。<a href="#/reference">共通知見ページ</a>の NoobAI/Illustrious 推奨（品質タグ・aesthetic・年タグ等）がそのまま使える。
  </div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>2. 推奨ワークフロー / プリセット</h3>
  <div class="pc-label">Preset</div>
  <ul class="ig-ref-list">
    <li><strong><code>t2i_default_vanilla</code></strong>（LoRA 無、id=21）— <strong>AsumaXL 使用時の既定</strong></li>
    <li><code>t2i_default</code> は NoobXL 向け LoRA が組み込まれているため、AsumaXL と組み合わせるのは非推奨</li>
  </ul>
  <div class="pc-label" style="margin-top:0.8rem;">section_preset 推奨セット</div>
  <ul class="ig-ref-list">
    <li>quality: <code>quality_asuma_official</code>（id=44, <strong>Illustrious コミュニティ推奨</strong>）または <code>quality_illustrious_newest</code>（id=21, 拡張版）</li>
    <li>character: <strong>使わない</strong>（<code>character_iniwa_vrc</code> は LoRA 前提）</li>
    <li>negative: <code>negative_asuma_official</code>（id=45, <strong>Illustrious 用拡張</strong>: displeasing / oldest / artistic failure 含む）または <code>negative_general</code>（id=1, NoobAI 寄り）</li>
    <li>artist: 必要に応じて <code>artist_ciloranko</code> 等を混ぜる（検証推奨）</li>
  </ul>
  <div class="pc-note">Illustrious 特有の <code>displeasing</code> / <code>very displeasing</code> / <code>oldest</code> タグを使いたければ公式版。</div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>3. LoRA 互換性</h3>
  <ul class="ig-ref-list">
    <li>Illustrious XL ベースなので <strong>Illustrious 向けキャラ LoRA</strong> を載せられる可能性が高い（A/B 検証推奨）</li>
    <li><code>INIWA_NOOBXL</code> は NoobXL 向けに学習されているため本モデルでは塗りがずれやすい</li>
    <li>LoRA 適用時は <code>t2i_default</code> のような LoRA 付き workflow を使うか、専用 workflow を別途用意</li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>4. 用途の目安</h3>
  <ul class="ig-ref-list">
    <li>✅ <strong>汎用イラスト（Iniwa_VRC 以外）</strong>: オリキャラ・既存キャラ・一般シーン</li>
    <li>✅ <strong>風景・背景のみ</strong>（人物なし）</li>
    <li>✅ <strong>Illustrious 系 LoRA と組み合わせたい場合</strong>（要専用 workflow）</li>
    <li>△ Iniwa_VRC を描くとき（LoRA 互換が無いため、ChenkinNoob-XL を使うべき）</li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>5. 実戦テンプレ</h3>
  <div class="pc-label">A. 汎用オリキャラ立ち絵</div>
  <div class="pc-prompt">checkpoint: asumaxlIllustrious_v4 / preset: t2i_default_vanilla
sections: quality_illustrious_newest, negative_general
user追加: 1girl, solo, silver hair, long hair, blue eyes, white dress, standing in a sunlit meadow, flowers, (detailed face:1.15), cowboy shot, looking at viewer
params: 896x1152, 28 steps, CFG 5.5, euler_ancestral+karras</div>

  <div class="pc-label" style="margin-top:0.8rem;">B. 風景（人物なし）</div>
  <div class="pc-prompt">checkpoint: asumaxlIllustrious_v4 / preset: t2i_default_vanilla
sections: quality_illustrious_newest, negative_general
user追加: no humans, (scenery:1.2), cyberpunk city at night, neon lights, rain, wet road reflections, cinematic composition, volumetric lighting, cool colors
params: 1024x1024, 30 steps, CFG 5.5</div>

  <div class="pc-label" style="margin-top:0.8rem;">C. artist 混合・凝ったスタイル</div>
  <div class="pc-prompt">checkpoint: asumaxlIllustrious_v4 / preset: t2i_default_vanilla
sections: quality_illustrious_newest, artist_ciloranko (0.6), artist_redum4 (0.4), negative_general
user追加: 1girl, solo, long black hair, red eyes, school uniform, rooftop, sunset, golden hour, cinematic lighting, (detailed face:1.15), cowboy shot, looking at viewer, wind
params: 896x1152, 32 steps, CFG 5.5</div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>6. Sources</h3>
  <ul class="ig-ref-list">
    <li><a href="https://civitai.com/models/1177258/asumaxl-illustrious" target="_blank" rel="noopener">AsumaXL-Illustrious v4 | Civitai</a></li>
    <li><a href="https://huggingface.co/Qnuk/Illustrious_Models_AsumaXL" target="_blank" rel="noopener">Qnuk/Illustrious_Models_AsumaXL（HF ミラー、V5–V7 あり）</a></li>
  </ul>
</div>
`;
}

export async function mount() { /* static */ }
