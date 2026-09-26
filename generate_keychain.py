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
WHITE = (0.95, 0.95, 0.96)   # цвета превью: белый пластик / чёрный пластик
BLACK = (0.11, 0.11, 0.13)
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

    def advance(self, ch: str) -> float:
        gname = self.cmap.get(ord(ch))
        return self.gs[gname].width if gname else 0.5 * self.upem

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
            if p.is_empty or p.area <= 0:
                continue
            # знаковая площадь исходного контура = направление обхода;
            # в TrueType тело и счётчик («дырка») всегда идут в разные стороны
            sa = 0.5 * sum(c[i][0] * c[(i + 1) % len(c)][1] -
                           c[(i + 1) % len(c)][0] * c[i][1] for i in range(len(c)))
            rings.append((p.area, math.copysign(1, sa), p))
        if not rings:
            raise ValueError(f"нет контура для {ch!r}")
        # идём от самого большого контура к мелким: сонаправленный с внешним —
        # добавляем, встречный — вычитаем. Так корректно ложатся и «о», и «8»,
        # и островок внутри дырки, и перекрывающиеся контуры составных глифов.
        rings.sort(key=lambda t: -t[0])
        body = rings[0][1]
        geom = Polygon()
        for _, sign, p in rings:
            geom = geom.union(p) if sign == body else geom.difference(p)
        return geom

    def line(self, text: str, cap_h: float, tracking: float = 0.04):
        """Обычная строка текста: раскладка по ширинам глифов, базовая линия y=0."""
        ref = self.glyph("M").bounds[3] / self.upem
        k = cap_h / (ref * self.upem)
        parts, x = [], 0.0
        for ch in text:
            if ch.strip() and self.has(ch):
                try:
                    parts.append(affinity.translate(
                        affinity.scale(self.glyph(ch), k, k, origin=(0, 0)), x, 0))
                except ValueError:
                    pass
            x += self.advance(ch) * k + tracking * cap_h
        geom = unary_union(parts)
        return affinity.translate(geom, -geom.bounds[0], 0)


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
    length: float = 60.0
    base_h: float = 2.4
    text_h: float = 0.6
    hole_d: float = 4.0
    back_text: str = "Москвич 3"
    back_depth: float = 0.4   # глубина гравировки на обороте (кратна слою!)
    with_hole: bool = True
    with_frame: bool = True
    with_rus: bool = True
    min_stroke: float = 0.8   # минимальная толщина чёрных элементов, мм
    min_rus_h: float = 2.6    # мельче надпись RUS печатать уже нет смысла

    # заполняется в build()
    plate: Polygon = field(default=None, repr=False)
    ink: object = field(default=None, repr=False)
    back: object = field(default=None, repr=False)
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

    avail = (main_x1 - main_x0) - 2 * max(G_MARGIN_MIN * S, 1.2)
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
        rus_h = max(G_RUS_H * S, d.min_rus_h) if d.with_rus else 0.0
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

        avail_r = (rx1 - rx0) - 2 * max(G_REGION_MARGIN * S, 1.0)
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

    # ── гравировка на обороте (печатается на столе, поэтому зеркалим) ────────
    back, back_cap = None, 0.0
    if d.back_text.strip():
        bx0 = (hole_cx + hole_r + 1.5) if d.with_hole else 2.0
        bx1 = d.length - 2.0
        box_w, box_h = bx1 - bx0, H - 2 * 1.8
        g = dejavu().line(d.back_text, 5.0)
        gw = g.bounds[2] - g.bounds[0]
        gh = g.bounds[3] - g.bounds[1]
        back_cap = 5.0 * min(box_w / gw, box_h / gh, 1.0)
        g = dejavu().line(d.back_text, back_cap)
        g = affinity.scale(g, 1, -1, origin=(0, 0))          # зеркало: смотрим снизу
        mnx, mny, mxx, mxy = g.bounds
        back = affinity.translate(g, bx0 + (box_w - (mxx - mnx)) / 2 - mnx,
                                  H / 2 - (mny + mxy) / 2)
        back = back.intersection(plate.buffer(-1.0))

    d.plate, d.ink, d.back = plate, ink, back
    d.info = dict(S=S, H=H, frame_w=frame_w, scale=scale,
                  digit_h=G_DIGIT_H * S * scale,
                  letter_h=G_DIGIT_H * S * scale * let_ref / dig_ref,
                  hole=(hole_cx, hole_cy, d.hole_d) if d.with_hole else None,
                  corner=corner, back_cap=back_cap)
    return d


_DEJAVU = None


def dejavu() -> Font:
    """DejaVu Sans Bold (идёт вместе с matplotlib) — для RUS и надписи на обороте:
    в шрифте госномеров нет ни латиницы R/U/S, ни строчной кириллицы."""
    global _DEJAVU
    if _DEJAVU is None:
        import matplotlib
        _DEJAVU = Font(os.path.join(os.path.dirname(matplotlib.__file__),
                                    "mpl-data", "fonts", "ttf", "DejaVuSans-Bold.ttf"))
    return _DEJAVU


def rus_geometry(cap_h: float):
    """Надпись RUS шрифтом DejaVu Sans Bold (в шрифте номеров нет латиницы R/U/S)."""
    f = dejavu()
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
        if not m.is_watertight:
            # триангуляция спотыкается о почти совпадающие точки после buffer():
            # чистим их (допуск 1 мкм — на порядки меньше сопла)
            for fix in (p.simplify(0.001), p.buffer(0.0005).buffer(-0.0005)):
                cand = trimesh.creation.extrude_polygon(fix, height)
                if cand.is_watertight:
                    m = cand
                    break
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


def write_3mf(path: str, objects, plate_xy=(128.0, 128.0), pause_z: float | None = None):
    """objects: [(имя объекта, [(имя части, меш, филамент), ...], (dx, dy))].
    Меши в координатах модели (центр XY = 0), dx/dy — сдвиг от центра стола."""
    if isinstance(objects, tuple):
        objects = [objects]

    # раздаём id: сначала детали каждого объекта, потом сам объект-контейнер
    plan, next_id = [], 1
    for name, parts, off in objects:
        part_ids = list(range(next_id, next_id + len(parts)))
        next_id += len(parts)
        plan.append((name, parts, off, part_ids, next_id))
        next_id += 1

    m = ['<?xml version="1.0" encoding="UTF-8"?>\n',
         '<model unit="millimeter" xml:lang="en-US" '
         'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
         'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">\n',
         ' <metadata name="Application">brelok-generator</metadata>\n',
         ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n',
         ' <resources>\n']
    for name, parts, off, part_ids, top_id in plan:
        for (pname, mesh, _), oid in zip(parts, part_ids):
            m.append(f'  <object id="{oid}" type="model">\n')
            m.append(_mesh_xml(mesh))
            m.append('  </object>\n')
        m.append(f'  <object id="{top_id}" type="model">\n   <components>\n')
        for oid in part_ids:
            m.append(f'    <component objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n')
        m.append('   </components>\n  </object>\n')
    m.append(' </resources>\n <build>\n')
    for name, parts, off, part_ids, top_id in plan:
        x, y = plate_xy[0] + off[0], plate_xy[1] + off[1]
        m.append(f'  <item objectid="{top_id}" '
                 f'transform="1 0 0 0 1 0 0 0 1 {x:.4f} {y:.4f} 0" printable="1"/>\n')
    m.append(' </build>\n</model>\n')
    model_xml = "".join(m)

    cfg = ['<?xml version="1.0" encoding="UTF-8"?>\n<config>\n']
    for name, parts, off, part_ids, top_id in plan:
        cfg.append(f'  <object id="{top_id}">\n')
        cfg.append(f'    <metadata key="name" value="{name}"/>\n')
        cfg.append('    <metadata key="extruder" value="1"/>\n')
        for (pname, mesh, ext), oid in zip(parts, part_ids):
            cfg.append(f'    <part id="{oid}" subtype="normal_part">\n')
            cfg.append(f'      <metadata key="name" value="{pname}"/>\n')
            cfg.append('      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n')
            cfg.append(f'      <metadata key="extruder" value="{ext}"/>\n')
            cfg.append('      <mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0"'
                       ' facets_reversed="0" backwards_edges="0"/>\n')
            cfg.append('    </part>\n')
        cfg.append('  </object>\n')
    cfg.append('  <plate>\n    <metadata key="plater_id" value="1"/>\n')
    cfg.append('    <metadata key="plater_name" value=""/>\n')
    cfg.append('    <metadata key="locked" value="false"/>\n')
    for name, parts, off, part_ids, top_id in plan:
        cfg.append(f'    <model_instance>\n      <metadata key="object_id" value="{top_id}"/>\n')
        cfg.append('      <metadata key="instance_id" value="0"/>\n    </model_instance>\n')
    cfg.append('  </plate>\n  <assemble>\n')
    for name, parts, off, part_ids, top_id in plan:
        x, y = plate_xy[0] + off[0], plate_xy[1] + off[1]
        cfg.append(f'   <assemble_item object_id="{top_id}" instance_id="0" '
                   f'transform="1 0 0 0 1 0 0 0 1 {x:.4f} {y:.4f} 0" offset="0 0 0"/>\n')
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
def make_meshes(d: Design):
    """Подложка (с вырезанной гравировкой) и чёрный рельеф, отцентрованные по XY."""
    base = extrude(d.plate, d.base_h)
    if d.back is not None and not d.back.is_empty:
        cutter = extrude(d.back, d.back_depth + 0.2, z=-0.2)   # с запасом вниз
        try:
            base = trimesh.boolean.difference([base, cutter], engine="manifold")
        except Exception as e:
            print("!! не удалось вырезать гравировку:", e)
    text = extrude(d.ink, d.text_h, z=d.base_h)
    cx, cy = d.length / 2, d.info["H"] / 2
    for m in (base, text):
        m.apply_translation([-cx, -cy, 0])
    return base, text


def tag_of(number: str, region: str) -> str:
    """Имя файла латиницей: Р788РК 126 → P788PK_126."""
    return (number + "_" + region).upper().translate(
        str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX"))


def make_variant(number: str, region: str, back_text: str, a, outdir: str):
    d = Design(number=number, region=region, length=a.length, base_h=a.base_h,
               text_h=a.text_h, hole_d=a.hole_d, back_text=back_text,
               back_depth=a.back_depth, with_hole=not a.no_hole,
               with_rus=not a.no_rus)
    build(d)
    base, text = make_meshes(d)
    tag = tag_of(number, region)

    os.makedirs(os.path.join(outdir, "stl"), exist_ok=True)
    try:
        whole = trimesh.boolean.union([base, text], engine="manifold")
    except Exception:
        whole = trimesh.util.concatenate([base, text])
    stl_dir = os.path.join(outdir, "stl")
    base.export(os.path.join(stl_dir, f"{tag}_1_podlozhka_BELAYA.stl"))
    text.export(os.path.join(stl_dir, f"{tag}_2_bukvy_CHERNYE.stl"))
    whole.export(os.path.join(stl_dir, f"{tag}_brelok_celikom.stl"))

    name = f"Брелок {number} {region}"
    parts2 = [("1. Подложка — БЕЛЫЙ пластик", base, 1),
              ("2. Буквы и рамка — ЧЁРНЫЙ пластик", text, 2)]
    parts1 = [(p[0], p[1], 1) for p in parts2]
    write_3mf(os.path.join(outdir, f"brelok_{tag}_A1_bez_AMS.3mf"), [(name, parts1, (0, 0))],
              pause_z=round(d.base_h + a.layer_h, 3))
    write_3mf(os.path.join(outdir, f"brelok_{tag}_2_filamenta.3mf"), [(name, parts2, (0, 0))])

    preview_top(d, os.path.join(outdir, "preview_vid_sverhu.png"))
    preview_3d([(base, WHITE), (text, BLACK)], os.path.join(outdir, "preview_3d.png"))
    preview_pause(d, os.path.join(outdir, "preview_sloy_pauzy.png"), a.layer_h)
    # вид на оборот: не двигаем камеру, а честно переворачиваем брелок через
    # длинную ось — ровно так его перевернёт рука, держащая за колечко
    flip = trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0])
    preview_3d([(base.copy().apply_transform(flip), WHITE),
                (text.copy().apply_transform(flip), BLACK)],
               os.path.join(outdir, "preview_3d_oborot.png"))

    i = d.info
    n_base = int(round(d.base_h / a.layer_h))
    print(f"\n── {number} {region} → {os.path.relpath(outdir, HERE)}/")
    print(f"   габарит       : {a.length:g} × {i['H']:.2f} × {d.base_h + d.text_h:g} мм")
    print(f"   цифры / буквы : {i['digit_h']:.2f} / {i['letter_h']:.2f} мм "
          f"(масштаб текста {i['scale']*100:.0f}%)")
    print(f"   отверстие     : Ø{d.hole_d} мм" if i['hole'] else "   отверстие     : нет")
    if back_text.strip():
        print(f"   оборот        : «{back_text}», буквы {i['back_cap']:.2f} мм, "
              f"глубина {d.back_depth} мм ({d.back_depth / a.layer_h:.0f} слоя), зеркально")
    print(f"   подложка      : 0 .. {d.base_h} мм = {n_base} слоёв по {a.layer_h}")
    print(f"   ПАУЗА         : перед слоем {n_base + 1} (Z = {d.base_h + a.layer_h:.2f} мм)")
    print(f"   герметичность : подложка {base.is_watertight}, текст {text.is_watertight}")
    print(f"   объём         : {(base.volume + text.volume) / 1000:.2f} см³ "
          f"≈ {(base.volume + text.volume) / 1000 * 1.24:.1f} г PLA")
    return dict(d=d, base=base, text=text, tag=tag, name=name, parts1=parts1, parts2=parts2)


def main():
    ap = argparse.ArgumentParser(description="Брелок-номер РФ для двухцветной печати")
    ap.add_argument("--brelok", action="append", metavar='"НОМЕР РЕГИОН НАДПИСЬ"',
                    help="брелок: номер, регион и надпись на обороте через пробел. "
                         "Ключ можно повторить — тогда будет ещё и общий стол")
    ap.add_argument("--number", default="Р788РК", help="серия и номер, например Р788РК")
    ap.add_argument("--region", default="126", help="код региона, например 126")
    ap.add_argument("--back-text", default="Москвич 3",
                    help="надпись, вдавленная в обратную сторону ('' — без неё)")
    ap.add_argument("--length", type=float, default=60.0, help="длина брелка, мм")
    ap.add_argument("--base-h", type=float, default=2.4, help="толщина белой подложки, мм")
    ap.add_argument("--text-h", type=float, default=0.6, help="высота чёрного рельефа, мм")
    ap.add_argument("--hole-d", type=float, default=4.0, help="диаметр отверстия, мм")
    ap.add_argument("--back-depth", type=float, default=0.4,
                    help="глубина гравировки на обороте, мм (кратна высоте слоя)")
    ap.add_argument("--no-hole", action="store_true")
    ap.add_argument("--no-rus", action="store_true")
    ap.add_argument("--layer-h", type=float, default=0.2, help="высота слоя для расчёта паузы")
    ap.add_argument("--gap", type=float, default=7.0, help="зазор между брелоками на столе, мм")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    a = ap.parse_args()

    specs = []
    for s in (a.brelok or []):
        w = s.split()
        if len(w) < 2:
            ap.error(f"--brelok {s!r}: нужно как минимум «НОМЕР РЕГИОН»")
        specs.append((w[0], w[1], " ".join(w[2:])))
    if not specs:
        specs = [(a.number, a.region, a.back_text)]

    os.makedirs(a.out, exist_ok=True)
    made = [make_variant(n, r, b, a, os.path.join(a.out, tag_of(n, r))) for n, r, b in specs]

    if len(made) > 1:
        # все брелоки на одном столе: высота подложки у них одинаковая,
        # поэтому одной паузы хватает на всю пластину
        step = max(v["d"].info["H"] for v in made) + a.gap
        y0 = -step * (len(made) - 1) / 2
        objs1, objs2, scene = [], [], []
        for k, v in enumerate(made):
            dy = y0 + k * step
            objs1.append((v["name"], v["parts1"], (0.0, dy)))
            objs2.append((v["name"], v["parts2"], (0.0, dy)))
            scene += [(v["base"].copy().apply_translation([0, dy, 0]), WHITE),
                      (v["text"].copy().apply_translation([0, dy, 0]), BLACK)]
        d0 = made[0]["d"]
        write_3mf(os.path.join(a.out, "vse_brelki_odin_stol_A1_bez_AMS.3mf"), objs1,
                  pause_z=round(d0.base_h + a.layer_h, 3))
        write_3mf(os.path.join(a.out, "vse_brelki_odin_stol_2_filamenta.3mf"), objs2)
        preview_3d(scene, os.path.join(a.out, "preview_vse_brelki.png"),
                   h=460 + 300 * len(made), elev=34.0)
        print(f"\n── общий стол: {len(made)} брелока, шаг {step:.1f} мм → "
              f"{os.path.relpath(a.out, HERE)}/vse_brelki_odin_stol_A1_bez_AMS.3mf")
        print(f"   печатаются одновременно, смена филамента одна на оба "
              f"(слой {int(round(d0.base_h / a.layer_h)) + 1})")
        print(f"   всего пластика: "
              f"{sum(v['base'].volume + v['text'].volume for v in made) / 1000 * 1.24:.1f} г")


if __name__ == "__main__":
    main()
