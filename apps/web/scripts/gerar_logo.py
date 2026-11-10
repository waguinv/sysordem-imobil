"""Gera as logos SysOrdem Imobil (SVG) a partir da logo oficial SysOrdem.

Mantém o símbolo e a palavra SYSORDEM do kit da marca (copiados da logo do SysOrdem Saúde)
e desenha a segunda linha "IMOBIL" com a fonte Inter, no mesmo espaço, altura e espaçamento
da linha "SAÚDE" original.

Uso:
  pip install fonttools
  python scripts/gerar_logo.py <logo-horizontal-color.svg do SysOrdem> <inter-latin-600-normal.woff> public/brand
"""

import re
import sys
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

GREEN, DARK, WHITE = "#00C99A", "#003B2D", "#FFFFFF"
TITLE = "SysOrdem Imobil"
WORD = "IMOBIL"
# geometria da 2ª linha na logo oficial (x, topo, altura das maiúsculas, largura total)
X, TOP, CAP, WIDTH, TRACK = 264, 146, 54, 721, 1.45


def second_line(font_path: Path, color: str) -> str:
    font = TTFont(font_path)
    gs = font.getGlyphSet()
    cmap = font.getBestCmap()
    cap = font["OS/2"].sCapHeight
    k = CAP / cap
    names = [cmap[ord(c)] for c in WORD]
    # bounds horizontais de cada glifo
    from fontTools.pens.boundsPen import BoundsPen

    bounds = []
    for n in names:
        bp = BoundsPen(gs)
        gs[n].draw(bp)
        bounds.append(bp.bounds)
    widths = [(b[2] - b[0]) for b in bounds]
    sx = k * TRACK  # letras alargadas como na linha original
    gap = (WIDTH - sum(w * sx for w in widths)) / (len(WORD) - 1)

    pen = SVGPathPen(gs)
    cursor = 0.0
    for n, b, w in zip(names, bounds, widths, strict=True):
        # coordenadas finais em px; y invertido (fonte y↑, SVG y↓), base em TOP+CAP
        t = TransformPen(pen, (sx, 0, 0, -k, X + cursor - b[0] * sx, TOP + CAP))
        gs[n].draw(t)
        cursor += w * sx + gap
    return f'<path fill="{color}" d="{pen.getCommands()}"/>'


def main() -> None:
    ref = Path(sys.argv[1]).read_text(encoding="utf-8")
    font_path = Path(sys.argv[2])
    out = Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)

    emblem = re.search(r"<g transform=\"matrix\(\.45 0 0 \.45 8 15\)\">.*?</g>", ref, re.S)
    words = re.findall(r'<path fill="#[0-9A-F]{6}" transform="translate\([^"]+\)" d="[^"]+"/>', ref)
    assert emblem and len(words) == 3, "logo de referência inesperada"
    base_body = emblem.group(0) + words[0] + words[1]  # símbolo + SYS + ORDEM

    variants = {"color": (GREEN, DARK, GREEN), "reverse": (GREEN, WHITE, GREEN), "white": (WHITE, WHITE, WHITE)}
    for name, (a, b, accent) in variants.items():
        body = base_body.replace(f'fill="{GREEN}"', "fill=A").replace(f'fill="{DARK}"', "fill=B")
        body = body.replace("fill=A", f'fill="{a}"').replace("fill=B", f'fill="{b}"')
        body += second_line(font_path, accent)
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="230" viewBox="0 0 1000 230" '
            f'role="img" aria-label="{TITLE}"><title>{TITLE}</title>{body}</svg>\n'
        )
        (out / f"logo-horizontal-{name}.svg").write_text(svg, encoding="utf-8")

        sym = re.sub(r"<g transform=\"[^\"]+\">", '<g transform="translate(15 10)">', emblem.group(0))
        sym = sym.replace(f'fill="{GREEN}"', "fill=A").replace(f'fill="{DARK}"', "fill=B")
        sym = sym.replace("fill=A", f'fill="{a}"').replace("fill=B", f'fill="{b}"')
        (out / f"simbolo-{name}.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="510" height="470" viewBox="0 0 510 470" '
            f'role="img" aria-label="{TITLE}"><title>{TITLE}</title>{sym}</svg>\n',
            encoding="utf-8",
        )
    print("Logos geradas em", out)


if __name__ == "__main__":
    main()
