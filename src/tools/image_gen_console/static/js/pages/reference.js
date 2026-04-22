/** Reference page — プリセット参考メモ。
 *
 *  ChenkinNoob-XL / NoobAI / Illustrious 系モデルのプロンプト設計知見を
 *  静的に表示する。外部取得せず、変更は本ファイルの編集で行う。
 */

export function render() {
  return `
<div class="pc-card">
  <h3>📘 プリセット参考 — NoobAI / Illustrious / ChenkinNoob-XL</h3>
  <div class="pc-note" style="margin-bottom:0.8rem;">
    現在の checkpoint <code>chenkinNoobXLCKXL_v05.safetensors</code>（NoobAI-XL 1.1 派生）
    向けに集めたプロンプト設計のメモ。section preset の組み替えや自作プロンプトの参考に。
  </div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>1. モデル作者公式の推奨値（Civitai）</h3>
  <div class="pc-label">Positive</div>
  <div class="pc-prompt">masterpiece, best quality, newest, high resolution, aesthetic, excellent, year 2026</div>
  <div class="pc-label">Negative</div>
  <div class="pc-prompt">nsfw, worst quality, old, early, low quality, lowres, signature, username, logo, bad hands, mutated hands</div>
  <div class="pc-label">生成パラメータ</div>
  <ul class="ig-ref-list">
    <li>Sampler: <code>Euler a</code>（= <code>euler_ancestral</code>）</li>
    <li>Steps: <code>25–30</code></li>
    <li>CFG: <code>5–6</code></li>
    <li>解像度: <code>1024×1024</code> クラス（896×1152 / 832×1216 など SDXL 標準比も可）</li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>2. NoobAI / Illustrious コミュニティ共通知</h3>

  <div class="pc-label">推奨プロンプト順序（SeaArt 公式ガイド）</div>
  <div class="pc-prompt">&lt;count/gender&gt;, &lt;character&gt;, &lt;series&gt;, &lt;artist(s)&gt;, &lt;general tags&gt;, &lt;other tags&gt;, &lt;quality tags&gt;</div>
  <div class="pc-note">品質タグは末尾でも先頭でも可。リポの section 合成順（quality → character → scene → composition → style → user）とほぼ同じ。</div>

  <div class="pc-label" style="margin-top:0.8rem;">品質タグの序列</div>
  <div class="pc-prompt">masterpiece &gt; best quality &gt; high quality &gt; good quality &gt; normal quality &gt; low quality &gt; worst quality</div>

  <div class="pc-label" style="margin-top:0.8rem;">aesthetic タグ（NoobAI 特有）</div>
  <ul class="ig-ref-list">
    <li>positive 側: <code>very awa</code>（強力な美的スコアタグ）、<code>very aesthetic</code></li>
    <li>negative 側: <code>worst aesthetic</code></li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">年タグ</div>
  <ul class="ig-ref-list">
    <li><code>newest</code>（= ざっくり "最近のイラスト"）</li>
    <li><code>year 2026</code> / <code>year 2024</code> など具体年（Danbooru のメタタグ）</li>
    <li>negative に <code>old, early</code> を入れると古い作風を避けられる</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">アーティスト記法</div>
  <ul class="ig-ref-list">
    <li>基本は <strong>アーティスト名だけ</strong>（<code>ciloranko</code>）。<code>by xxx</code> や <code>artist: xxx</code> は不要</li>
    <li>重み付け: <code>(ciloranko:0.6)</code> のように weight を直接付ける</li>
    <li>複数混合の定石: 強め 0.6 + 弱め 0.4、合計 1.0 前後にすると破綻しにくい</li>
    <li>参考：リポ既存 artist section は <code>(artist:xxx:0.85)</code> 表記。<code>artist:</code> プレフィックスは Danbooru 曖昧性解消用で、NoobAI では不要だが付けても動く（A/B 検証余地あり）</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">キャラタグ記法（Danbooru 系）</div>
  <div class="pc-prompt">character name (series name), series name</div>
  <div class="pc-note">例: <code>ganyu (genshin impact), genshin impact</code>。シリーズ名を 2 回書くと効きが強くなる。</div>

  <div class="pc-label" style="margin-top:0.8rem;">重み記法</div>
  <ul class="ig-ref-list">
    <li><code>(tag:1.2)</code> — 強調。最大 <code>1.4</code> 程度まで、超えると破綻しやすい</li>
    <li><code>(tag:0.8)</code> — 抑制</li>
    <li><code>(a \\(b\\):0.85)</code> — 括弧を含むタグはエスケープが必要</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">パラメータ目安（コミュニティ統計）</div>
  <ul class="ig-ref-list">
    <li>Sampler: <code>Euler a</code> / <code>Euler</code>（<code>DPM++</code> 系も可）</li>
    <li>Steps: <code>28–40</code></li>
    <li>CFG: <code>3.5–5.5</code>（公式値 5–6 と上限が重なる範囲）</li>
    <li>VAE/CLIP skip: 調整不要（SDXL 組込みで十分）</li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>3. 強化 Negative 定番セット</h3>
  <div class="pc-prompt">lowres, bad anatomy, bad hands, mutated hands and fingers, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, worst aesthetic, old, early, jpeg artifacts, signature, watermark, username, logo, blurry, bad feet, bad proportions, disfigured, ugly, monochrome</div>
  <div class="pc-note">これはリポの <code>negative_general</code>（id 1, builtin）に反映済み。写実を避けたい場合は <code>photorealistic, photo, realistic</code> も追加検討。</div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>4. リポ内プリセットの状態</h3>
  <div class="pc-label">quality_illustrious_newest（id 21, builtin, 更新済み）</div>
  <div class="pc-prompt">masterpiece, best quality, amazing quality, very aesthetic, very awa, absurdres, highres, newest, year 2026</div>

  <div class="pc-label" style="margin-top:0.8rem;">negative_general（id 1, builtin, 更新済み）</div>
  <div class="pc-prompt">上記「強化 Negative」を参照</div>

  <div class="pc-label" style="margin-top:0.8rem;">character_iniwa_vrc（id 9, builtin）</div>
  <div class="pc-prompt">Iniwa_VRC, 1girl, solo, cat ears, cat tail, animal ear fluff, ahoge, skyblue hair, short hair, messy hair, blue eyes, glasses, black-framed eyewear, blue hoodie, open hoodie, off shoulder, white camisole, black choker</div>
  <div class="pc-note">LoRA <code>INIWA_NOOBXL.safetensors</code>（t2i_default 組込、model=0.45 / clip=0.9）のトリガー <code>Iniwa_VRC</code> を含む。必ずセットで使用。</div>

  <div class="pc-label" style="margin-top:0.8rem;">artist セクション（id 35–38, 非builtin）</div>
  <ul class="ig-ref-list">
    <li><code>artist_ciloranko</code>: 柔らかいパステル / 繊細な線</li>
    <li><code>artist_redum4</code>: 手描きスケッチ風</li>
    <li><code>artist_ningen_mame</code>, <code>artist_ask_askzy</code>: 未検証</li>
    <li>2 つ混ぜで変化：例 <code>(ciloranko:0.6), (redum4:0.4)</code></li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>5. 実戦テンプレ（Iniwa_VRC 想定）</h3>
  <div class="pc-label">A. 日常系ポートレート（カフェ・教室・自室）</div>
  <div class="pc-prompt">sections: quality_illustrious_newest, character_iniwa_vrc, scene_cafe, composition_cowboy_shot, style_anime_modern, negative_general
user追加: (detailed eyes:1.2), sitting at window seat, holding a latte cup, autumn afternoon, sunlight from window
params: 896x1152, 28 steps, CFG 5.5, euler_ancestral+karras</div>

  <div class="pc-label" style="margin-top:0.8rem;">B. 星空・夜景シネマティック</div>
  <div class="pc-prompt">sections: quality_illustrious_newest, character_iniwa_vrc, scene_starry_sky, composition_cowboy_shot, style_cinematic, negative_general
user追加: standing on rooftop, looking at viewer, (rim light:1.2), (bokeh:1.1), shooting star in sky
params: 896x1152, 28 steps, CFG 5.5
注意: composition_from_below だと被写体が小さくなる。cowboy_shot 推奨</div>

  <div class="pc-label" style="margin-top:0.8rem;">C. アーティスト混合・凝った立ち絵</div>
  <div class="pc-prompt">sections: quality_illustrious_newest, artist_ciloranko (0.6), artist_redum4 (0.4), character_iniwa_vrc, composition_cowboy_shot, style_anime_modern, negative_general
user追加: (knee hug pose:1.1), (soft pastel coloring:1.1), (fine line work:1.1), golden hour lighting
params: 896x1152, 32 steps, CFG 5.5</div>

  <div class="pc-label" style="margin-top:0.8rem;">D. 水彩・野外全身</div>
  <div class="pc-prompt">sections: quality_illustrious_newest, character_iniwa_vrc, scene_forest, composition_cowboy_shot, style_watercolor, negative_general
user追加: walking along forest path, (dappled sunlight:1.2), falling leaves, (detailed face:1.15)
params: 832x1216, 28 steps, CFG 5.5
注意: composition_full_body は顔ディテールが潰れやすい。cowboy_shot 推奨</div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>6. 運用上のハマり所</h3>
  <ul class="ig-ref-list">
    <li>NoobAI 系は Danbooru タグベース。自然文より <strong>タグ列挙</strong>が効く（短い自然文の補助は OK）</li>
    <li>weight は <code>1.4</code> を超えると高確率で破綻（指・目・構図）</li>
    <li>artist 混合は合計 <code>1.0</code> 前後まで。超えると塗りが濁る</li>
    <li>全身構図（<code>full body</code>）+ 1024 付近の解像度は顔が潰れやすい → <code>cowboy shot</code> か <code>upper body</code> を使うか、<code>(detailed face:1.15)</code> で補強</li>
    <li>ポーズ・衣装を <code>(...:1.2)</code>〜<code>(...:1.4)</code> で強調すると LoRA の引き寄せが効く</li>
    <li>NSFW 不要なら negative に <code>nsfw</code> を入れると安定度が増す</li>
    <li>写実を避けたい場合は negative に <code>photorealistic, photo, realistic, 3d</code> を追加</li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>7. Sources / 出典</h3>
  <ul class="ig-ref-list">
    <li><a href="https://civitai.com/models/2167995/chenkin-noob-xl-ckxl" target="_blank" rel="noopener">Chenkin Noob XL (CKXL) v0.5 | Civitai</a></li>
    <li><a href="https://docs.seaart.ai/guide-1/6-permanent-events/high-quality-models-recommendation/noobai-xl" target="_blank" rel="noopener">NoobAI XL | SeaArt Guide</a></li>
    <li><a href="https://github.com/regiellis/ComfyUI-EasyNoobai" target="_blank" rel="noopener">ComfyUI-EasyNoobai (GitHub)</a></li>
    <li><a href="https://civitai.com/articles/9158/negative-prompt-for-noobai-xl-nai-xl-or-illustrious" target="_blank" rel="noopener">Negative prompt for NoobAI-XL / Illustrious | Civitai</a></li>
    <li><a href="https://civitai.com/articles/8380/tips-for-illustrious-xl-prompting-updates" target="_blank" rel="noopener">Tips for Illustrious XL Prompting | Civitai</a></li>
  </ul>
  <div class="pc-note" style="margin-top:0.6rem;">最終更新: 2026-04-22</div>
</div>
`;
}

export async function mount() {
  // 静的ページにつき mount 時の処理なし
}
