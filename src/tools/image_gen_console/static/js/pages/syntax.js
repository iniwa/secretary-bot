/** Syntax page — プロンプトの構文リファレンス。
 *
 *  - Wildcard / Dynamic Prompts（本リポ独自実装: src/units/image_gen/wildcard_expander.py）
 *  - ComfyUI / A1111 系の重み付け・スケジューリング（ComfyUI の
 *    ADVANCED_CLIP_TEXT_ENCODE 相当、A1111 WebUI 互換記法）
 *
 *  本ページは純粋なドキュメント。mount 時にクリップボードコピーだけ結線。
 */

export function render() {
  return `
<div class="pc-card">
  <h3>📖 プロンプト構文リファレンス</h3>
  <div class="pc-note">
    本リポの画像生成で使える記法のまとめ。
    <strong>Wildcard セクション</strong>はこのプロジェクト独自実装
    （<code>src/units/image_gen/wildcard_expander.py</code>）で、
    ジョブ投入前にサーバ側で展開される。
    <strong>ComfyUI / A1111 セクション</strong>はモデルに渡されるプロンプト側の記法で、
    どちらも <a href="#/wildcards">Wildcards</a> / <a href="#/generate">Generate</a>
    ページから共通に使える。
  </div>
</div>

<!-- ========== Wildcard（独自実装） ========== -->
<div class="pc-card" style="margin-top:1rem;">
  <h3>🎲 Wildcard / Dynamic Prompts（このリポの実装）</h3>
  <div class="pc-note">
    サーバ側（Python）で展開 → 展開後のテキストをモデルに渡す。
    つまり <strong>ComfyUI には届かない</strong>ので、サーバ側で壊れると警告 /
    プレビュー結果が空になる。プレビューは
    <a href="#/wildcards">Wildcards</a> 下部の「🔍 プレビュー」で試せる。
  </div>

  <div class="pc-label" style="margin-top:0.8rem;">① 択一ランダム <code>{a|b|c}</code></div>
  <div class="pc-prompt" data-copy>1girl, {short|medium|long} hair</div>
  <ul class="ig-ref-list">
    <li><code>|</code> で区切った候補から均等ランダムで 1 つ選ぶ。</li>
    <li>空候補も有効: <code>{smile|}</code> は 50% で何も出さない（条件付き出力）。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">② 重み付きランダム <code>{w::a|w::b}</code></div>
  <div class="pc-prompt" data-copy>{2::blonde hair|1::silver hair|1::black hair}</div>
  <ul class="ig-ref-list">
    <li>重みは<strong>非負の数値</strong>。省略すると <code>1.0</code>。</li>
    <li>合計は自動正規化（上の例なら 50% / 25% / 25%）。</li>
    <li>全部 0 なら均等ランダムにフォールバック。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">③ 整数レンジ <code>{1-5}</code></div>
  <div class="pc-prompt" data-copy>{1-5} braids, {3-8} earrings</div>
  <ul class="ig-ref-list">
    <li>両端 inclusive。<code>{5-1}</code> のように順序逆でも OK。</li>
    <li>負値も可: <code>{-3--1}</code> は -3〜-1 の整数。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">④ 辞書参照 <code>__name__</code></div>
  <div class="pc-prompt" data-copy>1girl, __hair_colors__, __hair_styles__, __eye_colors__</div>
  <ul class="ig-ref-list">
    <li><a href="#/wildcards">Wildcards</a> ページで登録した辞書名。英数 / <code>_ . -</code>、最大 64 文字。</li>
    <li>辞書は「1 行 = 1 候補」、<code>#</code> で始まる行と空行は無視。</li>
    <li>未定義の <code>__foo__</code> は原文のまま残り、warnings に記録される（エラーにはならない）。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">⑤ エスケープ</div>
  <div class="pc-prompt" data-copy>literal \\{ brace, \\| pipe, path\\\\to\\\\file</div>
  <ul class="ig-ref-list">
    <li>バックスラッシュ 1 文字で任意の次 1 文字をリテラル化。<code>\\{</code> <code>\\|</code> <code>\\}</code> <code>\\:</code> <code>\\_</code> など。</li>
    <li>バックスラッシュ自体は <code>\\\\</code>。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">⚠ 非対応・注意事項</div>
  <ul class="ig-ref-list">
    <li><strong>入れ子は非対応</strong>。<code>{a|{b|c}}</code> は最初の <code>}</code> で閉じ、残りはリテラル扱い。</li>
    <li>置換結果に <code>{...}</code> や <code>__foo__</code> が現れても<strong>再展開しない</strong>。</li>
    <li>決定的展開は <code>rng_seed</code> で制御（プレビューの seed 欄）。同じ seed + 同じ入力 + 同じ辞書 → 同じ結果。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">💡 実用例</div>
  <div class="pc-prompt" data-copy>1girl, solo, {2::__hair_colors__|1::silver hair}, __hair_styles__, __eye_colors__, __expressions__, __outfits_casual__, __backgrounds_outdoor__, {__lighting__|:|soft lighting}, __camera_framing__, masterpiece, best quality</div>
  <div class="pc-note">髪色は「通常の辞書参照を 2/3、指定色を 1/3」で混ぜる。lighting は 1/3 の確率で省略 → 辞書選択 → soft lighting 固定 のいずれか。</div>
</div>

<!-- ========== ComfyUI 重み付け ========== -->
<div class="pc-card" style="margin-top:1rem;">
  <h3>⚖ ComfyUI / A1111 系の重み付け</h3>
  <div class="pc-note">
    Wildcard の展開後に ComfyUI の CLIP Text Encode ノードに渡される部分。
    上限の目安は <code>1.4</code>。超えると色飽和・形状破綻が起きやすい。
  </div>

  <div class="pc-label" style="margin-top:0.8rem;">① 数値指定 <code>(tag:重み)</code></div>
  <div class="pc-prompt" data-copy>(masterpiece:1.2), (blurry:0.6)</div>
  <ul class="ig-ref-list">
    <li>一般的な範囲: <code>0.6〜1.4</code>。</li>
    <li>複数タグにまとめて掛けられる: <code>(best quality, ultra-detailed:1.1)</code>。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">② A1111 略記（丸括弧・角括弧）</div>
  <div class="pc-prompt" data-copy>((highly detailed)), [dull]</div>
  <ul class="ig-ref-list">
    <li><code>(tag)</code> = <code>(tag:1.1)</code>、<code>((tag))</code> = <code>(tag:1.21)</code>（1.1 の重ねがけ）。</li>
    <li><code>[tag]</code> = <code>(tag:0.9)</code>、<code>[[tag]]</code> = <code>(tag:0.81)</code>。</li>
    <li>ComfyUI もこの記法を解釈するが、<strong>数値指定のほうが意図が明確</strong>。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">③ 括弧を含むタグのエスケープ</div>
  <div class="pc-prompt" data-copy>(hatsune miku \\(cosplay\\):0.85)</div>
  <ul class="ig-ref-list">
    <li>Danbooru 系タグに含まれる <code>(</code> / <code>)</code> は <code>\\(</code> / <code>\\)</code> でエスケープ。</li>
    <li>これは ComfyUI / A1111 の構文。<strong>Wildcard 側のエスケープとは別レイヤ</strong>（先に Wildcard 展開 → そのまま ComfyUI へ）。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">④ プロンプトスケジューリング（ComfyUI/A1111 共通）</div>
  <div class="pc-prompt" data-copy>[red hair:blue hair:0.5], [dog::0.3], [:detailed face:0.7]</div>
  <ul class="ig-ref-list">
    <li><code>[A:B:0.5]</code> — 全体の 50% まで A、それ以降 B。</li>
    <li><code>[A::0.3]</code> — 最初の 30% だけ A（以降は何も出さない）。</li>
    <li><code>[:B:0.7]</code> — 最初は何も、70% から B を注入。</li>
    <li>値は <code>0〜1</code> の割合（合計ステップ比）。整数ならステップ番号指定。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">⑤ 交互プロンプト（A1111 <code>[a|b]</code>）</div>
  <div class="pc-prompt" data-copy>[cat|dog] creature</div>
  <ul class="ig-ref-list">
    <li>ステップ毎に A / B を交互に通す。ComfyUI でも同構文で解釈される（ノードによる）。</li>
    <li><strong>Wildcard の <code>{a|b}</code>（ランダム 1 回選択）とは全く別物</strong>なので混同注意。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">⑥ BREAK（A1111）</div>
  <div class="pc-prompt" data-copy>1girl, red dress BREAK forest, flowers</div>
  <ul class="ig-ref-list">
    <li>CLIP の 75 トークンチャンク境界を強制的に区切る。被写体と背景の干渉を避けたい時に。</li>
    <li>ComfyUI では <strong>標準ノードは解釈しない</strong>。<code>smZ Nodes</code> など A1111 互換ノードが必要。</li>
  </ul>
</div>

<!-- ========== ComfyUI 固有 ========== -->
<div class="pc-card" style="margin-top:1rem;">
  <h3>🧩 ComfyUI 固有の小技</h3>

  <div class="pc-label">Embedding（Textual Inversion）</div>
  <div class="pc-prompt" data-copy>embedding:easyNegative, (embedding:badhandv4:1.1)</div>
  <ul class="ig-ref-list">
    <li>A1111 は <code>easyNegative</code> のまま通るが、ComfyUI は <code>embedding:</code> プレフィックス必須。</li>
    <li>拡張子（<code>.pt</code> / <code>.safetensors</code>）は省略可。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">LoRA 記法（&lt;lora:name:strength&gt;）</div>
  <div class="pc-prompt" data-copy>&lt;lora:iniwa_vrc_v3:0.8&gt;</div>
  <ul class="ig-ref-list">
    <li>本リポでは <strong>プロンプト側の <code>&lt;lora:...&gt;</code> は使わず、Generate ページの LoRA 選択 UI 経由で適用</strong>する方針。</li>
    <li>ComfyUI 標準では <code>&lt;lora:...&gt;</code> は<strong>テキストとして扱われる</strong>（Load LoRA ノードが必要）。A1111 互換拡張を使う場合のみ有効。</li>
  </ul>

  <div class="pc-label" style="margin-top:0.8rem;">コメント</div>
  <div class="pc-prompt" data-copy>1girl, # 被写体
solo, masterpiece
</div>
  <ul class="ig-ref-list">
    <li>本リポの Wildcard 辞書は <code>#</code> 行をコメント扱い。</li>
    <li>ただし ComfyUI に渡るプロンプト側は <code>#</code> を<strong>ただの文字として通す</strong>ので注意。</li>
  </ul>
</div>

<!-- ========== レイヤ順のまとめ ========== -->
<div class="pc-card" style="margin-top:1rem;">
  <h3>🧭 処理レイヤの順序</h3>
  <ol class="ig-ref-list" style="padding-left:1.2rem;">
    <li>ユーザが入力した生テンプレート（Generate / Prompts / Discord から）</li>
    <li>section 合成（<code>section_composer</code> が quality → character → scene → composition → style → user の順に結合）</li>
    <li><strong>Wildcard 展開</strong>（サーバ側で 1 回だけ。<code>{...}</code> / <code>__...__</code> がここで消える）</li>
    <li>ComfyUI ワークフローに注入 → <strong>重み・スケジューリング・embedding</strong> がここで解釈される</li>
    <li>CLIP Text Encode → サンプラへ</li>
  </ol>
  <div class="pc-note">
    つまり「展開後の文字列」が ComfyUI に渡るので、Wildcard 辞書の中身は
    ComfyUI 側の記法（重み括弧・<code>embedding:</code>・スケジューリング）を
    そのまま書いてよい。逆に Wildcard 側の <code>__name__</code> を
    ComfyUI に直接投げても何も起きない（ただの文字列）。
  </div>
</div>
`;
}

// ============================================================
// Mount: クリックでクリップボードにコピー
// ============================================================
export async function mount() {
  document.querySelectorAll('[data-copy]').forEach((el) => {
    el.style.cursor = 'pointer';
    el.title = 'クリックでコピー';
    el.addEventListener('click', async () => {
      const text = el.textContent || '';
      try {
        await navigator.clipboard.writeText(text);
        const prev = el.style.outline;
        el.style.outline = '2px solid var(--accent, #4a9eff)';
        setTimeout(() => { el.style.outline = prev; }, 400);
      } catch {
        /* silent */
      }
    });
  });
}
