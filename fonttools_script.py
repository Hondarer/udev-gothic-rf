#!/bin/env python3

import configparser
import copy
import gc
import glob
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from fontTools import merge, ttLib, ttx
from fontTools.ttLib.tables.S_V_G_ import SVGDocument
from ttfautohint import options, ttfautohint

# iniファイルを読み込む
settings = configparser.ConfigParser()
settings.read("build.ini", encoding="utf-8")

FONT_NAME = settings.get("DEFAULT", "FONT_NAME")
FONTFORGE_PREFIX = settings.get("DEFAULT", "FONTFORGE_PREFIX")
FONTTOOLS_PREFIX = settings.get("DEFAULT", "FONTTOOLS_PREFIX")
SOURCE_FONTS_DIR = settings.get("DEFAULT", "SOURCE_FONTS_DIR")
BUILD_FONTS_DIR = settings.get("DEFAULT", "BUILD_FONTS_DIR")
EMOJI_FONT = settings.get("DEFAULT", "EMOJI_FONT")
HALF_WIDTH_12 = int(settings.get("DEFAULT", "HALF_WIDTH_12"))
FULL_WIDTH_35 = int(settings.get("DEFAULT", "FULL_WIDTH_35"))
WIDTH_35_STR = settings.get("DEFAULT", "WIDTH_35_STR")
EMOJI_STR = settings.get("DEFAULT", "EMOJI_STR")
EMOJI_SOURCE_WIDTH = 1275
EMOJI_SOURCE_BOTTOM = -212
EMOJI_OVERRIDE_PATH = "doc/emoji-override.txt"


def main():
    # 第一引数を取得
    # 特定のバリエーションのみを処理するための指定
    specific_variant = sys.argv[1] if len(sys.argv) > 1 else None

    edit_fonts(specific_variant)


def edit_fonts(specific_variant: str):
    """フォントを編集する"""

    if specific_variant is None:
        specific_variant = ""

    # ファイルをパターンで指定
    file_pattern = f"{FONTFORGE_PREFIX}{FONT_NAME}{specific_variant}*-eng.ttf"
    filenames = glob.glob(f"{BUILD_FONTS_DIR}/{file_pattern}")
    # ファイルが見つからない場合はエラー
    if len(filenames) == 0:
        print(f"Error: {file_pattern} not found")
        return
    paths = [Path(f) for f in filenames]
    for path in paths:
        print(f"edit {str(path)}")
        style = path.stem.split("-")[1]
        variant = path.stem.split("-")[0].replace(f"{FONTFORGE_PREFIX}{FONT_NAME}", "")
        add_hinting(str(path), str(path).replace(".ttf", "-hinted.ttf"))
        merged_font_name = merge_fonts(style, variant)
        fix_font_tables(style, variant, merged_font_name)

    # 一時ファイルを削除
    # スタイル部分以降はワイルドカードで指定
    for filename in glob.glob(
        f"{BUILD_FONTS_DIR}/{FONTTOOLS_PREFIX}{FONT_NAME}{specific_variant}*"
    ):
        safe_remove(filename)
    for filename in glob.glob(
        f"{BUILD_FONTS_DIR}/{FONTFORGE_PREFIX}{FONT_NAME}{specific_variant}*"
    ):
        safe_remove(filename)


def add_hinting(input_font_path, output_font_path):
    """フォントにヒンティングを付ける"""
    args = [
        "-l",
        "6",
        "-r",
        "45",
        "-D",
        "latn",
        "-f",
        "none",
        "-S",
        "-W",
        "-X",
        "14-",
        "-x",
        "0",
        "-I",
        input_font_path,
        output_font_path,
    ]
    options_ = options.parse_args(args)
    print("exec hinting", options_)
    ttfautohint(**options_)


def merge_fonts(style, variant):
    """フォントを結合する"""
    eng_font_path = f"{BUILD_FONTS_DIR}/{FONTFORGE_PREFIX}{FONT_NAME}{variant}-{style}-eng-hinted.ttf"
    jp_font_path = (
        f"{BUILD_FONTS_DIR}/{FONTFORGE_PREFIX}{FONT_NAME}{variant}-{style}-jp.ttf"
    )
    # vhea, vmtxテーブルを削除
    jp_font_object = ttLib.TTFont(jp_font_path)
    if "vhea" in jp_font_object:
        del jp_font_object["vhea"]
    if "vmtx" in jp_font_object:
        del jp_font_object["vmtx"]
    jp_font_object.save(jp_font_path)
    # フォントを結合
    merger = merge.Merger()
    merged_font = merger.merge([eng_font_path, jp_font_path])
    merged_font_path = (
        f"{BUILD_FONTS_DIR}/{FONTTOOLS_PREFIX}{FONT_NAME}{variant}-{style}_merged.ttf"
    )
    merged_font.save(merged_font_path)

    if EMOJI_STR in variant:
        return Path(
            merge_emoji_font(merged_font_path, flag_35=WIDTH_35_STR in variant)
        ).name

    return Path(merged_font_path).name


def merge_emoji_font(target_font_path: str, flag_35: bool):
    """Noto Color Emojiをマージする。

    既存の本文グリフを壊さないよう、targetに存在するcmapはemoji側から外す。
    ただし絵文字優先リストに含まれる文字はemoji側のcmapとグリフを使う。
    """
    emoji_override_codepoints = load_emoji_override_codepoints()
    target_font = ttLib.TTFont(target_font_path, recalcBBoxes=False)
    emoji_font = ttLib.TTFont(
        f"{SOURCE_FONTS_DIR}/{EMOJI_FONT}", recalcBBoxes=False
    )
    emoji_transform = get_emoji_transform(target_font, flag_35)
    emoji_font = normalize_emoji_font(
        emoji_font, target_font, emoji_transform, emoji_override_codepoints
    )
    target_glyph_names = set(target_font.getGlyphOrder())
    rename_emoji_glyph_conflicts(emoji_font, target_glyph_names)
    emoji_override_cmap = collect_emoji_override_cmap(
        emoji_font, emoji_override_codepoints
    )

    emoji_font_path = target_font_path.replace("_merged.ttf", "_emoji.ttf")
    target_merge_font_path = target_font_path.replace(
        "_merged.ttf", "_base_for_emoji.ttf"
    )
    shutil.copyfile(target_font_path, target_merge_font_path)
    emoji_font.save(emoji_font_path)

    merger = merge.Merger()
    merged_font = merger.merge([target_merge_font_path, emoji_font_path])
    merged_font.recalcBBoxes = False
    ensure_source_glyphs(merged_font, target_font)
    ensure_emoji_glyphs(merged_font, emoji_font, target_glyph_names)
    max_emoji_glyphs = 65535 - len(target_glyph_names | {".notdef"})
    emoji_base_glyph_names, emoji_required_glyph_names = collect_emoji_glyph_names(
        emoji_font, max_emoji_glyphs
    )
    emoji_required_glyph_names |= collect_layout_glyph_names(merged_font)
    emoji_required_glyph_names |= collect_composite_component_glyph_names(
        merged_font, emoji_required_glyph_names
    )
    prune_merged_glyphs(
        merged_font, target_glyph_names | emoji_required_glyph_names
    )
    apply_emoji_override_cmap(merged_font, emoji_override_cmap)
    transplant_emoji_color_tables(
        merged_font,
        emoji_font,
        target_glyph_names,
        emoji_transform,
        emoji_base_glyph_names,
    )
    merged_font["post"].formatType = 3.0

    if "vhea" in merged_font:
        del merged_font["vhea"]
    if "vmtx" in merged_font:
        del merged_font["vmtx"]

    target_font.close()
    emoji_font.close()
    output_font_path = target_font_path.replace("_merged.ttf", "_emoji_merged.ttf")
    merged_font.save(output_font_path)
    merged_font.close()
    safe_remove(emoji_font_path)
    safe_remove(target_merge_font_path)
    return output_font_path


def load_emoji_override_codepoints():
    override_path = Path(EMOJI_OVERRIDE_PATH)
    if not override_path.exists():
        return set()

    codepoints = set()
    with override_path.open(encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if len(line) == 0 or line.startswith("codepoint"):
                continue
            codepoint_text = line.split("\t", 1)[0]
            if not re.fullmatch(r"U\+[0-9A-Fa-f]{4,6}", codepoint_text):
                continue
            codepoints.add(int(codepoint_text[2:], 16))
    return codepoints


def collect_emoji_override_cmap(emoji_font: ttLib.TTFont, override_codepoints: set):
    if len(override_codepoints) == 0 or "cmap" not in emoji_font:
        return {}

    override_cmap = {}
    for cmap_table in emoji_font["cmap"].tables:
        if not hasattr(cmap_table, "cmap"):
            continue
        for codepoint in override_codepoints:
            glyph_name = cmap_table.cmap.get(codepoint)
            if glyph_name is not None:
                override_cmap[codepoint] = glyph_name
    return override_cmap


def apply_emoji_override_cmap(merged_font: ttLib.TTFont, override_cmap: dict):
    if len(override_cmap) == 0 or "cmap" not in merged_font:
        return

    glyph_names = set(merged_font.getGlyphOrder())
    override_cmap = {
        codepoint: glyph_name
        for codepoint, glyph_name in override_cmap.items()
        if glyph_name in glyph_names
    }
    for cmap_table in merged_font["cmap"].tables:
        if not hasattr(cmap_table, "cmap"):
            continue
        for codepoint, glyph_name in override_cmap.items():
            cmap_table.cmap[codepoint] = glyph_name


def safe_remove(path: str):
    for _ in range(5):
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            gc.collect()
            time.sleep(0.2)


def ensure_emoji_glyphs(
    merged_font: ttLib.TTFont, emoji_font: ttLib.TTFont, existing_glyph_names: set
):
    """COLR/SVGが参照する未エンコードの絵文字補助グリフも追加する。"""
    merged_glyph_order = merged_font.getGlyphOrder()
    merged_glyph_names = set(merged_glyph_order)
    glyf = merged_font["glyf"]
    hmtx = merged_font["hmtx"]
    emoji_glyf = emoji_font["glyf"]
    emoji_hmtx = emoji_font["hmtx"]

    for glyph_name in emoji_font.getGlyphOrder():
        if glyph_name == ".notdef":
            continue
        if glyph_name in existing_glyph_names or glyph_name in merged_glyph_names:
            continue
        glyf.glyphs[glyph_name] = copy.deepcopy(emoji_glyf.glyphs[glyph_name])
        hmtx.metrics[glyph_name] = emoji_hmtx.metrics[glyph_name]
        merged_glyph_order.append(glyph_name)
        merged_glyph_names.add(glyph_name)

    merged_font.setGlyphOrder(merged_glyph_order)
    merged_font["glyf"].glyphOrder = merged_glyph_order
    merged_font["maxp"].numGlyphs = len(merged_glyph_order)


def rename_emoji_glyph_conflicts(emoji_font: ttLib.TTFont, target_glyph_names: set):
    """COLR layer glyphsが合成先glyph名を参照しないよう衝突名を退避する。"""
    rename_map = build_emoji_glyph_rename_map(emoji_font, target_glyph_names)
    if len(rename_map) == 0:
        return

    rename_glyph_order(emoji_font, rename_map)
    rename_glyf_table(emoji_font, rename_map)
    rename_hmtx_table(emoji_font, rename_map)
    rename_cmap_table(emoji_font, rename_map)
    if "COLR" in emoji_font:
        rename_colr_glyphs(emoji_font["COLR"], rename_map)
    if "GSUB" in emoji_font:
        rename_ot_glyphs(emoji_font["GSUB"].table, rename_map)


def build_emoji_glyph_rename_map(emoji_font: ttLib.TTFont, target_glyph_names: set):
    glyph_order = emoji_font.getGlyphOrder()
    reserved_names = set(glyph_order) | target_glyph_names
    rename_map = {}
    for glyph_name in glyph_order:
        if glyph_name == ".notdef" or glyph_name not in target_glyph_names:
            continue
        new_glyph_name = f"emoji_{glyph_name}"
        suffix = 1
        while new_glyph_name in reserved_names:
            new_glyph_name = f"emoji_{glyph_name}_{suffix}"
            suffix += 1
        rename_map[glyph_name] = new_glyph_name
        reserved_names.add(new_glyph_name)
    return rename_map


def rename_glyph_order(font: ttLib.TTFont, rename_map: dict):
    font.setGlyphOrder([rename_map.get(name, name) for name in font.getGlyphOrder()])


def rename_glyf_table(font: ttLib.TTFont, rename_map: dict):
    if "glyf" not in font:
        return
    glyf = font["glyf"]
    glyf.glyphs = {
        rename_map.get(glyph_name, glyph_name): glyph
        for glyph_name, glyph in glyf.glyphs.items()
    }
    if hasattr(glyf, "glyphOrder"):
        glyf.glyphOrder = [
            rename_map.get(glyph_name, glyph_name) for glyph_name in glyf.glyphOrder
        ]
    for glyph in glyf.glyphs.values():
        if not glyph.isComposite():
            continue
        for component in glyph.components:
            component.glyphName = rename_map.get(component.glyphName, component.glyphName)


def rename_hmtx_table(font: ttLib.TTFont, rename_map: dict):
    if "hmtx" not in font:
        return
    font["hmtx"].metrics = {
        rename_map.get(glyph_name, glyph_name): metrics
        for glyph_name, metrics in font["hmtx"].metrics.items()
    }


def rename_cmap_table(font: ttLib.TTFont, rename_map: dict):
    if "cmap" not in font:
        return
    for cmap_table in font["cmap"].tables:
        if hasattr(cmap_table, "cmap"):
            cmap_table.cmap = {
                codepoint: rename_map.get(glyph_name, glyph_name)
                for codepoint, glyph_name in cmap_table.cmap.items()
            }


def rename_colr_glyphs(colr_table, rename_map: dict):
    if colr_table.version == 0:
        colr_table.ColorLayers = {
            rename_map.get(glyph_name, glyph_name): rename_ot_glyphs(layers, rename_map)
            for glyph_name, layers in colr_table.ColorLayers.items()
        }
        return
    rename_ot_glyphs(colr_table.table, rename_map)


def rename_ot_glyphs(value, rename_map: dict, visited=None):
    if visited is None:
        visited = set()
    if isinstance(value, str):
        return rename_map.get(value, value)
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = rename_ot_glyphs(item, rename_map, visited)
        return value
    if isinstance(value, tuple):
        return tuple(rename_ot_glyphs(item, rename_map, visited) for item in value)
    if isinstance(value, dict):
        renamed_items = [
            (
                rename_ot_glyphs(key, rename_map, visited),
                rename_ot_glyphs(item, rename_map, visited),
            )
            for key, item in value.items()
        ]
        value.clear()
        value.update(renamed_items)
        return value
    if not hasattr(value, "__dict__"):
        return value

    value_id = id(value)
    if value_id in visited:
        return value
    visited.add(value_id)
    for attr_name, attr_value in list(value.__dict__.items()):
        if attr_name.startswith("_"):
            continue
        setattr(value, attr_name, rename_ot_glyphs(attr_value, rename_map, visited))
    return value


def collect_emoji_glyph_names(emoji_font: ttLib.TTFont, max_glyph_count=None):
    glyph_names = set(emoji_font.getGlyphOrder())
    cmap_glyph_names = collect_cmap_glyph_names(emoji_font)
    base_glyph_names = set(cmap_glyph_names)
    gsub_glyph_names = set()
    gsub_output_glyph_names = set()
    if "GSUB" in emoji_font:
        gsub_glyph_names = collect_ot_glyph_names(emoji_font["GSUB"].table, glyph_names)
        gsub_output_glyph_names = collect_gsub_output_glyph_names(
            emoji_font["GSUB"].table
        )

    required_glyph_names = set(base_glyph_names) | gsub_glyph_names
    if "COLR" in emoji_font:
        required_glyph_names |= collect_colr_glyph_names(
            emoji_font["COLR"], base_glyph_names, glyph_names
        )
        if max_glyph_count is not None:
            for glyph_name in emoji_font.getGlyphOrder():
                if glyph_name not in gsub_output_glyph_names:
                    continue
                candidate_base_glyph_names = base_glyph_names | {glyph_name}
                candidate_required_glyph_names = (
                    required_glyph_names
                    | collect_colr_glyph_names(
                        emoji_font["COLR"], {glyph_name}, glyph_names
                    )
                )
                candidate_required_glyph_names |= collect_composite_component_glyph_names(
                    emoji_font, candidate_required_glyph_names
                )
                if len(candidate_required_glyph_names) > max_glyph_count:
                    continue
                base_glyph_names = candidate_base_glyph_names
                required_glyph_names = candidate_required_glyph_names
    if "SVG " in emoji_font:
        required_glyph_names |= collect_svg_glyph_names(
            emoji_font["SVG "], emoji_font.getGlyphOrder(), base_glyph_names
        )
    required_glyph_names |= collect_composite_component_glyph_names(
        emoji_font, required_glyph_names
    )
    required_glyph_names.discard(".notdef")
    return base_glyph_names, required_glyph_names


def collect_cmap_glyph_names(font: ttLib.TTFont):
    glyph_names = set()
    if "cmap" not in font:
        return glyph_names
    for cmap_table in font["cmap"].tables:
        if hasattr(cmap_table, "cmap"):
            glyph_names.update(cmap_table.cmap.values())
    return glyph_names


def collect_gsub_output_glyph_names(gsub_table):
    glyph_names = set()
    lookup_list = getattr(gsub_table, "LookupList", None)
    if lookup_list is None:
        return glyph_names
    for lookup in lookup_list.Lookup:
        for subtable in lookup.SubTable:
            glyph_names |= collect_gsub_subtable_output_glyph_names(subtable)
    return glyph_names


def collect_gsub_subtable_output_glyph_names(subtable):
    glyph_names = set()
    mapping = getattr(subtable, "mapping", None)
    if isinstance(mapping, dict):
        glyph_names.update(
            glyph_name for glyph_name in mapping.values() if isinstance(glyph_name, str)
        )

    alternates = getattr(subtable, "alternates", None)
    if isinstance(alternates, dict):
        for alternate_glyphs in alternates.values():
            glyph_names.update(alternate_glyphs)

    ligatures = getattr(subtable, "ligatures", None)
    if isinstance(ligatures, dict):
        for ligature_sets in ligatures.values():
            for ligature in ligature_sets:
                glyph_names.add(ligature.LigGlyph)

    substitute = getattr(subtable, "Substitute", None)
    if isinstance(substitute, list):
        glyph_names.update(substitute)
    elif isinstance(substitute, str):
        glyph_names.add(substitute)
    return glyph_names


def collect_colr_glyph_names(colr_table, base_glyph_names: set, glyph_names: set):
    if colr_table.version == 0:
        required_glyph_names = set()
        for base_glyph, layers in colr_table.ColorLayers.items():
            if base_glyph not in base_glyph_names:
                continue
            required_glyph_names.add(base_glyph)
            required_glyph_names |= collect_ot_glyph_names(layers, glyph_names)
        return required_glyph_names

    required_glyph_names = set()
    layer_paints = []
    layer_list = getattr(colr_table.table, "LayerList", None)
    if layer_list is not None:
        layer_paints = layer_list.Paint
    base_glyph_list = getattr(colr_table.table, "BaseGlyphList", None)
    if base_glyph_list is None:
        return required_glyph_names
    for record in base_glyph_list.BaseGlyphPaintRecord:
        if record.BaseGlyph not in base_glyph_names:
            continue
        required_glyph_names.add(record.BaseGlyph)
        required_glyph_names |= collect_colr_paint_glyph_names(
            record.Paint, glyph_names, layer_paints
        )
    return required_glyph_names


def collect_colr_paint_glyph_names(
    paint, glyph_names: set, layer_paints: list, visited=None
):
    if visited is None:
        visited = set()
    if paint is None:
        return set()
    paint_id = id(paint)
    if paint_id in visited:
        return set()
    visited.add(paint_id)

    result = collect_ot_glyph_names(paint, glyph_names)
    if getattr(paint, "Format", None) == 1:
        first_layer_index = paint.FirstLayerIndex
        for layer_index in range(first_layer_index, first_layer_index + paint.NumLayers):
            if layer_index >= len(layer_paints):
                continue
            result |= collect_colr_paint_glyph_names(
                layer_paints[layer_index], glyph_names, layer_paints, visited
            )
    nested_paints = [
        value
        for value in getattr(paint, "__dict__", {}).values()
        if value.__class__.__name__ == "Paint"
    ]
    for nested_paint in nested_paints:
        result |= collect_colr_paint_glyph_names(
            nested_paint, glyph_names, layer_paints, visited
        )
    return result


def collect_svg_glyph_names(svg_table, glyph_order: list, base_glyph_names: set):
    required_glyph_names = set()
    for doc in svg_table.docList:
        for glyph_id in range(doc.startGlyphID, doc.endGlyphID + 1):
            if glyph_id >= len(glyph_order):
                continue
            glyph_name = glyph_order[glyph_id]
            if glyph_name in base_glyph_names:
                required_glyph_names.add(glyph_name)
    return required_glyph_names


def collect_composite_component_glyph_names(
    font: ttLib.TTFont, initial_glyph_names: set
):
    if "glyf" not in font:
        return set()
    glyf = font["glyf"]
    collected_glyph_names = set()
    pending_glyph_names = list(initial_glyph_names)
    while len(pending_glyph_names) > 0:
        glyph_name = pending_glyph_names.pop()
        if glyph_name not in glyf.glyphs:
            continue
        glyph = glyf[glyph_name]
        if not glyph.isComposite():
            continue
        for component in glyph.components:
            component_glyph_name = component.glyphName
            if component_glyph_name in collected_glyph_names:
                continue
            collected_glyph_names.add(component_glyph_name)
            pending_glyph_names.append(component_glyph_name)
    return collected_glyph_names


def collect_layout_glyph_names(font: ttLib.TTFont):
    glyph_names = set(font.getGlyphOrder())
    collected_glyph_names = set()
    for table_name in ["GSUB", "GPOS"]:
        if table_name in font:
            collected_glyph_names |= collect_ot_glyph_names(
                font[table_name].table, glyph_names
            )
    return collected_glyph_names


def collect_ot_glyph_names(value, glyph_names: set, visited=None):
    if visited is None:
        visited = set()
    if isinstance(value, str):
        return {value} if value in glyph_names else set()
    if isinstance(value, (list, tuple)):
        result = set()
        for item in value:
            result |= collect_ot_glyph_names(item, glyph_names, visited)
        return result
    if isinstance(value, dict):
        result = set()
        for key, item in value.items():
            result |= collect_ot_glyph_names(key, glyph_names, visited)
            result |= collect_ot_glyph_names(item, glyph_names, visited)
        return result
    if not hasattr(value, "__dict__"):
        return set()

    value_id = id(value)
    if value_id in visited:
        return set()
    visited.add(value_id)
    result = set()
    for attr_name, attr_value in value.__dict__.items():
        if attr_name.startswith("_"):
            continue
        result |= collect_ot_glyph_names(attr_value, glyph_names, visited)
    return result


def prune_merged_glyphs(merged_font: ttLib.TTFont, keep_glyph_names: set):
    keep_glyph_names = set(keep_glyph_names)
    keep_glyph_names.add(".notdef")
    glyph_order = [
        glyph_name
        for glyph_name in merged_font.getGlyphOrder()
        if glyph_name in keep_glyph_names
    ]
    glyph_order_names = set(glyph_order)

    if "glyf" in merged_font:
        merged_font["glyf"].glyphs = {
            glyph_name: glyph
            for glyph_name, glyph in merged_font["glyf"].glyphs.items()
            if glyph_name in glyph_order_names
        }
        merged_font["glyf"].glyphOrder = glyph_order
    if "hmtx" in merged_font:
        merged_font["hmtx"].metrics = {
            glyph_name: metrics
            for glyph_name, metrics in merged_font["hmtx"].metrics.items()
            if glyph_name in glyph_order_names
        }
    merged_font.setGlyphOrder(glyph_order)
    merged_font["maxp"].numGlyphs = len(glyph_order)


def ensure_source_glyphs(merged_font: ttLib.TTFont, source_font: ttLib.TTFont):
    """mergeで落ちた未エンコードの合成元グリフを戻す。"""
    merged_glyph_order = merged_font.getGlyphOrder()
    merged_glyph_names = set(merged_glyph_order)
    glyf = merged_font["glyf"]
    hmtx = merged_font["hmtx"]
    source_glyf = source_font["glyf"]
    source_hmtx = source_font["hmtx"]

    for glyph_name in source_font.getGlyphOrder():
        if glyph_name in merged_glyph_names:
            continue
        glyf.glyphs[glyph_name] = copy.deepcopy(source_glyf.glyphs[glyph_name])
        hmtx.metrics[glyph_name] = source_hmtx.metrics[glyph_name]
        merged_glyph_order.append(glyph_name)
        merged_glyph_names.add(glyph_name)

    merged_font.setGlyphOrder(merged_glyph_order)
    merged_font["glyf"].glyphOrder = merged_glyph_order
    merged_font["maxp"].numGlyphs = len(merged_glyph_order)


def get_emoji_transform(target_font: ttLib.TTFont, flag_35: bool):
    target_cmap = target_font.getBestCmap()
    target_width = FULL_WIDTH_35 if flag_35 else target_font["hmtx"].metrics[
        target_cmap[0x3000]
    ][0]
    scale = target_width / EMOJI_SOURCE_WIDTH
    target_bottom = get_fullwidth_bottom(target_font)
    translate_y = target_bottom - EMOJI_SOURCE_BOTTOM * scale
    return {
        "scale_x": scale,
        "scale_y": scale,
        "translate_x": 0,
        "translate_y": translate_y,
        "target_width": target_width,
    }


def get_fullwidth_bottom(font: ttLib.TTFont):
    cmap = font.getBestCmap()
    for codepoint in [0x6F22, 0x5B57]:
        glyph_name = cmap.get(codepoint)
        if glyph_name is None:
            continue
        glyph = font["glyf"][glyph_name]
        if hasattr(glyph, "yMin"):
            return glyph.yMin
    bottoms = []
    for codepoint, glyph_name in cmap.items():
        if font["hmtx"].metrics[glyph_name][0] != font["hmtx"].metrics[cmap[0x3000]][0]:
            continue
        glyph = font["glyf"][glyph_name]
        if hasattr(glyph, "yMin"):
            bottoms.append(glyph.yMin)
    if len(bottoms) == 0:
        return 0
    return sorted(bottoms)[len(bottoms) // 2]


def normalize_emoji_font(
    emoji_font: ttLib.TTFont,
    target_font: ttLib.TTFont,
    emoji_transform: dict,
    emoji_override_codepoints: set = None,
):
    """絵文字側を合成先の全角幅に合わせる。"""
    target_cmap = target_font.getBestCmap()
    target_width = emoji_transform["target_width"]
    target_upm = target_font["head"].unitsPerEm
    if emoji_override_codepoints is None:
        emoji_override_codepoints = set()

    emoji_font["head"].unitsPerEm = target_upm
    transform_glyf_table(emoji_font, emoji_transform)
    for glyph_name, (advance_width, left_side_bearing) in list(
        emoji_font["hmtx"].metrics.items()
    ):
        if advance_width == 0:
            continue
        scaled_lsb = int(round(left_side_bearing * emoji_transform["scale_x"]))
        emoji_font["hmtx"].metrics[glyph_name] = (target_width, scaled_lsb)

    emoji_font["hhea"].advanceWidthMax = target_width
    if "OS/2" in emoji_font:
        emoji_font["OS/2"].xAvgCharWidth = target_width
    if "vhea" in emoji_font:
        del emoji_font["vhea"]
    if "vmtx" in emoji_font:
        del emoji_font["vmtx"]

    for cmap_table in emoji_font["cmap"].tables:
        if hasattr(cmap_table, "cmap"):
            cmap_table.cmap = {
                codepoint: glyph_name
                for codepoint, glyph_name in cmap_table.cmap.items()
                if codepoint not in target_cmap
                or codepoint in emoji_override_codepoints
            }

    return emoji_font


def transform_glyf_table(font: ttLib.TTFont, transform: dict):
    glyf = font["glyf"]
    for glyph_name in font.getGlyphOrder():
        glyph = glyf[glyph_name]
        if glyph.isComposite():
            continue
        if glyph.numberOfContours <= 0:
            continue
        coordinates, _, _ = glyph.getCoordinates(glyf)
        for index, (x, y) in enumerate(coordinates):
            coordinates[index] = (
                int(round(x * transform["scale_x"] + transform["translate_x"])),
                int(round(y * transform["scale_y"] + transform["translate_y"])),
            )
        glyph.coordinates = coordinates
        glyph.recalcBounds(glyf)


def transplant_emoji_color_tables(
    merged_font: ttLib.TTFont,
    emoji_font: ttLib.TTFont,
    target_glyph_names: set,
    emoji_transform: dict,
    emoji_base_glyph_names: set = None,
):
    """mergeで落ちる絵文字カラーテーブルを移植する。"""
    emoji_glyph_order = emoji_font.getGlyphOrder()
    merged_glyph_order = merged_font.getGlyphOrder()
    merged_glyph_ids = {
        glyph_name: glyph_id for glyph_id, glyph_name in enumerate(merged_glyph_order)
    }

    if "CPAL" in emoji_font:
        merged_font["CPAL"] = copy.deepcopy(emoji_font["CPAL"])
    if "COLR" in emoji_font:
        merged_font["COLR"] = copy.deepcopy(emoji_font["COLR"])
        remove_existing_base_glyphs_from_colr(merged_font["COLR"], target_glyph_names)
        if emoji_base_glyph_names is not None:
            keep_base_glyphs_in_colr(merged_font["COLR"], emoji_base_glyph_names)
        compact_colr_layer_list(merged_font["COLR"])
        transform_colr_table(merged_font["COLR"], emoji_transform)
    if "SVG " in emoji_font:
        merged_font["SVG "] = copy.deepcopy(emoji_font["SVG "])
        remap_svg_glyph_ids(
            merged_font["SVG "],
            emoji_glyph_order,
            merged_glyph_ids,
            target_glyph_names,
            emoji_transform,
        )


def transform_colr_table(colr_table, transform: dict):
    if colr_table.version != 1:
        return
    table = colr_table.table
    if getattr(table, "LayerList", None) is not None:
        for paint in table.LayerList.Paint:
            transform_colr_paint(paint, transform)
    if getattr(table, "BaseGlyphList", None) is not None:
        for record in table.BaseGlyphList.BaseGlyphPaintRecord:
            transform_colr_paint(record.Paint, transform)
    if getattr(table, "ClipList", None) is not None:
        transform_colr_cliplist(table.ClipList, transform)


def transform_colr_cliplist(clip_list, transform: dict):
    """ベースグリフごとのクリップ矩形 (ビューポート) を拡大・移動する。
    クリップ矩形は font 空間 (y-up) のため glyf/COLR と同じ変換を適用する。"""
    for clip_box in clip_list.clips.values():
        x0 = clip_box.xMin * transform["scale_x"] + transform["translate_x"]
        x1 = clip_box.xMax * transform["scale_x"] + transform["translate_x"]
        y0 = clip_box.yMin * transform["scale_y"] + transform["translate_y"]
        y1 = clip_box.yMax * transform["scale_y"] + transform["translate_y"]
        clip_box.xMin = clamp_short(int(round(min(x0, x1))))
        clip_box.xMax = clamp_short(int(round(max(x0, x1))))
        clip_box.yMin = clamp_short(int(round(min(y0, y1))))
        clip_box.yMax = clamp_short(int(round(max(y0, y1))))


def transform_colr_paint(paint, transform: dict):
    if paint is None:
        return
    fmt = getattr(paint, "Format", None)
    if fmt in [4, 6]:
        scale_paint_coords(paint, transform, ["x0", "y0", "x1", "y1", "x2", "y2"])
        scale_paint_radii(paint, transform, ["r0", "r1"])
    elif fmt == 12:
        matrix = paint.Transform
        old_dx = matrix.dx
        old_dy = matrix.dy
        matrix.dx = old_dx * transform["scale_x"] + transform["translate_x"] - (
            matrix.xx * transform["translate_x"] + matrix.xy * transform["translate_y"]
        )
        matrix.dy = old_dy * transform["scale_y"] + transform["translate_y"] - (
            matrix.yx * transform["translate_x"] + matrix.yy * transform["translate_y"]
        )
        matrix.dx = clamp_fixed_16_16(matrix.dx)
        matrix.dy = clamp_fixed_16_16(matrix.dy)
    elif fmt == 14:
        paint.dx = clamp_short(int(round(paint.dx * transform["scale_x"])))
        paint.dy = clamp_short(int(round(paint.dy * transform["scale_y"])))
    elif fmt == 18:
        paint.centerX = clamp_short(
            int(round(paint.centerX * transform["scale_x"] + transform["translate_x"]))
        )
        paint.centerY = clamp_short(
            int(round(paint.centerY * transform["scale_y"] + transform["translate_y"]))
        )

    for value in getattr(paint, "__dict__", {}).values():
        if value.__class__.__name__ == "Paint":
            transform_colr_paint(value, transform)


def scale_paint_coords(paint, transform: dict, attributes: list):
    for attr in attributes:
        if not hasattr(paint, attr):
            continue
        scale = transform["scale_x"] if attr.startswith("x") else transform["scale_y"]
        translate = transform["translate_x"] if attr.startswith("x") else transform["translate_y"]
        setattr(
            paint,
            attr,
            clamp_short(int(round(getattr(paint, attr) * scale + translate))),
        )


def scale_paint_radii(paint, transform: dict, attributes: list):
    for attr in attributes:
        if hasattr(paint, attr):
            setattr(
                paint,
                attr,
                clamp_short(int(round(getattr(paint, attr) * transform["scale_x"]))),
            )


def remove_existing_base_glyphs_from_colr(colr_table, existing_glyph_names: set):
    """既存グリフをCOLRの対象から外す。"""
    if colr_table.version == 0:
        colr_table.ColorLayers = {
            glyph_name: layers
            for glyph_name, layers in colr_table.ColorLayers.items()
            if glyph_name not in existing_glyph_names
        }
        return

    base_glyph_list = getattr(colr_table.table, "BaseGlyphList", None)
    if base_glyph_list is None:
        return
    base_glyph_list.BaseGlyphPaintRecord = [
        record
        for record in base_glyph_list.BaseGlyphPaintRecord
        if record.BaseGlyph not in existing_glyph_names
    ]
    base_glyph_list.BaseGlyphCount = len(base_glyph_list.BaseGlyphPaintRecord)


def keep_base_glyphs_in_colr(colr_table, keep_glyph_names: set):
    if colr_table.version == 0:
        colr_table.ColorLayers = {
            glyph_name: layers
            for glyph_name, layers in colr_table.ColorLayers.items()
            if glyph_name in keep_glyph_names
        }
        return

    base_glyph_list = getattr(colr_table.table, "BaseGlyphList", None)
    if base_glyph_list is None:
        return
    base_glyph_list.BaseGlyphPaintRecord = [
        record
        for record in base_glyph_list.BaseGlyphPaintRecord
        if record.BaseGlyph in keep_glyph_names
    ]
    base_glyph_list.BaseGlyphCount = len(base_glyph_list.BaseGlyphPaintRecord)


def compact_colr_layer_list(colr_table):
    if colr_table.version != 1:
        return
    table = colr_table.table
    layer_list = getattr(table, "LayerList", None)
    base_glyph_list = getattr(table, "BaseGlyphList", None)
    if layer_list is None or base_glyph_list is None:
        return

    used_layer_indices = collect_used_colr_layer_indices(
        [record.Paint for record in base_glyph_list.BaseGlyphPaintRecord],
        layer_list.Paint,
    )
    if len(used_layer_indices) == len(layer_list.Paint):
        return

    layer_index_map = {
        old_index: new_index
        for new_index, old_index in enumerate(sorted(used_layer_indices))
    }
    layer_list.Paint = [
        layer_list.Paint[old_index] for old_index in sorted(used_layer_indices)
    ]
    layer_list.LayerCount = len(layer_list.Paint)
    remap_colr_layer_indices(
        [record.Paint for record in base_glyph_list.BaseGlyphPaintRecord]
        + layer_list.Paint,
        layer_index_map,
    )


def collect_used_colr_layer_indices(paints: list, layer_paints: list, visited=None):
    if visited is None:
        visited = set()
    used_layer_indices = set()
    for paint in paints:
        if paint is None:
            continue
        paint_id = id(paint)
        if paint_id in visited:
            continue
        visited.add(paint_id)
        if getattr(paint, "Format", None) == 1:
            first_layer_index = paint.FirstLayerIndex
            layer_indices = range(first_layer_index, first_layer_index + paint.NumLayers)
            for layer_index in layer_indices:
                if layer_index >= len(layer_paints):
                    continue
                used_layer_indices.add(layer_index)
                used_layer_indices |= collect_used_colr_layer_indices(
                    [layer_paints[layer_index]], layer_paints, visited
                )
        nested_paints = [
            value
            for value in getattr(paint, "__dict__", {}).values()
            if value.__class__.__name__ == "Paint"
        ]
        used_layer_indices |= collect_used_colr_layer_indices(
            nested_paints, layer_paints, visited
        )
    return used_layer_indices


def remap_colr_layer_indices(paints: list, layer_index_map: dict, visited=None):
    if visited is None:
        visited = set()
    for paint in paints:
        if paint is None:
            continue
        paint_id = id(paint)
        if paint_id in visited:
            continue
        visited.add(paint_id)
        if getattr(paint, "Format", None) == 1:
            paint.FirstLayerIndex = layer_index_map[paint.FirstLayerIndex]
        nested_paints = [
            value
            for value in getattr(paint, "__dict__", {}).values()
            if value.__class__.__name__ == "Paint"
        ]
        remap_colr_layer_indices(nested_paints, layer_index_map, visited)


def remap_svg_glyph_ids(
    svg_table,
    emoji_glyph_order: list,
    merged_glyph_ids: dict,
    existing_glyph_names: set,
    emoji_transform: dict,
):
    """SVGテーブル内の glyph ID 参照を合成後のIDに合わせる。"""
    remapped_docs = []
    for doc in svg_table.docList:
        new_glyph_ids = []
        replacement_map = {}
        removed_glyph_ids = []
        for old_glyph_id in range(doc.startGlyphID, doc.endGlyphID + 1):
            if old_glyph_id >= len(emoji_glyph_order):
                continue
            glyph_name = emoji_glyph_order[old_glyph_id]
            if glyph_name in existing_glyph_names:
                removed_glyph_ids.append(str(old_glyph_id))
                continue
            new_glyph_id = merged_glyph_ids.get(glyph_name)
            if new_glyph_id is None:
                removed_glyph_ids.append(str(old_glyph_id))
                continue
            replacement_map[str(old_glyph_id)] = str(new_glyph_id)
            new_glyph_ids.append(new_glyph_id)
        if len(new_glyph_ids) > 0:
            data = remove_svg_glyph_groups(doc.data, removed_glyph_ids)
            data = re.sub(
                r"(?<=glyph)(\d+)(?![0-9])",
                lambda match: replacement_map.get(match.group(1), match.group(1)),
                data,
            )
            data = bake_svg_glyph_transforms(data, replacement_map.values(), emoji_transform)
            remapped_docs.append(
                SVGDocument(
                    data,
                    min(new_glyph_ids),
                    max(new_glyph_ids),
                    compressed=True,
                )
            )
    svg_table.docList = remapped_docs


def remove_svg_glyph_groups(svg_data: str, glyph_ids: list):
    for glyph_id in glyph_ids:
        svg_data = re.sub(
            rf'<g id="glyph{glyph_id}"[^>]*>.*?</g>',
            "",
            svg_data,
            flags=re.DOTALL,
        )
    return svg_data


def bake_svg_glyph_transforms(svg_data: str, glyph_ids, transform: dict):
    path_defs = collect_svg_path_defs(svg_data)
    glyph_id_set = set(glyph_ids)

    def replace(match):
        glyph_id = match.group(1)
        attrs = match.group(2)
        body = match.group(3)
        if glyph_id not in glyph_id_set:
            return match.group(0)
        attrs = re.sub(r'\s+transform="[^"]*"', "", attrs)
        body = inline_svg_uses(body, path_defs, transform)
        body = transform_svg_paths(body, transform)
        return f'<g id="glyph{glyph_id}"{attrs}>{body}</g>'

    return re.sub(
        r'<g id="glyph(\d+)"([^>]*)>(.*?)</g>',
        replace,
        svg_data,
        flags=re.DOTALL,
    )


def collect_svg_path_defs(svg_data: str):
    defs = {}
    for match in re.finditer(r"<path\b([^>]*)/?>", svg_data):
        attrs = match.group(1)
        path_id = get_svg_attr(attrs, "id")
        path_d = get_svg_attr(attrs, "d")
        if path_id is None or path_d is None:
            continue
        defs[path_id] = path_d
    return defs


def inline_svg_uses(svg_body: str, path_defs: dict, transform: dict):
    def replace(match):
        attrs = cleanup_svg_attrs(match.group(1))
        href = get_svg_attr(attrs, "xlink:href") or get_svg_attr(attrs, "href")
        if href is None or not href.startswith("#"):
            return match.group(0)
        path_d = path_defs.get(href[1:])
        if path_d is None:
            return match.group(0)
        x = parse_svg_number_attr(attrs, "x")
        y = parse_svg_number_attr(attrs, "y")
        local_transform = parse_svg_matrix_transform(get_svg_attr(attrs, "transform"))
        path_d = transform_svg_path_d(
            path_d,
            transform,
            offset_x=x,
            offset_y=y,
            local_transform=local_transform,
        )
        path_attrs = remove_svg_attrs(
            attrs, ["xlink:href", "href", "x", "y", "transform"]
        )
        return f'<path{path_attrs} d="{path_d}" data-baked="1"/>'

    return re.sub(r"<use\b([^>]*)/?>", replace, svg_body)


def transform_svg_paths(svg_body: str, transform: dict):
    def replace(match):
        attrs = cleanup_svg_attrs(match.group(1))
        path_d = get_svg_attr(attrs, "d")
        if path_d is None:
            return match.group(0)
        if get_svg_attr(attrs, "data-baked") == "1":
            attrs = remove_svg_attrs(attrs, ["data-baked"])
            return f"<path{attrs}/>"
        path_d = transform_svg_path_d(path_d, transform)
        attrs = set_svg_attr(attrs, "d", path_d)
        attrs = re.sub(r'\s+transform="[^"]*"', "", attrs)
        return f"<path{attrs}/>"

    return re.sub(r"<path\b([^>]*)/?>", replace, svg_body)


def get_svg_attr(attrs: str, attr_name: str):
    match = re.search(rf'(?:^|\s){re.escape(attr_name)}="([^"]*)"', attrs)
    return match.group(1) if match else None


def cleanup_svg_attrs(attrs: str):
    return re.sub(r"\s*/\s*$", "", attrs)


def parse_svg_number_attr(attrs: str, attr_name: str):
    value = get_svg_attr(attrs, attr_name)
    return float(value) if value is not None else 0


def remove_svg_attrs(attrs: str, attr_names: list):
    for attr_name in attr_names:
        attrs = re.sub(rf'\s+{re.escape(attr_name)}="[^"]*"', "", attrs)
    return attrs


def set_svg_attr(attrs: str, attr_name: str, attr_value: str):
    if get_svg_attr(attrs, attr_name) is None:
        return f'{attrs} {attr_name}="{attr_value}"'
    return re.sub(
        rf'(\s{re.escape(attr_name)}=")[^"]*(")',
        rf'\g<1>{attr_value}\2',
        attrs,
        count=1,
    )


def parse_svg_matrix_transform(transform_attr: str):
    if transform_attr is None:
        return (1, 0, 0, 1, 0, 0)
    match = re.search(r"matrix\(([^)]*)\)", transform_attr)
    if match is None:
        return (1, 0, 0, 1, 0, 0)
    values = [
        float(value)
        for value in re.findall(
            r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", match.group(1)
        )
    ]
    if len(values) != 6:
        return (1, 0, 0, 1, 0, 0)
    return tuple(values)


def transform_svg_path_d(
    path_d: str,
    transform: dict,
    offset_x=0,
    offset_y=0,
    local_transform=None,
):
    if local_transform is None:
        local_transform = (1, 0, 0, 1, 0, 0)
    tokens = re.findall(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", path_d)
    result = []
    index = 0
    command = None
    first_pair_after_move = False
    while index < len(tokens):
        token = tokens[index]
        if re.match(r"^[A-Za-z]$", token):
            command = token
            result.append(command)
            index += 1
            first_pair_after_move = command in ["M", "m"]
            continue
        if command is None:
            result.append(token)
            index += 1
            continue
        count = svg_path_command_number_count(command)
        if count is None:
            result.append(token)
            index += 1
            continue
        if index + count > len(tokens):
            result.extend(tokens[index:])
            break
        values = [float(value) for value in tokens[index : index + count]]
        transformed = transform_svg_path_values(
            command, values, transform, offset_x, offset_y, local_transform
        )
        result.extend(format_svg_number(value) for value in transformed)
        index += count
        if command == "M" and first_pair_after_move:
            command = "L"
            first_pair_after_move = False
        elif command == "m" and first_pair_after_move:
            command = "l"
            first_pair_after_move = False
    return " ".join(result)


def svg_path_command_number_count(command: str):
    return {
        "M": 2,
        "m": 2,
        "L": 2,
        "l": 2,
        "H": 1,
        "h": 1,
        "V": 1,
        "v": 1,
        "C": 6,
        "c": 6,
        "S": 4,
        "s": 4,
        "Q": 4,
        "q": 4,
        "T": 2,
        "t": 2,
        "A": 7,
        "a": 7,
        "Z": 0,
        "z": 0,
    }.get(command)


def transform_svg_path_values(
    command: str, values: list, transform: dict, offset_x, offset_y, local_transform
):
    is_relative = command.islower()
    command_upper = command.upper()
    transformed = values[:]
    if command_upper in ["M", "L", "T"]:
        transformed = transform_svg_point_values(
            values, transform, is_relative, offset_x, offset_y, local_transform
        )
    elif command_upper in ["C"]:
        transformed = transform_svg_point_values(
            values, transform, is_relative, offset_x, offset_y, local_transform
        )
    elif command_upper in ["S", "Q"]:
        transformed = transform_svg_point_values(
            values, transform, is_relative, offset_x, offset_y, local_transform
        )
    elif command_upper == "H":
        transformed[0] = transform_svg_point(
            values[0], 0, transform, is_relative, offset_x, offset_y, local_transform
        )[0]
    elif command_upper == "V":
        transformed[0] = transform_svg_point(
            0, values[0], transform, is_relative, offset_x, offset_y, local_transform
        )[1]
    elif command_upper == "A":
        transformed[0] = values[0] * local_transform[0] * transform["scale_x"]
        transformed[1] = values[1] * local_transform[3] * transform["scale_y"]
        transformed[5], transformed[6] = transform_svg_point(
            values[5],
            values[6],
            transform,
            is_relative,
            offset_x,
            offset_y,
            local_transform,
        )
    return transformed


def transform_svg_point_values(
    values: list, transform: dict, is_relative: bool, offset_x, offset_y, local_transform
):
    transformed = values[:]
    for index in range(0, len(values), 2):
        transformed[index], transformed[index + 1] = transform_svg_point(
            values[index],
            values[index + 1],
            transform,
            is_relative,
            offset_x,
            offset_y,
            local_transform,
        )
    return transformed


def transform_svg_point(
    x, y, transform: dict, is_relative: bool, offset_x, offset_y, local_transform
):
    a, b, c, d, e, f = local_transform
    if is_relative:
        local_x = x * a + y * c
        local_y = x * b + y * d
        return local_x * transform["scale_x"], local_y * transform["scale_y"]
    x += offset_x
    y += offset_y
    local_x = x * a + y * c + e
    local_y = x * b + y * d + f
    # OT-SVG は y 軸が下向き (svg_y = -font_y) のため、font 空間で求めた
    # translate_y は SVG 空間では符号を反転して適用する
    return (
        local_x * transform["scale_x"] + transform["translate_x"],
        local_y * transform["scale_y"] - transform["translate_y"],
    )


def format_svg_number(value):
    return f"{value:.6f}".rstrip("0").rstrip(".")


def clamp_fixed_16_16(value):
    return max(min(value, 32767), -32768)


def clamp_short(value):
    return max(min(value, 32767), -32768)


def fix_font_tables(style, variant, input_font_name: str = None):
    """フォントテーブルを編集する"""

    if input_font_name is None:
        input_font_name = f"{FONTTOOLS_PREFIX}{FONT_NAME}{variant}-{style}_merged.ttf"
    output_name_base = f"{FONTTOOLS_PREFIX}{FONT_NAME}{variant}-{style}"
    completed_name_base = f"{FONT_NAME.replace(' ', '')}{variant}-{style}"
    completed_font_path = f"{BUILD_FONTS_DIR}/{completed_name_base}.ttf"

    # OS/2, post テーブルのみのttxファイルを出力
    xml = dump_ttx(input_font_name, output_name_base)
    # OS/2 テーブルを編集
    fix_os2_table(xml, style, flag_35=WIDTH_35_STR in variant)
    # post テーブルを編集
    fix_post_table(xml, flag_35=WIDTH_35_STR in variant)
    # cmap テーブルを編集
    fix_cmap_table(xml, style, variant, input_font_name)

    # ttxファイルを上書き保存
    xml.write(
        f"{BUILD_FONTS_DIR}/{output_name_base}.ttx",
        encoding="utf-8",
        xml_declaration=True,
    )

    # ttxファイルをttfファイルに適用
    ttx.main(
        [
            "-o",
            completed_font_path,
            "-m",
            f"{BUILD_FONTS_DIR}/{input_font_name}",
            f"{BUILD_FONTS_DIR}/{output_name_base}.ttx",
        ]
    )


def dump_ttx(input_name_base, output_name_base) -> ET:
    """OS/2, post テーブルのみのttxファイルを出力"""
    ttx.main(
        [
            "-t",
            "OS/2",
            "-t",
            "post",
            "-t",
            "cmap",
            "-f",
            "-o",
            f"{BUILD_FONTS_DIR}/{output_name_base}.ttx",
            f"{BUILD_FONTS_DIR}/{input_name_base}",
        ]
    )

    return ET.parse(f"{BUILD_FONTS_DIR}/{output_name_base}.ttx")


def fix_os2_table(xml: ET, style: str, flag_35: bool = False):
    """OS/2 テーブルを編集する"""
    # xAvgCharWidthを編集
    # タグ形式: <xAvgCharWidth value="1000"/>
    if flag_35:
        x_avg_char_width = FULL_WIDTH_35
    else:
        x_avg_char_width = HALF_WIDTH_12
    xml.find("OS_2/xAvgCharWidth").set("value", str(x_avg_char_width))

    # fsSelectionを編集
    # タグ形式: <fsSelection value="00000000 11000000" />
    # スタイルに応じたビットを立てる
    fs_selection = None
    if style == "Regular":
        fs_selection = "00000001 01000000"
    elif style == "Italic":
        fs_selection = "00000001 00000001"
    elif style == "Bold":
        fs_selection = "00000001 00100000"
    elif style == "BoldItalic":
        fs_selection = "00000001 00100001"

    if fs_selection is not None:
        xml.find("OS_2/fsSelection").set("value", fs_selection)

    # panoseを編集
    # タグ形式:
    # <panose>
    #   <bFamilyType value="2" />
    #   <bSerifStyle value="11" />
    #   <bWeight value="6" />
    #   <bProportion value="9" />
    #   <bContrast value="6" />
    #   <bStrokeVariation value="3" />
    #   <bArmStyle value="0" />
    #   <bLetterForm value="2" />
    #   <bMidline value="0" />
    #   <bXHeight value="4" />
    # </panose>
    if style == "Regular" or style == "Italic":
        bWeight = 5
    else:
        bWeight = 8
    if flag_35:
        panose = {
            "bFamilyType": 2,
            "bSerifStyle": 11,
            "bWeight": bWeight,
            "bProportion": 3,
            "bContrast": 2,
            "bStrokeVariation": 2,
            "bArmStyle": 3,
            "bLetterForm": 2,
            "bMidline": 2,
            "bXHeight": 7,
        }
    else:
        panose = {
            "bFamilyType": 2,
            "bSerifStyle": 11,
            "bWeight": bWeight,
            "bProportion": 9,
            "bContrast": 2,
            "bStrokeVariation": 2,
            "bArmStyle": 3,
            "bLetterForm": 2,
            "bMidline": 2,
            "bXHeight": 7,
        }

    for key, value in panose.items():
        xml.find(f"OS_2/panose/{key}").set("value", str(value))


def fix_post_table(xml: ET, flag_35):
    """post テーブルを編集する"""
    # isFixedPitchを編集
    # タグ形式: <isFixedPitch value="0"/>
    is_fixed_pitch = 0 if flag_35 else 1
    xml.find("post/isFixedPitch").set("value", str(is_fixed_pitch))
    # underlinePosition, underlineThicknessを編集
    # <underlinePosition value="-155"/>
    # <underlineThickness value="50"/>
    # EM 1000 -> 2048 の拡大率に合わせて値を調整
    xml.find("post/underlinePosition").set("value", "-317")
    xml.find("post/underlineThickness").set("value", "102")


def fix_cmap_table(xml: ET, style: str, variant: str, input_font_name: str):
    """異体字シーケンスを搭載するために cmap テーブルを編集する。
    pyftmerge で結合すると異体字シーケンスを司るテーブル cmap_format_14 が
    消えてしまうため、マージする前の編集済み日本語フォントから該当テーブル情報を取り出して適用する。"""
    # タグ形式:
    # <cmap_format_14 platformID="0" platEncID="5">
    #   <map uv="0x4fae" uvs="0xfe00" name="uniFA30"/>
    #   <map uv="0x50e7" uvs="0xfe00" name="uniFA31"/>
    # </cmap_format_14>
    source_xml = dump_ttx(
        f"{FONTFORGE_PREFIX}{FONT_NAME}{variant}-{style}-jp.ttf",
        f"{FONTFORGE_PREFIX}{FONT_NAME}{variant}-{style}-jp",
    )
    source_cmap_format_14 = source_xml.find("cmap/cmap_format_14")
    input_font = ttLib.TTFont(f"{BUILD_FONTS_DIR}/{input_font_name}", recalcBBoxes=False)
    glyph_names = set(input_font.getGlyphOrder())
    input_font.close()
    for cmap_map in list(source_cmap_format_14):
        if cmap_map.get("name") is None:
            continue
        glyph_name = cmap_map.get("name").split("#", 1)[0]
        if glyph_name not in glyph_names:
            source_cmap_format_14.remove(cmap_map)
    target_cmap = xml.find("cmap")
    target_cmap.append(source_cmap_format_14)


if __name__ == "__main__":
    main()
