/** Reference page — NoobAI / Illustrious 系モデルの共通知見。
 *
 *  モデル固有の情報（ファイル名・推奨 preset・LoRA 互換性など）は
 *  `reference_chenkin.js` / `reference_asuma.js` など、checkpoint 別のページへ。
 */

export function render() {
  return `
<div class="pc-card">
  <h3>📘 プリセット参考（共通知見）</h3>
  <div class="pc-note" style="margin-bottom:0.8rem;">
    NoobAI / Illustrious 系モデル全般に通用するプロンプト設計の知見をまとめたメモ。
    モデル固有の情報はサイドバーの checkpoint 別ページを参照:
  </div>
  <ul class="ig-ref-list">
    <li><a href="#/reference/chenkin">📙 ChenkinNoob-XL v0.5</a> — 既定 checkpoint（Iniwa_VRC LoRA 付）</li>
    <li><a href="#/reference/asuma">📗 AsumaXL-Illustrious v4</a> — 代替 checkpoint（LoRA 無、汎用）</li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>1. プロンプトの基本構造</h3>

  <div class="pc-label">推奨プロンプト順序（SeaArt 公式ガイド準拠）</div>
  <div class="pc-prompt">&lt;count/gender&gt;, &lt;character&gt;, &lt;series&gt;, &lt;artist(s)&gt;, &lt;general tags&gt;, &lt;other tags&gt;, &lt;quality tags&gt;</div>
  <div class="pc-note">品質タグは末尾でも先頭でも可。リポの section 合成順（quality → character → scene → composition → style → user）はこの順序に準じている。</div>

  <div class="pc-label" style="margin-top:0.8rem;">品質タグ序列</div>
  <div class="pc-prompt">masterpiece &gt; best quality &gt; high quality &gt; good quality &gt; normal quality &gt; low quality &gt; worst quality</div>

  <div class="pc-label" style="margin-top:0.8rem;">aesthetic タグ（NoobAI 特有、強力）</div>
  <ul class="ig-ref-list">
    <li>positive 側: <code>very awa</code>, <code>very aesthetic</code></li>
    <li>negative 側: <code>worst aesthetic</code></li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">年タグ</div>
  <ul class="ig-ref-list">
    <li><code>newest</code> = ざっくり "最近のイラスト"</li>
    <li><code>year 2026</code> / <code>year 2024</code> など具体年（Danbooru のメタタグ）</li>
    <li>negative に <code>old, early</code> を入れると古い作風を避けられる</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">重み記法</div>
  <ul class="ig-ref-list">
    <li><code>(tag:1.2)</code> — 強調。上限 <code>1.4</code> 程度、超えると破綻しやすい</li>
    <li><code>(tag:0.8)</code> — 抑制</li>
    <li><code>(a \\(b\\):0.85)</code> — 括弧入りタグはバックスラッシュでエスケープ</li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>2. アーティスト / キャラタグ</h3>

  <div class="pc-label">アーティスト記法</div>
  <ul class="ig-ref-list">
    <li>基本は <strong>アーティスト名だけ</strong>（<code>ciloranko</code>）。<code>by xxx</code> や <code>artist: xxx</code> は不要</li>
    <li>重み: <code>(ciloranko:0.6)</code></li>
    <li>複数混合の定石: 強め 0.6 + 弱め 0.4、合計 1.0 前後が無難</li>
    <li>リポ既存 artist section は <code>(artist:xxx:0.85)</code> 表記。<code>artist:</code> プレフィックスは Danbooru 曖昧性解消用で、NoobAI では不要だが付けても動く</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">キャラタグ（Danbooru 系）</div>
  <div class="pc-prompt">character name (series name), series name</div>
  <div class="pc-note">例: <code>ganyu (genshin impact), genshin impact</code>。シリーズ名を 2 回書くと効きが強くなる。</div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>3. 強化 Negative（定番セット）</h3>
  <div class="pc-prompt">lowres, bad anatomy, bad hands, mutated hands and fingers, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, worst aesthetic, old, early, jpeg artifacts, signature, watermark, username, logo, blurry, bad feet, bad proportions, disfigured, ugly, monochrome</div>
  <div class="pc-note">リポの <code>negative_general</code>（id 1, builtin）に反映済。写実を避けたい場合は <code>photorealistic, photo, realistic, 3d</code> を追加検討。NSFW 不要なら <code>nsfw</code> も追加で安定度↑。</div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>4. パラメータ目安</h3>
  <ul class="ig-ref-list">
    <li>Sampler: <code>Euler a</code>（= <code>euler_ancestral</code>）を基準。<code>DPM++</code> 系も可</li>
    <li>Steps: <code>28–32</code>（作者推奨 25-30、コミュニティ 28-40）</li>
    <li>CFG: <code>5.0–5.5</code>（作者推奨 5-6、コミュニティ 3.5-5.5）</li>
    <li>Scheduler: <code>karras</code> または <code>normal</code></li>
    <li>解像度: <code>1024×1024</code> / <code>896×1152</code> / <code>832×1216</code>（SDXL 標準比）</li>
    <li>VAE/CLIP skip: 調整不要（SDXL 組込で十分）</li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>5. リポ内 section_preset の状態</h3>
  <div class="pc-label">quality_illustrious_newest（id 21, builtin, 更新済）</div>
  <div class="pc-prompt">masterpiece, best quality, amazing quality, very aesthetic, very awa, absurdres, highres, newest, year 2026</div>

  <div class="pc-label" style="margin-top:0.8rem;">negative_general（id 1, builtin, 更新済）</div>
  <div class="pc-prompt">上記「強化 Negative」を参照</div>

  <div class="pc-label" style="margin-top:0.8rem;">artist セクション（id 35–38, 非builtin）</div>
  <ul class="ig-ref-list">
    <li><code>artist_ciloranko</code>: 柔らかいパステル / 繊細な線</li>
    <li><code>artist_redum4</code>: 手描きスケッチ風</li>
    <li><code>artist_ningen_mame</code>, <code>artist_ask_askzy</code>: 未検証</li>
    <li>2 つ混ぜ例: <code>(ciloranko:0.6), (redum4:0.4)</code></li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">character_iniwa_vrc（id 9, builtin）</div>
  <div class="pc-prompt">Iniwa_VRC, 1girl, solo, cat ears, cat tail, animal ear fluff, ahoge, skyblue hair, short hair, messy hair, blue eyes, glasses, black-framed eyewear, blue hoodie, open hoodie, off shoulder, white camisole, black choker</div>
  <div class="pc-note">LoRA <code>INIWA_NOOBXL.safetensors</code> 向けで、<strong>ChenkinNoob-XL + t2i_default との併用が前提</strong>。</div>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>6. 運用上のハマり所</h3>
  <ul class="ig-ref-list">
    <li>NoobAI 系は Danbooru タグベース。自然文より <strong>タグ列挙</strong>が効く</li>
    <li>weight <code>1.4</code> を超えると高確率で破綻（指・目・構図）</li>
    <li>artist 混合は合計 <code>1.0</code> 前後まで。超えると塗りが濁る</li>
    <li>全身構図（<code>full body</code>）+ 1024 付近は顔が潰れやすい → <code>cowboy shot</code> / <code>upper body</code> または <code>(detailed face:1.15)</code> で補強</li>
    <li>ポーズ・衣装は <code>(...:1.2)</code>〜<code>(...:1.4)</code> で強調すると LoRA の引き寄せが効く</li>
    <li>写実を避けたい場合は negative に <code>photorealistic, photo, realistic, 3d</code></li>
  </ul>
</div>

<div class="pc-card" style="margin-top:1rem;">
  <h3>7. Sources（共通知見の出典）</h3>
  <ul class="ig-ref-list">
    <li><a href="https://docs.seaart.ai/guide-1/6-permanent-events/high-quality-models-recommendation/noobai-xl" target="_blank" rel="noopener">NoobAI XL | SeaArt Guide</a></li>
    <li><a href="https://github.com/regiellis/ComfyUI-EasyNoobai" target="_blank" rel="noopener">ComfyUI-EasyNoobai (GitHub)</a></li>
    <li><a href="https://civitai.com/articles/9158/negative-prompt-for-noobai-xl-nai-xl-or-illustrious" target="_blank" rel="noopener">Negative prompt for NoobAI-XL / Illustrious | Civitai</a></li>
    <li><a href="https://civitai.com/articles/8380/tips-for-illustrious-xl-prompting-updates" target="_blank" rel="noopener">Tips for Illustrious XL Prompting | Civitai</a></li>
  </ul>
  <div class="pc-note" style="margin-top:0.6rem;">最終更新: 2026-04-22</div>
</div>
`;
}

export async function mount() { /* static */ }
