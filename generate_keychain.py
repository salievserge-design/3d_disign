#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор брелка-автомобильного номера РФ (ГОСТ Р 50577, тип 1) для двухцветной
печати на Bambu Lab A1 БЕЗ AMS (одна пауза на смену филамента).

Модель состоит из двух деталей, которые точно стыкуются по высоте:
  1) подложка (белый пластик)  : Z = 0 .. BASE_H
  2) буквы/цифры/рамка (чёрный): Z = BASE_H .. BASE_H + TEXT_H

BASE_H кратна высоте слоя, поэтому граница цветов ложится ровно на границу слоёв.

Выход: STL (3 шт.), 3MF для Bambu Studio (2 детали в одном объекте),
3MF с заранее заложенной паузой и PNG-превью.

Использование:
    python generate_keychain.py --number Р788РК --region 126 --length 85
"""
from __future__ import annotations

import argparse
import math
import os
import zipfile
from dataclasses import dataclass, field

import numpy as np
import trimesh
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from shapely import affinity
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_PLATE = os.path.join(HERE, "fonts", "RoadNumbers2.0.ttf")

# ─────────────────────────────────────────────────────────────────────────────
# Пропорции настоящего номера (ГОСТ Р 50577-2018, тип 1), в мм на пластине
# 520 × 112 мм. Всё остальное считается из них масштабированием.
# ─────────────────────────────────────────────────────────────────────────────
G_LEN, G_H = 520.0, 112.0
G_CORNER_R = 10.0        # радиус скругления пластины
G_FRAME_INSET = 4.26     # отступ внешнего края чёрной окантовки от края пластины
G_FRAME_W = 4.87         # толщина окантовки
G_FRAME_RX = 19.9        # скругление окантовки по горизонтали (оно эллиптическое)
G_FRAME_RY = 7.8         # скругление окантовки по вертикали
G_DIGIT_H = 76.0         # высота цифр основной группы
G_REGION_DIGIT_H = 58.0  # высота цифр кода региона
G_RUS_H = 20.0           # высота надписи RUS
G_REGION_FIELD = 111.9   # ширина поля кода региона (внутри окантовки)
G_GAP_IN = 9.3           # просвет между соседними знаками внутри группы
G_GAP_OUT = 19.0         # просвет между группами (буква | цифры | буквы)
G_REGION_GAP = 8.0       # просвет между цифрами региона
G_MARGIN_MIN = 6.0       # минимальные поля вокруг текста
G_REGION_MARGIN = 11.0   # минимальные поля в блоке региона

# Кириллица номерных знаков → латинские двойники (в шрифте номеров только они)
CYR2LAT = {
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
}


# ─────────────────────────────────────────────────────────────────────────────
# Контуры глифов → полигоны shapely
# ─────────────────────────────────────────────────────────────────────────────
class PolyPen(BasePen):
    """Переводит контуры глифа в ломаные (кривые Безье разбиваются на отрезки)."""

    def __init__(self, glyph_set, steps: int = 12):
        super().__init__(glyph_set)
        self.contours: list[list[tuple[float, float]]] = []
        self.cur: list[tuple[float, float]] = []
        self.steps = steps

    def _moveTo(self, pt):
        self.cur = [pt]

    def _lineTo(self, pt):
        self.cur.append(pt)

    def _curveToOne(self, p1, p2, p3):
        p0 = self.cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            u = 1 - t
            self.cur.append((
                u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
            ))

    def _qCurveToOne(self, p1, p2):
        p0 = self.cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            u = 1 - t
            self.cur.append((
                u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
            ))

    def _closePath(self):
        if self.cur and len(self.cur) > 2:
            self.contours.append(self.cur)
        self.cur = []

    def _endPath(self):
        self._closePath()


class Font:
    def __init__(self, path: str):
        self.tt = TTFont(path)
        self.cmap = self.tt.getBestCmap()
        self.gs = self.tt.getGlyphSet()
        self.upem = self.tt["head"].unitsPerEm

    def has(self, ch: str) -> bool:
        return ord(ch) in self.cmap

    def glyph(self, ch: str) -> Polygon:
        """Полигон глифа в единицах em (базовая линия y=0, начало пера x=0)."""
        gname = self.cmap[ord(ch)]
        pen = PolyPen(self.gs)
        self.gs[gname].draw(pen)
        rings = []
        for c in pen.contours:
            p = Polygon(c)
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty and p.area > 0:
                rings.append(p)
        if not rings:
            raise ValueError(f"нет контура для {ch!r}")
        # вложенность: чётная глубина — тело, нечётная — дырка
        shells, holes = [], []
        for i, r in enumerate(rings):
            pt = r.representative_point()
            depth = sum(1 for j, o in enumerate(rings) if j != i and o.contains(pt))
            (holes if depth % 2 else shells).append(r)
        geom = unary_union(shells)
        if holes:
            geom = geom.difference(unary_union(holes))
        return geom


@dataclass
class Placed:
    geom: object
    ink_w: float
    ink_h: float


def sized_char(font: Font, ch: str, cap_h: float, ref_h: float) -> Placed:
    """Глиф, отмасштабированный так, чтобы эталонная высота ref_h (в em) стала cap_h мм.
    Возвращает геометрию с базовой линией y=0 и левым краем чернил x=0."""
    g = font.glyph(ch)
    k = cap_h / (ref_h * font.upem)
    g = affinity.scale(g, k, k, origin=(0, 0))
    minx, miny, maxx, maxy = g.bounds
    g = affinity.translate(g, -minx, 0)
    return Placed(g, maxx - minx, maxy - miny)


# ─────────────────────────────────────────────────────────────────────────────
# Геометрия
# ─────────────────────────────────────────────────────────────────────────────
def rounded_rect(x0, y0, x1, y1, r) -> Polygon:
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    if r <= 0:
        return box(x0, y0, x1, y1)
    return box(x0 + r, y0 + r, x1 - r, y1 - r).buffer(r, quad_segs=24, join_style=1)


def ellipse_rect(x0, y0, x1, y1, rx, ry) -> Polygon:
    """Прямоугольник с эллиптическими скруглениями углов (как окантовка по ГОСТ)."""
    k = ry / rx
    g = rounded_rect(x0 * k, y0, x1 * k, y1, ry)
    return affinity.scale(g, 1 / k, 1.0, origin=(0, 0))


@dataclass
class Design:
    number: str = "Р788РК"
    region: str = "126"
    length: float = 85.0
    base_h: float = 2.4
    text_h: float = 0.6
    hole_d: float = 3.5
    with_hole: bool = True
    with_frame: bool = True
    with_rus: bool = True
    min_stroke: float = 0.8   # минимальная толщина чёрных элементов, мм

    # заполняется в build()
    plate: Polygon = field(default=None, repr=False)
    ink: object = field(default=None, repr=False)
    info: dict = field(default_factory=dict, repr=False)


def build(d: Design) -> Design:
    f = Font(FONT_PLATE)
    S = d.length / G_LEN                       # масштаб «настоящий номер → брелок»
    H = G_H * S
    corner = G_CORNER_R * S
    frame_in = G_FRAME_INSET * S
    frame_w = max(d.min_stroke, G_FRAME_W * S)

    # опорные высоты глифов в шрифте (em): цифра «8» и буква «P»
    dig_ref = f.glyph("8").bounds[3] / f.upem
    let_ref = f.glyph("P").bounds[3] / f.upem

    plate = rounded_rect(0, 0, d.length, H, corner)

    inner_x0, inner_x1 = frame_in + frame_w, d.length - frame_in - frame_w
    inner_y0, inner_y1 = frame_in + frame_w, H - frame_in - frame_w

    black = []          # чёрные элементы (2D)

    # ── окантовка (осевая линия + равномерная толщина) ───────────────────────
    if d.with_frame:
        c = frame_in + frame_w / 2
        mid = ellipse_rect(c, c, d.length - c, H - c,
                           max(G_FRAME_RX * S, frame_w), max(G_FRAME_RY * S, frame_w))
        black.append(mid.exterior.buffer(frame_w / 2, quad_segs=16))

    # ── разделитель и поле региона ───────────────────────────────────────────
    has_region = bool(d.region)
    if has_region:
        region_w = G_REGION_FIELD * S
        div_x = inner_x1 - region_w                      # левый край поля региона
        div = box(div_x - frame_w, inner_y0, div_x, inner_y1)
        if d.with_frame:
            div = box(div_x - frame_w, frame_in, div_x, H - frame_in)
        black.append(div)
        main_x1 = div_x - frame_w
    else:
        main_x1 = inner_x1

    # ── отверстие под кольцо (в левом поле, как штатное крепёжное) ───────────
    hole_r = d.hole_d / 2
    hole_cx = max(inner_x0 + hole_r + 1.2, 2.4 + hole_r)
    hole_cy = H / 2
    main_x0 = inner_x0
    if d.with_hole:
        main_x0 = hole_cx + hole_r + 1.0                # текст начинается за отверстием

    # ── основная группа (буква цифры цифры цифры буква буква) ───────────────
    chars = [CYR2LAT.get(c.upper(), c.upper()) for c in d.number]
    kinds = ["L" if c.isalpha() else "D" for c in chars]

    def layout_main(scale: float):
        cap = G_DIGIT_H * S * scale
        items, total = [], 0.0
        for i, (c, k) in enumerate(zip(chars, kinds)):
            ref = dig_ref if k == "D" else let_ref
            p = sized_char(f, c, cap * (1 if k == "D" else let_ref / dig_ref), dig_ref)
            if i:
                gap = (G_GAP_OUT if kinds[i - 1] != k else G_GAP_IN) * S * scale
                total += gap
            items.append((p, total))
            total += p.ink_w
        return items, total

    avail = (main_x1 - main_x0) - 2 * G_MARGIN_MIN * S
    scale = 1.0
    items, total = layout_main(scale)
    if total > avail:
        scale = avail / total
        items, total = layout_main(scale)
    x0 = main_x0 + ((main_x1 - main_x0) - total) / 2
    base_y = inner_y0 + ((inner_y1 - inner_y0) - G_DIGIT_H * S * scale) / 2
    for p, dx in items:
        black.append(affinity.translate(p.geom, x0 + dx, base_y))

    # ── код региона + RUS ────────────────────────────────────────────────────
    if has_region:
        rx0, rx1 = div_x, inner_x1
        rus_h = G_RUS_H * S if d.with_rus else 0.0
        # цифры региона: при необходимости ужимаем, чтобы влезли 3 знака
        def layout_region(scale: float):
            cap = G_REGION_DIGIT_H * S * scale
            items, total = [], 0.0
            for i, c in enumerate(d.region):
                p = sized_char(f, c, cap, dig_ref)
                if i:
                    total += G_REGION_GAP * S * scale
                items.append((p, total))
                total += p.ink_w
            return items, total

        avail_r = (rx1 - rx0) - 2 * (G_REGION_MARGIN * S)
        rs = 1.0
        ritems, rtotal = layout_region(rs)
        if rtotal > avail_r:
            rs = avail_r / rtotal
            ritems, rtotal = layout_region(rs)
        rcap = G_REGION_DIGIT_H * S * rs
        # по вертикали: цифры сверху, RUS снизу
        free = (inner_y1 - inner_y0) - rcap - rus_h
        pad = free / 3
        rbase_y = inner_y0 + pad * 2 + rus_h
        rx = rx0 + ((rx1 - rx0) - rtotal) / 2
        for p, dx in ritems:
            black.append(affinity.translate(p.geom, rx + dx, rbase_y))

        if d.with_rus:
            rus = rus_geometry(rus_h)
            w = rus.bounds[2] - rus.bounds[0]
            black.append(affinity.translate(rus, rx0 + ((rx1 - rx0) - w) / 2,
                                            inner_y0 + pad))

    ink = unary_union(black)
    if d.with_hole:
        hole = Polygon([(hole_cx + hole_r * math.cos(t), hole_cy + hole_r * math.sin(t))
                        for t in np.linspace(0, 2 * math.pi, 64, endpoint=False)])
        plate = plate.difference(hole)
        ink = ink.difference(hole.buffer(0.4))

    ink = ink.intersection(plate)

    d.plate, d.ink = plate, ink
    d.info = dict(S=S, H=H, frame_w=frame_w, scale=scale,
                  digit_h=G_DIGIT_H * S * scale,
                  letter_h=G_DIGIT_H * S * scale * let_ref / dig_ref,
                  hole=(hole_cx, hole_cy, d.hole_d) if d.with_hole else None,
                  corner=corner)
    return d


def rus_geometry(cap_h: float):
    """Надпись RUS шрифтом DejaVu Sans Bold (в шрифте номеров нет латиницы R/U/S)."""
    import matplotlib
    path = os.path.join(os.path.dirname(matplotlib.__file__),
                        "mpl-data", "fonts", "ttf", "DejaVuSans-Bold.ttf")
    f = Font(path)
    ref = f.glyph("R").bounds[3] / f.upem
    parts, x = [], 0.0
    for i, ch in enumerate("RUS"):
        p = sized_char(f, ch, cap_h, ref)
        if i:
            x += cap_h * 0.14
        parts.append(affinity.translate(p.geom, x, 0))
        x += p.ink_w
    return unary_union(parts)


# ─────────────────────────────────────────────────────────────────────────────
# 2D → 3D
# ─────────────────────────────────────────────────────────────────────────────
def extrude(geom, height: float, z: float = 0.0) -> trimesh.Trimesh:
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    meshes = []
    for p in polys:
        if p.is_empty or p.area < 1e-9:
            continue
        m = trimesh.creation.extrude_polygon(p, height)
        meshes.append(m)
    mesh = trimesh.util.concatenate(meshes)
    if z:
        mesh.apply_translation([0, 0, z])
    return mesh


# ─────────────────────────────────────────────────────────────────────────────
# 3MF (формат Bambu Studio: один объект, две детали)
# ─────────────────────────────────────────────────────────────────────────────
CT = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="png" ContentType="image/png"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def _mesh_xml(mesh: trimesh.Trimesh) -> str:
    v = mesh.vertices
    fa = mesh.faces
    out = ["    <mesh>\n     <vertices>\n"]
    out.extend(f'      <vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>\n' for x, y, z in v)
    out.append("     </vertices>\n     <triangles>\n")
    out.extend(f'      <triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in fa)
    out.append("     </triangles>\n    </mesh>\n")
    return "".join(out)


def write_3mf(path: str, parts: list[tuple[str, trimesh.Trimesh, int]],
              obj_name: str, plate_xy=(128.0, 128.0), pause_z: float | None = None):
    """parts: [(имя, меш, номер филамента)]; меш уже в координатах модели (центр XY = 0)."""
    ids = list(range(1, len(parts) + 1))
    top_id = len(parts) + 1

    m = ['<?xml version="1.0" encoding="UTF-8"?>\n',
         '<model unit="millimeter" xml:lang="en-US" '
         'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
         'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">\n',
         ' <metadata name="Application">brelok-generator</metadata>\n',
         ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n',
         ' <resources>\n']
    for (name, mesh, _), oid in zip(parts, ids):
        m.append(f'  <object id="{oid}" type="model">\n')
        m.append(_mesh_xml(mesh))
        m.append('  </object>\n')
    m.append(f'  <object id="{top_id}" type="model">\n   <components>\n')
    for oid in ids:
        m.append(f'    <component objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n')
    m.append('   </components>\n  </object>\n </resources>\n <build>\n')
    m.append(f'  <item objectid="{top_id}" '
             f'transform="1 0 0 0 1 0 0 0 1 {plate_xy[0]:.4f} {plate_xy[1]:.4f} 0" '
             f'printable="1"/>\n </build>\n</model>\n')
    model_xml = "".join(m)

    cfg = ['<?xml version="1.0" encoding="UTF-8"?>\n<config>\n',
           f'  <object id="{top_id}">\n',
           f'    <metadata key="name" value="{obj_name}"/>\n',
           '    <metadata key="extruder" value="1"/>\n']
    for (name, mesh, ext), oid in zip(parts, ids):
        cfg.append(f'    <part id="{oid}" subtype="normal_part">\n')
        cfg.append(f'      <metadata key="name" value="{name}"/>\n')
        cfg.append('      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n')
        cfg.append(f'      <metadata key="extruder" value="{ext}"/>\n')
        cfg.append('      <mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0"'
                   ' facets_reversed="0" backwards_edges="0"/>\n')
        cfg.append('    </part>\n')
    cfg.append('  </object>\n  <plate>\n    <metadata key="plater_id" value="1"/>\n')
    cfg.append('    <metadata key="plater_name" value=""/>\n')
    cfg.append('    <metadata key="locked" value="false"/>\n')
    cfg.append(f'    <model_instance>\n      <metadata key="object_id" value="{top_id}"/>\n')
    cfg.append('      <metadata key="instance_id" value="0"/>\n    </model_instance>\n')
    cfg.append('  </plate>\n  <assemble>\n')
    cfg.append(f'   <assemble_item object_id="{top_id}" instance_id="0" '
               f'transform="1 0 0 0 1 0 0 0 1 {plate_xy[0]:.4f} {plate_xy[1]:.4f} 0" '
               'offset="0 0 0"/>\n')
    cfg.append('  </assemble>\n</config>\n')
    cfg_xml = "".join(cfg)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", model_xml)
        z.writestr("Metadata/model_settings.config", cfg_xml)
        if pause_z is not None:
            z.writestr("Metadata/custom_gcode_per_layer.xml",
                       '<?xml version="1.0" encoding="utf-8"?>\n'
                       '<custom_gcodes_per_layer>\n  <plate>\n'
                       '    <plate_info id="1"/>\n'
                       f'    <layer top_z="{pause_z:.2f}" type="1" extruder="1" '
                       'color="#000000" extra="" gcode="M601"/>\n'
                       '    <mode value="SingleExtruder"/>\n'
                       '  </plate>\n</custom_gcodes_per_layer>\n')


# ─────────────────────────────────────────────────────────────────────────────
# Превью
# ─────────────────────────────────────────────────────────────────────────────
def preview_top(d: Design, path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MPath

    def patch(geom, **kw):
        polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
        verts, codes = [], []
        for p in polys:
            for ring in [p.exterior, *p.interiors]:
                c = np.asarray(ring.coords)
                verts.extend(c)
                codes.extend([MPath.MOVETO] + [MPath.LINETO] * (len(c) - 2) + [MPath.CLOSEPOLY])
        return PathPatch(MPath(verts, codes), **kw)

    W, H = d.length, d.info["H"]
    fig, ax = plt.subplots(figsize=(W / 8, (H + 10) / 8), dpi=200)
    ax.add_patch(patch(d.plate, facecolor="white", edgecolor="#b8bcc2", lw=1.2, zorder=2))
    ax.add_patch(patch(d.ink, facecolor="#16181c", edgecolor="none", zorder=3))
    ax.set_xlim(-3, W + 3)
    ax.set_ylim(-6, H + 6)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_facecolor("#eef0f3")
    ax.text(W / 2, -4.2, f"{W:g} × {H:.1f} × {d.base_h + d.text_h:g} мм",
            ha="center", va="center", fontsize=6, color="#6b7280")
    fig.tight_layout(pad=0.4)
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


def _box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Быстрое размытие (для мягкой тени под моделью)."""
    if r < 1:
        return a
    pad = np.pad(a, r, mode="edge")
    c = np.cumsum(np.cumsum(pad, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)))
    k = 2 * r + 1
    h, w = a.shape
    out = (c[k:k + h, k:k + w] - c[0:h, k:k + w] - c[k:k + h, 0:w] + c[0:h, 0:w])
    return out / (k * k)


def preview_pause(d: Design, path: str, layer_h: float):
    """Шпаргалка: как выглядит предпросмотр Bambu Studio на слое до и после паузы."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MPath

    def patch(geom, **kw):
        polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
        verts, codes = [], []
        for p in polys:
            for ring in [p.exterior, *p.interiors]:
                c = np.asarray(ring.coords)
                verts.extend(c)
                codes.extend([MPath.MOVETO] + [MPath.LINETO] * (len(c) - 2) + [MPath.CLOSEPOLY])
        return PathPatch(MPath(verts, codes), **kw)

    n_base = int(round(d.base_h / layer_h))
    W, H = d.length, d.info["H"]
    fig, axes = plt.subplots(2, 1, figsize=(W / 9, 2 * (H + 16) / 9), dpi=200)
    for ax, show_ink in zip(axes, (False, True)):
        ax.add_patch(patch(d.plate, facecolor="#f4f5f7", edgecolor="#9aa0a6", lw=1.0, zorder=2))
        if show_ink:
            ax.add_patch(patch(d.ink, facecolor="#16181c", edgecolor="none", zorder=3))
        ax.set_xlim(-2, W + 2)
        ax.set_ylim(-11, H + 3)
        ax.set_aspect("equal")
        ax.axis("off")
        if show_ink:
            t1 = f"слой {n_base + 1}   ·   Z = {d.base_h + layer_h:.2f} мм   ·   ПОЯВИЛИСЬ БУКВЫ"
            t2 = "◀  сюда ставим паузу (Add Pause)"
            c1, c2 = "#0b6b2f", "#0b6b2f"
        else:
            t1 = f"слой {n_base}   ·   Z = {d.base_h:.2f} мм   ·   только белая пластина"
            t2 = "рано — букв ещё нет"
            c1, c2 = "#6b7280", "#9aa0a6"
        ax.text(1, -4.5, t1, ha="left", va="center", fontsize=6.2, color=c1, weight="bold")
        ax.text(W - 1, -4.5, t2, ha="right", va="center", fontsize=6.2, color=c2)
    fig.patch.set_facecolor("#eef0f3")
    fig.tight_layout(pad=0.5)
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


def preview_3d(meshes_colors, path: str, w=1700, h=780, elev=27.0, azim=-48.0, ss=2):
    """Собственный растеризатор с Z-буфером: matplotlib неверно сортирует
    накладывающиеся треугольники и «топит» буквы в подложке."""
    from PIL import Image

    W, H = w * ss, h * ss
    V, F, C = [], [], []
    off = 0
    for mesh, rgb in meshes_colors:
        V.append(mesh.vertices)
        F.append(mesh.faces + off)
        C.append(np.tile(np.asarray(rgb, float), (len(mesh.faces), 1)))
        off += len(mesh.vertices)
    V = np.vstack(V)
    F = np.vstack(F)
    C = np.vstack(C)

    el, az = math.radians(elev), math.radians(azim)
    fwd = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    right = np.cross([0, 0, 1.0], fwd)
    right /= np.linalg.norm(right)
    up = np.cross(fwd, right)

    P = np.column_stack([V @ right, V @ up, V @ fwd])
    mn, mx = P[:, :2].min(axis=0), P[:, :2].max(axis=0)
    span = (mx - mn)
    sc = min(W * 0.88 / span[0], H * 0.88 / span[1])
    px = (P[:, 0] - (mn[0] + mx[0]) / 2) * sc + W / 2
    py = H / 2 - (P[:, 1] - (mn[1] + mx[1]) / 2) * sc
    pz = P[:, 2]

    tri = np.stack([px[F], py[F], pz[F]], axis=-1)          # (n,3,3)
    normals = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    ln = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.where(ln == 0, 1, ln)
    facing = normals @ fwd
    keep = facing > 1e-6
    tri, C, normals, facing = tri[keep], C[keep], normals[keep], facing[keep]

    light = np.array([0.45, -0.5, 0.74])
    light /= np.linalg.norm(light)
    diff = np.clip(normals @ light, 0, 1)
    spec = np.clip(normals @ ((light + fwd) / np.linalg.norm(light + fwd)), 0, 1) ** 28
    shade = np.clip(0.30 + 0.72 * diff, 0, 1)[:, None] * C + 0.28 * spec[:, None]
    shade = np.clip(shade, 0, 1)

    img = np.ones((H, W, 3), float) * np.array([0.933, 0.941, 0.953])
    zbuf = np.full((H, W), -1e18)
    cov = np.zeros((H, W), bool)

    order = np.argsort(-tri[:, :, 2].mean(axis=1))
    for i in order:
        t = tri[i]
        x0 = max(int(np.floor(t[:, 0].min())), 0)
        x1 = min(int(np.ceil(t[:, 0].max())), W - 1)
        y0 = max(int(np.floor(t[:, 1].min())), 0)
        y1 = min(int(np.ceil(t[:, 1].max())), H - 1)
        if x1 < x0 or y1 < y0:
            continue
        X, Y = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        (ax_, ay_), (bx, by), (cx_, cy_) = t[0, :2], t[1, :2], t[2, :2]
        den = (by - cy_) * (ax_ - cx_) + (cx_ - bx) * (ay_ - cy_)
        if abs(den) < 1e-12:
            continue
        l0 = ((by - cy_) * (X - cx_) + (cx_ - bx) * (Y - cy_)) / den
        l1 = ((cy_ - ay_) * (X - cx_) + (ax_ - cx_) * (Y - cy_)) / den
        l2 = 1.0 - l0 - l1
        m = (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)
        if not m.any():
            continue
        z = l0 * t[0, 2] + l1 * t[1, 2] + l2 * t[2, 2]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        upd = m & (z > sub)
        if not upd.any():
            continue
        sub[upd] = z[upd]
        img[y0:y1 + 1, x0:x1 + 1][upd] = shade[i]
        cov[y0:y1 + 1, x0:x1 + 1] |= upd

    # мягкая тень
    sh = _box_blur(cov.astype(float), max(2, int(9 * ss)))
    dy, dx = int(26 * ss), int(14 * ss)
    sh = np.roll(np.roll(sh, dy, axis=0), dx, axis=1)
    sh[:dy, :] = 0
    sh[:, :dx] = 0
    sh = np.clip(sh * 0.55, 0, 1) * (~cov)
    img = img * (1 - sh[:, :, None] * 0.55)

    out = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
    out = out.resize((w, h), Image.LANCZOS)
    out.save(path)


# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Брелок-номер РФ для двухцветной печати")
    ap.add_argument("--number", default="Р788РК", help="серия и номер, например Р788РК")
    ap.add_argument("--region", default="126", help="код региона, например 126")
    ap.add_argument("--length", type=float, default=85.0, help="длина брелка, мм")
    ap.add_argument("--base-h", type=float, default=2.4, help="толщина белой подложки, мм")
    ap.add_argument("--text-h", type=float, default=0.6, help="высота чёрного рельефа, мм")
    ap.add_argument("--hole-d", type=float, default=3.5, help="диаметр отверстия, мм")
    ap.add_argument("--no-hole", action="store_true")
    ap.add_argument("--no-rus", action="store_true")
    ap.add_argument("--layer-h", type=float, default=0.2, help="высота слоя для расчёта паузы")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    a = ap.parse_args()

    d = Design(number=a.number, region=a.region, length=a.length, base_h=a.base_h,
               text_h=a.text_h, hole_d=a.hole_d, with_hole=not a.no_hole,
               with_rus=not a.no_rus)
    build(d)

    os.makedirs(a.out, exist_ok=True)
    os.makedirs(os.path.join(a.out, "stl"), exist_ok=True)

    base = extrude(d.plate, d.base_h)
    text = extrude(d.ink, d.text_h, z=d.base_h)
    # центрируем по XY (Bambu Studio ставит объект в центр стола)
    cx, cy = d.length / 2, d.info["H"] / 2
    for m in (base, text):
        m.apply_translation([-cx, -cy, 0])

    tag = (a.number + "_" + a.region).upper()
    trans = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")
    tag = tag.translate(trans)

    # цельная модель одним телом (для варианта «один STL + пауза»)
    try:
        whole = trimesh.boolean.union([base, text], engine="manifold")
    except Exception:
        whole = trimesh.util.concatenate([base, text])

    stl_dir = os.path.join(a.out, "stl")
    base.export(os.path.join(stl_dir, f"{tag}_1_podlozhka_BELAYA.stl"))
    text.export(os.path.join(stl_dir, f"{tag}_2_bukvy_CHERNYE.stl"))
    whole.export(os.path.join(stl_dir, f"{tag}_brelok_celikom.stl"))

    parts = [("1. Подложка — БЕЛЫЙ пластик", base, 1),
             ("2. Буквы и рамка — ЧЁРНЫЙ пластик", text, 2)]
    parts1 = [("1. Подложка — БЕЛЫЙ пластик", base, 1),
              ("2. Буквы и рамка — ЧЁРНЫЙ пластик", text, 1)]
    name = f"Брелок {a.number} {a.region}"
    write_3mf(os.path.join(a.out, f"brelok_{tag}_A1_bez_AMS.3mf"), parts1, name,
              pause_z=round(d.base_h + a.layer_h, 3))
    write_3mf(os.path.join(a.out, f"brelok_{tag}_2_filamenta.3mf"), parts, name)

    preview_top(d, os.path.join(a.out, "preview_vid_sverhu.png"))
    preview_3d([(base, (0.95, 0.95, 0.96)), (text, (0.11, 0.11, 0.13))],
               os.path.join(a.out, "preview_3d.png"))
    preview_pause(d, os.path.join(a.out, "preview_sloy_pauzy.png"), a.layer_h)

    i = d.info
    print(f"номер           : {a.number} {a.region}")
    print(f"габарит         : {a.length:g} × {i['H']:.2f} × {d.base_h + d.text_h:g} мм")
    print(f"цифры / буквы   : {i['digit_h']:.2f} / {i['letter_h']:.2f} мм "
          f"(масштаб текста {i['scale']*100:.0f}%)")
    print(f"рамка           : {i['frame_w']:.2f} мм")
    print(f"отверстие       : {i['hole']}")
    n_base = int(round(d.base_h / a.layer_h))
    print(f"подложка        : 0 .. {d.base_h} мм  = {n_base} слоёв по {a.layer_h}")
    print(f"чёрный рельеф   : {d.base_h} .. {d.base_h + d.text_h} мм = "
          f"{d.text_h / a.layer_h:.0f} слоя")
    print(f"ПАУЗА           : перед слоем {n_base + 1} (Z = {d.base_h + a.layer_h:.2f} мм) — "
          f"ставится вручную в Bambu Studio: «+» → Add Pause")
    print(f"треугольников   : подложка {len(base.faces)}, текст {len(text.faces)}")
    print(f"герметичность   : подложка {base.is_watertight}, текст {text.is_watertight}")
    print(f"объём           : {(base.volume + text.volume)/1000:.2f} см³")


if __name__ == "__main__":
    main()
