# -*- coding: utf-8 -*-
"""Ядро конструктора: спецификация брелока → превью (SVG) и файлы для печати.

Геометрию считает generate_keychain.py, здесь только то, что нужно приложению:
проверка ввода, лимиты, SVG-превью, расчёт веса/времени и запись файлов.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trimesh                                                    # noqa: E402
from shapely import affinity                                      # noqa: E402

import generate_keychain as gk                                    # noqa: E402

# буквы, которые бывают на российских номерах (совпадают по начертанию с латиницей)
LETTERS = "АВЕКМНОРСТУХ"
DIGITS = "0123456789"
PLATE_MASK = "LDDDLL"          # Р 433 ЕК — буква, три цифры, две буквы

PLA_DENSITY = 1.24             # г/см³
MOUNTS = {
    "hole": "Отверстие в пластине",
    "ear":  "Ушко под карабин",
    "none": "Без крепления",
}


@dataclass
class Spec:
    number: str = "Р433ЕК"
    region: str = "126"
    back_text: str = "Changan"
    mount: str = "ear"
    hole_d: float = 6.0
    length: float = 60.0
    base_h: float = 2.4
    text_h: float = 0.6
    back_depth: float = 0.4
    layer_h: float = 0.2
    with_rus: bool = True

    def clean(self) -> "Spec":
        """Приводим ввод в чувство, не ругаясь на пользователя."""
        self.number = "".join(ch for ch in self.number.upper() if not ch.isspace())[:9]
        self.region = "".join(ch for ch in self.region if ch.isdigit())[:3]
        self.back_text = " ".join(self.back_text.split())[:24]
        if self.mount not in MOUNTS:
            self.mount = "hole"
        self.length = _clamp(self.length, 40.0, 120.0)
        self.layer_h = _clamp(self.layer_h, 0.06, 0.32)
        snap = lambda v: round(round(v / self.layer_h) * self.layer_h, 3)   # noqa: E731
        self.base_h = snap(_clamp(self.base_h, 1.0, 6.0))
        self.text_h = snap(_clamp(self.text_h, 0.2, 2.0))
        self.back_depth = snap(_clamp(self.back_depth, 0.0, 1.5))
        lo, hi = gk.hole_limits(self.length, self.mount)
        self.hole_d = _clamp(self.hole_d, lo, hi)
        return self

    def tag(self) -> str:
        return gk.tag_of(self.number, self.region)

    def title(self) -> str:
        return f"{self.number} {self.region}".strip()


def _clamp(v: float, lo: float, hi: float) -> float:
    return round(min(max(float(v), lo), hi), 3)


def limits(length: float, mount: str) -> dict:
    lo, hi = gk.hole_limits(length, mount)
    return {"min": lo, "max": hi}


# ─────────────────────────────────────────────────────────────────────────────
# Геометрия
# ─────────────────────────────────────────────────────────────────────────────
def design(spec: Spec) -> gk.Design:
    d = gk.Design(number=spec.number, region=spec.region, length=spec.length,
                  base_h=spec.base_h, text_h=spec.text_h, hole_d=spec.hole_d,
                  mount=spec.mount, back_text=spec.back_text,
                  back_depth=spec.back_depth, with_rus=spec.with_rus)
    return gk.build(d)


def _path(geom) -> str:
    """shapely → атрибут d для <path> (дырки работают через fill-rule=evenodd)."""
    if geom is None or geom.is_empty:
        return ""
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    out = []
    for p in polys:
        if p.geom_type != "Polygon":
            continue
        for ring in [p.exterior, *p.interiors]:
            pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in ring.coords)
            out.append("M" + pts + "Z")
    return " ".join(out)


def preview(d: gk.Design) -> dict:
    """Два SVG: лицо и оборот (оборот — как будто брелок перевернули в руке)."""
    mnx, mny, mxx, mxy = d.plate.bounds
    W, H = mxx - mnx, mxy - mny
    pad = 1.5
    vb = f"{mnx - pad:.2f} {-pad:.2f} {W + 2 * pad:.2f} {H + 2 * pad:.2f}"
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" '
            f'preserveAspectRatio="xMidYMid meet" class="plate-svg">'
            f'<g transform="translate(0,{mxy + mny:.3f}) scale(1,-1)">')

    front = (head
             + f'<path class="p-body" d="{_path(d.plate)}" fill-rule="evenodd"/>'
             + f'<path class="p-ink" d="{_path(d.ink)}" fill-rule="evenodd"/>'
             + "</g></svg>")

    cy = (mny + mxy) / 2
    flip = lambda g: affinity.scale(g, 1, -1, origin=(0, cy))      # noqa: E731
    back_plate, back_ink = flip(d.plate), flip(d.back) if d.back is not None else None
    back = (head
            + f'<path class="p-body" d="{_path(back_plate)}" fill-rule="evenodd"/>'
            + (f'<path class="p-engrave" d="{_path(back_ink)}" fill-rule="evenodd"/>'
               if back_ink is not None else "")
            + "</g></svg>")
    return {"front": front, "back": back}


def stats(spec: Spec, d: gk.Design) -> dict:
    """Габариты, слой паузы, вес и прикидка времени печати."""
    i = d.info
    mnx, mny, mxx, mxy = d.plate.bounds
    area_mm3 = d.plate.area * spec.base_h + d.ink.area * spec.text_h
    if d.back is not None and not d.back.is_empty:
        area_mm3 -= d.back.area * spec.back_depth
    vol = area_mm3 / 1000.0
    n_base = int(round(spec.base_h / spec.layer_h))
    warn = []

    lo, hi = gk.hole_limits(spec.length, spec.mount)
    if spec.mount == "hole":
        gap = (i["H"] - spec.hole_d) / 2
        if gap < 2.4:
            warn.append(f"От отверстия до края всего {gap:.1f} мм — тонковато. "
                        f"Возьми ушко под карабин или увеличь длину брелока.")
        if i["scale"] < 0.8:
            warn.append("Большое отверстие сильно ужало номер — знаки стали мельче.")
    if spec.mount == "ear" and spec.hole_d >= 9:
        warn.append("Ушко получилось выше самой пластины — это нормально, "
                    "но брелок станет заметно крупнее.")
    if i["digit_h"] < 6:
        warn.append("Цифры мельче 6 мм — читаться будет, но мелко. Прибавь длину.")
    if abs(spec.base_h / spec.layer_h - n_base) > 1e-6:
        warn.append("Толщина подложки не кратна высоте слоя — цвет разделится криво.")

    return {
        "size_x": round(mxx - mnx, 2), "size_y": round(mxy - mny, 2),
        "size_z": round(spec.base_h + spec.text_h, 2),
        "digit_h": round(i["digit_h"], 2), "letter_h": round(i["letter_h"], 2),
        "back_cap": round(i["back_cap"], 2),
        "hole_d": spec.hole_d if i["hole"] else 0,
        "hole_min": lo, "hole_max": hi,
        "pause_layer": n_base + 1,
        "pause_z": round(spec.base_h + spec.layer_h, 2),
        "base_layers": n_base,
        "text_layers": int(round(spec.text_h / spec.layer_h)),
        "back_layers": int(round(spec.back_depth / spec.layer_h)),
        "volume": round(vol, 2),
        "grams": round(vol * PLA_DENSITY, 1),
        "minutes": int(round(6 + vol * 7)),
        "warnings": warn,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Файлы
# ─────────────────────────────────────────────────────────────────────────────
HOWTO = """КАК ПЕЧАТАТЬ ЭТОТ БРЕЛОК НА BAMBU LAB A1 БЕЗ AMS
{title}

1. Открой файл {f3mf} в Bambu Studio (File - Open Project).
   Уведомление «load geometry data only» — это нормально, файл не сломан.
   Заправь БЕЛЫЙ пластик. Профиль 0.20 mm Standard, заполнение 100%, без поддержек.
   ВЫСОТУ СЛОЯ НЕ МЕНЯТЬ: {layer_h} мм.

2. Нажми «Нарезка» (Slice) и перейди в «Предпросмотр» (Preview).
   На вертикальном ползунке слоёв справа:
     «+» -> Jump to Layer -> {pause_layer} -> OK
     (проверь: на этом слое уже видны буквы, на предыдущем — чистая пластина)
     «+» -> Add Pause
   Нажми «Нарезка» ещё раз — иначе пауза не попадёт в G-code.

3. Печатай. На Z = {pause_z} мм принтер встанет на паузу.
   На экране принтера: Филамент -> Выгрузить, поставь ЧЁРНЫЙ -> Загрузить.
   Прогони пластик, пока из сопла не пойдёт чистый чёрный, и жми Resume.

Слоёв белой подложки: {base_layers} (0 .. {base_h} мм)
Слоёв чёрного рельефа: {text_layers} ({base_h} .. {top} мм)
{back_line}Габарит: {size_x} x {size_y} x {size_z} мм, примерно {grams} г пластика.

Файл ..._2_filamenta.3mf — для принтера с AMS: деталям уже назначены
филамент 1 (белый) и филамент 2 (чёрный), пауза не нужна.
"""


def _howto(spec: Spec, st: dict, fname: str) -> str:
    back_line = ""
    if spec.back_text.strip():
        back_line = (f"На обороте вдавлена надпись «{spec.back_text}» "
                     f"({st['back_layers']} нижних слоя). В предпросмотре первый слой\n"
                     f"будет с прорезями в виде этих букв — так и надо.\n")
    return HOWTO.format(title=spec.title(), f3mf=fname, layer_h=spec.layer_h,
                        pause_layer=st["pause_layer"], pause_z=st["pause_z"],
                        base_layers=st["base_layers"], base_h=spec.base_h,
                        text_layers=st["text_layers"],
                        top=round(spec.base_h + spec.text_h, 2),
                        back_line=back_line, size_x=st["size_x"], size_y=st["size_y"],
                        size_z=st["size_z"], grams=st["grams"])


def generate(specs: list[Spec], outdir: str, want_stl: bool = True,
             combined: bool = True, gap: float = 7.0) -> dict:
    """Пишет 3MF (и STL) для каждого брелока + общий стол, если их несколько."""
    os.makedirs(outdir, exist_ok=True)
    made, files = [], []

    for spec in specs:
        spec.clean()
        d = design(spec)
        st = stats(spec, d)
        base, text = gk.make_meshes(d)
        tag = spec.tag()
        folder = os.path.join(outdir, tag) if len(specs) > 1 else outdir
        os.makedirs(folder, exist_ok=True)

        name = f"Брелок {spec.title()}"
        parts2 = [("1. Подложка — БЕЛЫЙ пластик", base, 1),
                  ("2. Буквы и рамка — ЧЁРНЫЙ пластик", text, 2)]
        parts1 = [(p[0], p[1], 1) for p in parts2]

        main3mf = f"brelok_{tag}_A1_bez_AMS.3mf"
        gk.write_3mf(os.path.join(folder, main3mf), [(name, parts1, (0, 0))],
                     pause_z=st["pause_z"])
        gk.write_3mf(os.path.join(folder, f"brelok_{tag}_2_filamenta.3mf"),
                     [(name, parts2, (0, 0))])
        files += [os.path.join(folder, main3mf),
                  os.path.join(folder, f"brelok_{tag}_2_filamenta.3mf")]

        if want_stl:
            stl_dir = os.path.join(folder, "stl")
            os.makedirs(stl_dir, exist_ok=True)
            try:
                whole = trimesh.boolean.union([base, text], engine="manifold")
            except Exception:
                whole = trimesh.util.concatenate([base, text])
            for mesh, fn in ((base, f"{tag}_1_podlozhka_BELAYA.stl"),
                             (text, f"{tag}_2_bukvy_CHERNYE.stl"),
                             (whole, f"{tag}_brelok_celikom.stl")):
                mesh.export(os.path.join(stl_dir, fn))
                files.append(os.path.join(stl_dir, fn))

        pv = preview(d)
        for key, fn in (("front", f"{tag}_lico.svg"), ("back", f"{tag}_oborot.svg")):
            with open(os.path.join(folder, fn), "w", encoding="utf-8") as fh:
                fh.write(pv[key])
            files.append(os.path.join(folder, fn))

        howto = os.path.join(folder, "КАК ПЕЧАТАТЬ.txt")
        with open(howto, "w", encoding="utf-8-sig") as fh:
            fh.write(_howto(spec, st, main3mf))
        files.append(howto)

        made.append(dict(spec=spec, design=d, base=base, text=text,
                         parts1=parts1, parts2=parts2, name=name, stats=st))

    combo = None
    if combined and len(made) > 1:
        step = max(v["design"].info["H"] for v in made) + gap
        y0 = -step * (len(made) - 1) / 2
        objs1, objs2 = [], []
        for k, v in enumerate(made):
            dy = y0 + k * step
            objs1.append((v["name"], v["parts1"], (0.0, dy)))
            objs2.append((v["name"], v["parts2"], (0.0, dy)))
        combo = os.path.join(outdir, "VSE_brelki_odin_stol_A1_bez_AMS.3mf")
        gk.write_3mf(combo, objs1, pause_z=made[0]["stats"]["pause_z"])
        gk.write_3mf(os.path.join(outdir, "VSE_brelki_odin_stol_2_filamenta.3mf"), objs2)
        files += [combo, os.path.join(outdir, "VSE_brelki_odin_stol_2_filamenta.3mf")]

    return {
        "folder": os.path.abspath(outdir),
        "files": [os.path.abspath(f) for f in files],
        "combined": os.path.abspath(combo) if combo else None,
        "items": [{"title": v["spec"].title(), **v["stats"]} for v in made],
        "grams": round(sum(v["stats"]["grams"] for v in made), 1),
        "minutes": int(round(6 + sum(v["stats"]["volume"] for v in made) * 7)),
    }


def default_outdir() -> str:
    """Куда складывать файлы по умолчанию: Рабочий стол, если он есть."""
    home = os.path.expanduser("~")
    for name in ("Desktop", "Рабочий стол", "OneDrive/Desktop", "OneDrive/Рабочий стол"):
        p = os.path.join(home, *name.split("/"))
        if os.path.isdir(p):
            return os.path.join(p, "Брелоки 3D")
    return os.path.join(home, "Брелоки 3D")


def spec_from_dict(data: dict) -> Spec:
    fields = {k: v for k, v in (data or {}).items() if k in Spec.__annotations__}
    return Spec(**{**asdict(Spec()), **fields}).clean()
