/** Reference page — ChenkinNoob-XL v0.5 固有メモ。
 *
 *  共通の NoobAI/Illustrious 知見は `/#/reference` へ。
 */

export function render() {
  return `
<div class="pc-card">
  <h3>📙 ChenkinNoob-XL (CKXL) v0.5</h3>
  <ul class="ig-ref-list">
    <li>ファイル名: <code>chenkinNoobXLCKXL_v05.safetensors</code></li>
    <li>サイズ: 6.46 GB（fp16 SafeTensor）</li>
    <li>ベース: <strong>Laxhar/noobai-XL-1.1</strong></li>
    <li>学習データ: ~12M 画像（9M Danbooru アニメ + 2.17M ゲームコンセプト / 西洋系）</li>
    <li>配布: <a href="https://civitai.com/models/2167995/chenkin-noob-xl-ckxl" target="_blank" rel="noopener">Civitai #2167995</a></li>
  </ul>
  <div class="pc-note">
    現在のボットの <strong>既定 checkpoint</strong>。Iniwa_VRC キャラ LoRA（<code>INIWA_NOOBXL.safetensors</code>）は本モデル向けに学習されているため、本モデルと組み合わせて使うのが最適。
  </div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>1. 作者公式の推奨設定</h3>
  <div class="pc-label">Positive</div>
  <div class="pc-prompt">masterpiece, best quality, newest, high resolution, aesthetic, excellent, year 2026</div>
  <div class="pc-label">Negative</div>
  <div class="pc-prompt">nsfw, worst quality, old, early, low quality, lowres, signature, username, logo, bad hands, mutated hands</div>
  <div class="pc-label">パラメータ</div>
  <ul class="ig-ref-list">
    <li>Sampler: <code>Euler a</code>（= <code>euler_ancestral</code>）</li>
    <li>Steps: <code>25–30</code></li>
    <li>CFG: <code>5–6</code></li>
    <li>解像度: <code>1024×1024</code> / <code>896×1152</code> / <code>832×1216</code> など SDXL 標準比</li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>2. 推奨ワークフロー / プリセット</h3>
  <div class="pc-label">Preset</div>
  <ul class="ig-ref-list">
    <li><strong><code>t2i_default</code></strong>（LoRA 付、id=18）— INIWA_NOOBXL を model=0.45 / clip=0.9 で組み込み済</li>
    <li><code>t2i_default_vanilla</code>（LoRA 無、id=21）— LoRA を当てたくない場合の汎用用途向け</li>
  </ul>
  <div class="pc-label" style="margin-top:0.8rem;">section_preset 推奨セット</div>
  <ul class="ig-ref-list">
    <li>quality: <code>quality_chenkin_official</code>（id=42, <strong>作者公式タグ</strong>）または <code>quality_illustrious_newest</code>（id=21, 汎用拡張版）</li>
    <li>character: <code>character_iniwa_vrc</code>（id=9, LoRA トリガー <code>Iniwa_VRC</code> 含む）</li>
    <li>negative: <code>negative_chenkin_official</code>（id=43, <strong>作者公式</strong>）または <code>negative_general</code>（id=1, 拡張版）</li>
  </ul>
  <div class="pc-note">公式版はタグ数が少なく "作者の意図通りに出やすい"、拡張版は破綻除去を強めに効かせたい時向け。</div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>3. LoRA 互換性</h3>
  <ul class="ig-ref-list">
    <li><code>INIWA_NOOBXL.safetensors</code> は本モデル向けに学習された <strong>キャラ LoRA</strong></li>
    <li>トリガータグ: <code>Iniwa_VRC</code>（必須。Positive 先頭付近に置く）</li>
    <li>推奨 strength: model=0.45 / clip=0.9（既存 <code>t2i_default</code> 設定値）</li>
    <li>他の checkpoint（AsumaXL 等）でもロード自体は可能だが、塗り・絵柄がずれるため非推奨</li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>4. 用途の目安</h3>
  <ul class="ig-ref-list">
    <li>✅ <strong>Iniwa_VRC キャラを描くとき</strong>（最適、LoRA が本モデル向け）</li>
    <li>✅ アニメ寄り・ゲームコンセプト寄りのイラスト全般</li>
    <li>△ 実写寄りや西洋油絵テイスト（データ混入はあるが傾向は弱い）</li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>5. 実戦テンプレ</h3>
  <div class="pc-label">A. 日常系ポートレート（Iniwa_VRC）</div>
  <div class="pc-prompt">checkpoint: chenkinNoobXLCKXL_v05 / preset: t2i_default
sections: quality_illustrious_newest, character_iniwa_vrc, scene_cafe, composition_cowboy_shot, style_anime_modern, negative_general
user追加: (detailed eyes:1.2), sitting at window seat, holding a latte cup, autumn afternoon, sunlight from window
params: 896x1152, 28 steps, CFG 5.5, euler_ancestral+karras</div>

  <div class="pc-label" style="margin-top:0.8rem;">B. 星空・夜景シネマティック（Iniwa_VRC）</div>
  <div class="pc-prompt">checkpoint: chenkinNoobXLCKXL_v05 / preset: t2i_default
sections: quality_illustrious_newest, character_iniwa_vrc, scene_starry_sky, composition_cowboy_shot, style_cinematic, negative_general
user追加: standing on rooftop, looking at viewer, (rim light:1.2), (bokeh:1.1), shooting star in sky
params: 896x1152, 28 steps, CFG 5.5</div>

  <div class="pc-label" style="margin-top:0.8rem;">C. アーティスト混合・凝った立ち絵（Iniwa_VRC）</div>
  <div class="pc-prompt">checkpoint: chenkinNoobXLCKXL_v05 / preset: t2i_default
sections: quality_illustrious_newest, artist_ciloranko (0.6), artist_redum4 (0.4), character_iniwa_vrc, composition_cowboy_shot, style_anime_modern, negative_general
user追加: (knee hug pose:1.1), (soft pastel coloring:1.1), (fine line work:1.1), golden hour lighting
params: 896x1152, 32 steps, CFG 5.5</div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>6. Sources</h3>
  <ul class="ig-ref-list">
    <li><a href="https://civitai.com/models/2167995/chenkin-noob-xl-ckxl" target="_blank" rel="noopener">Chenkin Noob XL (CKXL) v0.5 | Civitai</a></li>
    <li><a href="https://huggingface.co/Laxhar/noobai-XL-1.0" target="_blank" rel="noopener">Laxhar/noobai-XL (base) | HuggingFace</a></li>
  </ul>
</div>
`;
}

export async function mount() { /* static */ }
