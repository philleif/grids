"""IDML (InDesign Markup Language) export for GRIDS calling cards.

Generates a valid .idml file (ZIP archive of XML files) that can be
opened directly in Adobe InDesign. Maps our CardSpec, CmykColor, and
TypographySpec to IDML's XML structure:

  - designmap.xml           -- document manifest
  - META-INF/container.xml  -- package identifier
  - Resources/Fonts.xml     -- font declarations
  - Resources/Styles.xml    -- paragraph + character styles
  - Resources/Graphic.xml   -- color swatches + object styles
  - Spreads/Spread_1.xml    -- page layout with text frames
  - Stories/Story_*.xml     -- text content per frame

Output is a .idml file that preserves:
  - Exact card dimensions (with bleed)
  - CMYK color definitions as named swatches
  - Font families as paragraph/character styles
  - Text frame positions matching our TikZ layout
  - Editable text (name, title, org, contact lines)
"""

import os
import uuid
import zipfile
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element, SubElement, tostring

from rich.console import Console

from grids.typeset.engine import CardContent, CardSpec, CmykColor, TypographySpec

console = Console(stderr=True)


def _uid() -> str:
    return str(uuid.uuid4()).replace("-", "")[:8]


def _pt_to_idml(pt: float) -> str:
    """IDML uses points as default unit."""
    return f"{pt:.4f}"


# ---------------------------------------------------------------------------
# XML generators for each IDML package part
# ---------------------------------------------------------------------------

def _mimetype() -> bytes:
    return b"application/vnd.adobe.indesign-idml-package+xml"


def _container_xml() -> bytes:
    root = Element("container", xmlns="urn:oasis:names:tc:opendocument:xmlns:container")
    root.set("version", "1.0")
    rootfiles = SubElement(root, "rootfiles")
    rf = SubElement(rootfiles, "rootfile")
    rf.set("full-path", "designmap.xml")
    rf.set("media-type", "text/xml")
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + tostring(root, encoding="unicode").encode("utf-8")


def _designmap_xml(
    story_ids: list[str],
    spread_ids: list[str],
) -> bytes:
    root = Element("Document")
    root.set("DOMVersion", "18.0")
    root.set("Self", "d")

    # Document preferences
    dp = SubElement(root, "DocumentPreference")
    dp.set("PageHeight", "187.92")  # 2.61" in points
    dp.set("PageWidth", "221.04")   # 3.07" in points
    dp.set("FacingPages", "false")
    dp.set("DocumentBleedTopOffset", "9")
    dp.set("DocumentBleedBottomOffset", "9")
    dp.set("DocumentBleedInsideOrLeftOffset", "9")
    dp.set("DocumentBleedOutsideOrRightOffset", "9")

    # Color preferences
    cp = SubElement(root, "ColorPreference")
    cp.set("ColorModel", "Process")
    cp.set("ColorSpace", "CMYK")

    # Map stories
    for sid in story_ids:
        s = SubElement(root, "idPkg:Story", src=f"Stories/Story_{sid}.xml")
        s.set("xmlns:idPkg", "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging")

    # Map spreads
    for spid in spread_ids:
        sp = SubElement(root, "idPkg:Spread", src=f"Spreads/Spread_{spid}.xml")
        sp.set("xmlns:idPkg", "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging")

    # Resource refs
    for res in ["Fonts", "Styles", "Graphic"]:
        r = SubElement(root, f"idPkg:{res}", src=f"Resources/{res}.xml")
        r.set("xmlns:idPkg", "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging")

    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + tostring(root, encoding="unicode").encode("utf-8")


def _fonts_xml(typography: TypographySpec) -> bytes:
    root = Element("idPkg:Fonts")
    root.set("xmlns:idPkg", "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging")
    root.set("DOMVersion", "18.0")

    for font_name in [typography.primary_font, typography.secondary_font]:
        if font_name:
            ff = SubElement(root, "FontFamily")
            ff.set("Self", f"FontFamily/{font_name}")
            ff.set("Name", font_name)
            face = SubElement(ff, "Font")
            face.set("Self", f"FontFamily/{font_name}\tRegular")
            face.set("FontFamily", font_name)
            face.set("Name", "Regular")
            face.set("PostScriptName", font_name.replace(" ", ""))

    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + tostring(root, encoding="unicode").encode("utf-8")


def _graphic_xml(
    primary: CmykColor,
    secondary: CmykColor | None,
) -> bytes:
    root = Element("idPkg:Graphic")
    root.set("xmlns:idPkg", "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging")
    root.set("DOMVersion", "18.0")

    for color in [primary, secondary]:
        if color is None:
            continue
        swatch = SubElement(root, "Color")
        swatch.set("Self", f"Color/{color.name}")
        swatch.set("Name", color.name)
        swatch.set("Model", "Process")
        swatch.set("Space", "CMYK")
        swatch.set("ColorValue", f"{color.c:.1f} {color.m:.1f} {color.y:.1f} {color.k:.1f}")

    # Default object style
    os_elem = SubElement(root, "ObjectStyle")
    os_elem.set("Self", "ObjectStyle/$ID/[None]")
    os_elem.set("Name", "[None]")

    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + tostring(root, encoding="unicode").encode("utf-8")


def _styles_xml(
    typography: TypographySpec,
    primary: CmykColor,
    secondary: CmykColor | None,
) -> bytes:
    root = Element("idPkg:Styles")
    root.set("xmlns:idPkg", "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging")
    root.set("DOMVersion", "18.0")

    # Root paragraph style group
    psg = SubElement(root, "RootParagraphStyleGroup")
    psg.set("Self", "u14f")

    # Heading style
    heading = SubElement(psg, "ParagraphStyle")
    heading.set("Self", "ParagraphStyle/Heading")
    heading.set("Name", "Heading")
    heading.set("FontStyle", "Bold")
    heading.set("PointSize", str(typography.heading_size_pt))
    heading.set("Justification", "CenterAlign")
    if typography.primary_font:
        heading.set("AppliedFont", typography.primary_font)
    heading.set("FillColor", f"Color/{primary.name}")

    # Body style
    body = SubElement(psg, "ParagraphStyle")
    body.set("Self", "ParagraphStyle/Body")
    body.set("Name", "Body")
    body.set("FontStyle", "Regular")
    body.set("PointSize", str(typography.body_size_pt))
    body.set("Justification", "CenterAlign")
    if typography.primary_font:
        body.set("AppliedFont", typography.primary_font)
    sec = secondary or primary
    body.set("FillColor", f"Color/{sec.name}")

    # Italic sub-style
    italic = SubElement(psg, "ParagraphStyle")
    italic.set("Self", "ParagraphStyle/BodyItalic")
    italic.set("Name", "BodyItalic")
    italic.set("FontStyle", "Italic")
    italic.set("PointSize", str(typography.body_size_pt))
    italic.set("Justification", "CenterAlign")
    if typography.secondary_font or typography.primary_font:
        italic.set("AppliedFont", typography.secondary_font or typography.primary_font)
    italic.set("FillColor", f"Color/{sec.name}")

    # Root character style group
    csg = SubElement(root, "RootCharacterStyleGroup")
    csg.set("Self", "u14g")

    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + tostring(root, encoding="unicode").encode("utf-8")


def _story_xml(
    story_id: str,
    text_lines: list[tuple[str, str]],  # (text, paragraph_style_name)
) -> bytes:
    root = Element("idPkg:Story")
    root.set("xmlns:idPkg", "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging")
    root.set("DOMVersion", "18.0")

    story = SubElement(root, "Story")
    story.set("Self", f"Story_{story_id}")
    story.set("AppliedTOCStyle", "n")
    story.set("TrackChanges", "false")
    story.set("StoryTitle", story_id)

    for text, style in text_lines:
        pr = SubElement(story, "ParagraphStyleRange")
        pr.set("AppliedParagraphStyle", f"ParagraphStyle/{style}")
        cr = SubElement(pr, "CharacterStyleRange")
        cr.set("AppliedCharacterStyle", "CharacterStyle/$ID/[No character style]")
        content = SubElement(cr, "Content")
        content.text = text
        # Line break between paragraphs
        br_cr = SubElement(pr, "CharacterStyleRange")
        br_cr.set("AppliedCharacterStyle", "CharacterStyle/$ID/[No character style]")
        br_content = SubElement(br_cr, "Br")

    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + tostring(root, encoding="unicode").encode("utf-8")


def _spread_xml(
    spread_id: str,
    card: CardSpec,
    story_ids: list[str],
    frame_specs: list[dict],
) -> bytes:
    """Generate a Spread XML with text frames positioned on the card.

    frame_specs: list of {story_id, x, y, w, h} dicts (all in points,
    origin at top-left of card including bleed).
    """
    root = Element("idPkg:Spread")
    root.set("xmlns:idPkg", "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging")
    root.set("DOMVersion", "18.0")

    spread = SubElement(root, "Spread")
    spread.set("Self", f"Spread_{spread_id}")
    spread.set("FlattenerOverride", "Default")
    spread.set("ShowMasterItems", "true")

    # Page
    page = SubElement(spread, "Page")
    page.set("Self", f"Page_{spread_id}")
    page.set("AppliedMaster", "n")
    page.set("Name", spread_id)
    # Geometric bounds: top, left, bottom, right (in points, relative to spread origin)
    page.set("GeometricBounds", f"0 0 {_pt_to_idml(card.total_height_pt)} {_pt_to_idml(card.total_width_pt)}")

    # Margin preferences
    mp = SubElement(page, "MarginPreference")
    safe = card.safe_margin_inches * 72
    mp.set("Top", _pt_to_idml(card.bleed_pt + safe))
    mp.set("Bottom", _pt_to_idml(card.bleed_pt + safe))
    mp.set("Left", _pt_to_idml(card.bleed_pt + safe))
    mp.set("Right", _pt_to_idml(card.bleed_pt + safe))

    # Text frames
    for fs in frame_specs:
        tf = SubElement(spread, "TextFrame")
        tf.set("Self", f"TextFrame_{fs['story_id']}")
        tf.set("ParentStory", f"Story_{fs['story_id']}")
        tf.set("ContentType", "TextType")

        # Geometric bounds: top, left, bottom, right
        top = fs["y"]
        left = fs["x"]
        bottom = fs["y"] + fs["h"]
        right = fs["x"] + fs["w"]
        tf.set("GeometricBounds", f"{_pt_to_idml(top)} {_pt_to_idml(left)} {_pt_to_idml(bottom)} {_pt_to_idml(right)}")

        # Text frame preferences
        tfp = SubElement(tf, "TextFramePreference")
        tfp.set("VerticalJustification", "TopAlign")
        tfp.set("AutoSizingReferencePoint", "TopCenterPoint")

    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + tostring(root, encoding="unicode").encode("utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_card_idml(
    content: CardContent,
    card: CardSpec | None = None,
    primary_color: CmykColor | None = None,
    secondary_color: CmykColor | None = None,
    typography: TypographySpec | None = None,
    side: str = "front",
    output_dir: str = ".",
    name: str = "card",
    verbose: bool = True,
) -> str | None:
    """Export a single card face as an .idml file.

    Returns path to the .idml file, or None on failure.
    """
    card = card or CardSpec()
    primary_color = primary_color or CmykColor()
    typography = typography or TypographySpec()

    spread_id = _uid()
    stories: list[tuple[str, list[tuple[str, str]], dict]] = []

    bleed = card.bleed_pt
    safe = card.safe_margin_inches * 72
    cx = card.total_width_pt / 2
    text_width = card.width_pt - 2 * safe

    if side == "front":
        y_cursor = bleed + safe + 12
        if content.name:
            sid = _uid()
            stories.append((sid, [(content.name, "Heading")], {
                "story_id": sid,
                "x": bleed + safe,
                "y": y_cursor,
                "w": text_width,
                "h": typography.heading_size_pt + 8,
            }))
            y_cursor += typography.heading_size_pt + 14

        if content.title:
            sid = _uid()
            stories.append((sid, [(content.title, "BodyItalic")], {
                "story_id": sid,
                "x": bleed + safe,
                "y": y_cursor,
                "w": text_width,
                "h": typography.body_size_pt + 6,
            }))
            y_cursor += typography.body_size_pt + 10

        if content.organization:
            sid = _uid()
            stories.append((sid, [(content.organization, "Body")], {
                "story_id": sid,
                "x": bleed + safe,
                "y": y_cursor,
                "w": text_width,
                "h": typography.body_size_pt + 6,
            }))
    else:
        y_cursor = bleed + safe + 10
        for line in content.contact_lines:
            sid = _uid()
            stories.append((sid, [(line, "Body")], {
                "story_id": sid,
                "x": bleed + safe,
                "y": y_cursor,
                "w": text_width,
                "h": typography.body_size_pt + 4,
            }))
            y_cursor += typography.body_size_pt + 6

        if content.tagline:
            sid = _uid()
            y_tag = card.total_height_pt - bleed - safe - typography.body_size_pt - 8
            stories.append((sid, [(content.tagline, "BodyItalic")], {
                "story_id": sid,
                "x": bleed + safe,
                "y": y_tag,
                "w": text_width,
                "h": 14,
            }))

    story_ids = [s[0] for s in stories]
    frame_specs = [s[2] for s in stories]

    os.makedirs(output_dir, exist_ok=True)
    idml_path = os.path.join(output_dir, f"{name}-{side}.idml")

    try:
        with zipfile.ZipFile(idml_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("mimetype", _mimetype())
            zf.writestr("META-INF/container.xml", _container_xml())
            zf.writestr("designmap.xml", _designmap_xml(story_ids, [spread_id]))
            zf.writestr("Resources/Fonts.xml", _fonts_xml(typography))
            zf.writestr("Resources/Graphic.xml", _graphic_xml(primary_color, secondary_color))
            zf.writestr("Resources/Styles.xml", _styles_xml(typography, primary_color, secondary_color))
            zf.writestr(f"Spreads/Spread_{spread_id}.xml", _spread_xml(spread_id, card, story_ids, frame_specs))

            for sid, lines, _ in stories:
                zf.writestr(f"Stories/Story_{sid}.xml", _story_xml(sid, lines))

        if verbose:
            console.print(f"[green]IDML: {idml_path}[/green]")
        return idml_path

    except Exception as e:
        if verbose:
            console.print(f"[red]IDML export failed: {e}[/red]")
        return None


def export_card_set_idml(
    cards: list[tuple[CardContent, str]],  # (content, side) pairs
    card: CardSpec | None = None,
    primary_color: CmykColor | None = None,
    secondary_color: CmykColor | None = None,
    typography: TypographySpec | None = None,
    output_dir: str = ".",
    name: str = "card-set",
    verbose: bool = True,
) -> list[str]:
    """Export multiple card faces as individual .idml files.

    Returns list of paths to generated .idml files.
    """
    results = []
    for i, (content, side) in enumerate(cards):
        card_name = f"{name}-{i + 1}" if len(cards) > 1 else name
        path = export_card_idml(
            content=content,
            card=card,
            primary_color=primary_color,
            secondary_color=secondary_color,
            typography=typography,
            side=side,
            output_dir=output_dir,
            name=card_name,
            verbose=verbose,
        )
        if path:
            results.append(path)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Export a calling card as IDML (InDesign Markup Language)"
    )
    parser.add_argument("--name", default="Sample Card", help="Cardholder name")
    parser.add_argument("--title", default="Designer", help="Title / role")
    parser.add_argument("--org", default="Studio", help="Organization")
    parser.add_argument("--contact", nargs="*", default=["hello@example.com"], help="Contact lines")
    parser.add_argument("--tagline", default="", help="Back tagline")
    parser.add_argument("--side", choices=["front", "back", "both"], default="both", help="Which side(s)")
    parser.add_argument("--output-dir", "-o", default=".", help="Output directory")
    parser.add_argument("--font", default="Helvetica", help="Primary font")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    content = CardContent(
        name=args.name,
        title=args.title,
        organization=args.org,
        contact_lines=args.contact,
        tagline=args.tagline,
    )
    typography = TypographySpec(primary_font=args.font)
    verbose = not args.quiet

    if args.side in ("front", "both"):
        export_card_idml(content, side="front", output_dir=args.output_dir, typography=typography, verbose=verbose)
    if args.side in ("back", "both"):
        export_card_idml(content, side="back", output_dir=args.output_dir, typography=typography, verbose=verbose)


if __name__ == "__main__":
    main()
