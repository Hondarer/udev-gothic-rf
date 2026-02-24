# RuntimeError in add_nerd_font_glyphs()

## 概要

`make.ps1` を実行すると、Nerd Fonts バリアント (NF, NFLG 等) のビルド中に以下の RuntimeError が発生し、当該バリアントの出力が得られない。

```text
Traceback (most recent call last):
  File "fontforge_script.py", line 784, in <module>
    main()
  File "fontforge_script.py", line 86, in main
    generate_font(...)
  File "fontforge_script.py", line 201, in generate_font
    add_nerd_font_glyphs(jp_font, eng_font)
  File "fontforge_script.py", line 711, in add_nerd_font_glyphs
    if nerd_glyph.unicode != -1:
       ^^^^^^^^^^^^^^^^^^
RuntimeError: Glyph object is not valid, the font may have been closed
```

## 原因

`add_nerd_font_glyphs()` は、`generate_font()` から Regular / Bold / Italic / BoldItalic の 4 回呼ばれる。関数はグローバル変数 `nerd_font` を使ったキャッシュ設計になっており、初回のみ `SymbolsNerdFont-Regular.ttf` を開く。

問題は、`jp_font.mergeFonts(nerd_font)` (line 727) の実行後に FontForge が `nerd_font` 内のグリフオブジェクトを無効化する点にある。その後の呼び出しでは `if nerd_font is None:` が `False` となってキャッシュを再利用するが、直後の `for nerd_glyph in nerd_font.glyphs():` (line 710) で無効化されたオブジェクトにアクセスし RuntimeError が発生する。

```python
# fontforge_script.py
nerd_font = None  # line 60: グローバルキャッシュ

def add_nerd_font_glyphs(jp_font, eng_font):
    global nerd_font
    if nerd_font is None:
        nerd_font = fontforge.open(...)  # 初回のみ開く
        # ... グリフ名・幅の調整 ...

    # ここは毎回実行される
    for nerd_glyph in nerd_font.glyphs():  # 2 回目以降は無効なオブジェクト
        if nerd_glyph.unicode != -1:       # <-- RuntimeError 発生
            ...
    jp_font.mergeFonts(nerd_font)  # ここでグリフが無効化される
```

`add_box_drawing_block_elements()` の `hack_font` も同じキャッシュパターンを使うが、`if hack_font is None:` 外でグリフを再イテレートしないため同様の問題は起きていない。

## 再現条件

`--nerd-font` オプションを含むバリアントのビルド時に必ず発生する。make.ps1 の並列実行が原因ではなく、1 プロセス内で `generate_font()` が複数回呼ばれることで起きる。

対象バリアント例: `NF`, `35NF`, `NFLG`, `35NFLG` およびこれらを含む組み合わせ。

## 修正方針

グローバルキャッシュを廃止し、`add_nerd_font_glyphs()` の中で毎回フォントを開いて処理後に閉じる構造に変更する。

変更点:

- line 60 の `nerd_font = None` 宣言を削除する
- 関数内の `global nerd_font` 宣言と `if nerd_font is None:` 条件分岐を削除する
- 関数の末尾 (`jp_font.mergeFonts(nerd_font)` の直後) に `nerd_font.close()` を追加する

変更後の構造 (概略):

```python
def add_nerd_font_glyphs(jp_font, eng_font):
    nerd_font = fontforge.open(f"{SOURCE_FONTS_DIR}/SymbolsNerdFont-Regular.ttf")
    nerd_font.em = EM_ASCENT + EM_DESCENT
    glyph_names = set()
    for nerd_glyph in nerd_font.glyphs():
        # グリフ名・幅の調整 (既存コードのまま)
        ...
    for nerd_glyph in nerd_font.glyphs():
        if nerd_glyph.unicode != -1:
            ...
    jp_font.mergeFonts(nerd_font)
    nerd_font.close()  # 追加
    jp_font.selection.none()
    eng_font.selection.none()
```

ビルドごとにファイルを開き直すため、NF バリアントのビルド時間は若干増加するが、4 回で 1 回分の増加であり許容範囲と考えられる。

## 検証方法

修正後、`pwsh -File make.ps1` を完全実行し、NF / NFLG / 35NF / 35NFLG を含む全バリアントが RuntimeError なしで完了することを確認する。

