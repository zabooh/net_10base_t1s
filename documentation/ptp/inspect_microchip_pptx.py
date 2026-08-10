#!/usr/bin/env python3
"""Scan the Microchip PPTX files for ACMA / PTP / TSU related slides."""
import os
import re
from pptx import Presentation

REPO = r"c:\work\ptp\check4\net_10base_t1s"
TEMPLATES = [
    os.path.join(REPO, "documentation", "pdf",
                 "3_Special Features of LAN8670_1_2 LAN8650_1.pptx"),
    os.path.join(REPO, "documentation", "pdf",
                 "LAN867x_PHY_d47.pptx"),
]

KEYWORDS = re.compile(
    r"\b(ACMA|PTP|TSU|Time\s*Sync|Wall\s*Clock|"
    r"Pdelay|Sync(?:\s|hron|hroniz)|gPTP|"
    r"802\.1AS|1588|"
    r"Event\s*Generator|Event\s*Capture|1PPS|"
    r"Phase\s*Adjust|Timestamp|"
    r"TDMA)\b", re.IGNORECASE)


def slide_text(slide):
    parts = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            for p in shape.text_frame.paragraphs:
                for r in p.runs:
                    if r.text:
                        parts.append(r.text)
        if shape.shape_type == 19:  # table
            try:
                for row in shape.table.rows:
                    for cell in row.cells:
                        if cell.text_frame:
                            parts.append(cell.text)
            except Exception:
                pass
    return "\n".join(parts)


def count_images(slide):
    n = 0
    for shape in slide.shapes:
        if shape.shape_type == 13:  # picture
            n += 1
        elif shape.shape_type == 6:  # group — check inside
            for sub in shape.shapes:
                if sub.shape_type == 13:
                    n += 1
    return n


def shape_summary(slide):
    summary = []
    for shape in slide.shapes:
        st = shape.shape_type
        if st == 13:  # picture
            summary.append("PIC")
        elif st == 1:  # autoshape
            summary.append("autoShape")
        elif st == 6:  # group
            summary.append("GROUP")
        elif st == 17:  # text box
            summary.append("text")
        elif st == 19:  # table
            summary.append("TABLE")
        elif st == 14:  # placeholder
            pass
    return summary


for path in TEMPLATES:
    print("=" * 80)
    print(path)
    print("=" * 80)
    prs = Presentation(path)
    for idx, slide in enumerate(prs.slides, start=1):
        text = slide_text(slide)
        if not KEYWORDS.search(text):
            continue
        n_pics = count_images(slide)
        title = ""
        if slide.shapes.title is not None:
            title = slide.shapes.title.text_frame.text.strip().split("\n")[0]
        shapes = shape_summary(slide)
        keywords_found = sorted(set(m.group(0).upper() for m in KEYWORDS.finditer(text)))
        print(f"\n--- Slide {idx} ---")
        print(f"  Title: {title[:80]}")
        print(f"  Pictures: {n_pics}    Other shapes: {','.join(shapes) if shapes else '(none)'}")
        print(f"  Keywords: {keywords_found}")
        # Print first 10 lines of text content
        lines = [ln for ln in text.split("\n") if ln.strip()][:8]
        for ln in lines:
            print(f"    | {ln[:90]}")
