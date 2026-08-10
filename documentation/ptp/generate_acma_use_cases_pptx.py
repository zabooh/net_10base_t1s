#!/usr/bin/env python3
"""
Generate a PowerPoint deck about ACMA + PTP + 10BASE-T1S use cases,
based on the Microchip "Special Features" template (preserves
Microchip branding via the existing slide masters and layouts).

Output: documentation/pdf/acma_use_cases.pptx

Run from repo root or anywhere — paths are absolute.
"""

import copy
import os
import subprocess
import sys
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.enum.text import PP_ALIGN

REPO_ROOT = r"c:\work\ptp\check4\net_10base_t1s"
TEMPLATE = os.path.join(REPO_ROOT, "documentation", "pdf",
                        "3_Special Features of LAN8670_1_2 LAN8650_1.pptx")
OUTPUT = os.path.join(REPO_ROOT, "documentation", "pdf",
                      "acma_use_cases.pptx")
DIAGRAMS = os.path.join(REPO_ROOT, "documentation", "pdf",
                        "_extracted_diagrams")


# ---------------------------------------------------------------------------
# Layout indices (verified against template):
#   0  = Title Slide
#   1  = Section Header
#   2  = Title and Content
#   3  = Title + Subtitle and Content
#   4  = 2 Line Title and Content
#   5  = Two Content
#   6  = Two Content + Subtitle
#   7  = Comparison
#   8  = Comparison + Subtitle
#   9  = Title Only
#   10 = Title + Subtitle Only
#   11 = Blank
#   12 = left one third
#   13 = Fixed Agenda
LAYOUT_TITLE = 0
LAYOUT_SECTION = 1
LAYOUT_TITLE_CONTENT = 2
LAYOUT_TITLE_SUBTITLE_CONTENT = 3
LAYOUT_COMPARISON = 7
LAYOUT_TITLE_ONLY = 9
# ---------------------------------------------------------------------------


def remove_all_slides(prs):
    """Remove all existing slides while preserving slide masters and layouts."""
    sldIdLst = prs.slides._sldIdLst
    slide_ids = list(sldIdLst)
    for sId in slide_ids:
        rId = sId.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        prs.part.drop_rel(rId)
        sldIdLst.remove(sId)


def get_placeholder_by_idx(slide, idx):
    """Find placeholder by idx; returns None if not found."""
    for ph in slide.placeholders:
        if ph.placeholder_format.idx == idx:
            return ph
    return None


def set_title(slide, text):
    """Set the slide title text."""
    title_ph = slide.shapes.title
    if title_ph is not None:
        title_ph.text = text


def set_subtitle(slide, text):
    """Set subtitle on a Title Slide layout (placeholder idx 1)."""
    sub = get_placeholder_by_idx(slide, 1)
    if sub is not None:
        sub.text = text


def set_content_bullets(slide, bullets, content_idx=1):
    """Set content placeholder with a list of bullet strings.
    bullets can be a list of (level, text) tuples or just strings.
    """
    content = get_placeholder_by_idx(slide, content_idx)
    if content is None:
        # Try idx=2 as fallback (some layouts use that)
        content = get_placeholder_by_idx(slide, 2)
    if content is None or not content.has_text_frame:
        return
    tf = content.text_frame
    tf.clear()

    for i, item in enumerate(bullets):
        if isinstance(item, tuple):
            level, text = item
        else:
            level, text = 0, item
        if i == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        p.text = text
        p.level = level


def add_section_header(prs, title, subtitle=None, intro_bullets=None):
    """Add a section divider slide.

    intro_bullets: optional list of bullet strings (or (level, text) tuples)
    rendered in a textbox below the title/subtitle.
    """
    slide = prs.slides.add_slide(prs.slide_layouts[LAYOUT_SECTION])
    set_title(slide, title)
    if subtitle:
        sub = get_placeholder_by_idx(slide, 1)
        if sub is not None and sub.has_text_frame:
            sub.text = subtitle

    if intro_bullets:
        slide_w = prs.slide_width
        slide_h = prs.slide_height
        side_margin = Emu(800000)
        top = int(slide_h * 0.42)
        h = slide_h - top - Emu(700000)
        w = slide_w - 2 * side_margin
        box = slide.shapes.add_textbox(side_margin, top, w, h)
        tf = box.text_frame
        tf.word_wrap = True
        for i, b in enumerate(intro_bullets):
            if isinstance(b, tuple):
                level, text = b
            else:
                level, text = 0, b
            if i == 0:
                p = tf.paragraphs[0]
            else:
                p = tf.add_paragraph()
            p.text = text
            p.level = level
            for run in p.runs:
                run.font.size = Pt(15)
    return slide


def add_use_case(prs, title, scenario, bullets, footer=None):
    """Add a use-case slide with scenario subtitle and bulleted detail.

    bullets: list of strings (level 0) or (level, text) tuples for indent.
    footer: optional concluding bullet rendered at bullet-level 0.
    """
    slide = prs.slides.add_slide(prs.slide_layouts[LAYOUT_TITLE_CONTENT])
    set_title(slide, title)

    items = []
    if scenario:
        items.append((0, f"Szenario: {scenario}"))
    for b in bullets:
        if isinstance(b, tuple):
            items.append(b)
        else:
            items.append((0, b))
    if footer:
        items.append((0, footer))

    set_content_bullets(slide, items)
    return slide


def add_simple(prs, title, bullets):
    """Add a Title and Content slide with bullets only."""
    slide = prs.slides.add_slide(prs.slide_layouts[LAYOUT_TITLE_CONTENT])
    set_title(slide, title)
    set_content_bullets(slide, bullets)
    return slide


def add_image_slide(prs, title, image_basename, caption=None, bullets=None):
    """Add a Title Only slide with an image plus optional caption / bullets.

    Layout:
      - bullets is None         -> image fills full available width, centred
      - bullets is not None     -> image left ~58%, bullets right ~38%

    image_basename can have any extension; we look in DIAGRAMS for
    .png / .jpg / .jpeg / .wmf in that order.
    """
    slide = prs.slides.add_slide(prs.slide_layouts[LAYOUT_TITLE_ONLY])
    set_title(slide, title)

    image_path = None
    base, ext = os.path.splitext(image_basename)
    candidates = [image_basename] if ext else []
    for e in (".png", ".jpg", ".jpeg", ".wmf"):
        candidates.append(base + e)
    for c in candidates:
        p = os.path.join(DIAGRAMS, c)
        if os.path.exists(p):
            image_path = p
            break
    if image_path is None:
        print(f"  WARN: image not found for {image_basename}")
        return slide

    # Slide layout constants
    slide_w = prs.slide_width
    slide_h = prs.slide_height
    top_margin = Emu(1100000)
    bottom_margin = Emu(700000)
    side_margin = Emu(600000)
    avail_h = slide_h - top_margin - bottom_margin

    if bullets:
        # ---- Two-column: image left, bullets right ----
        col_gap = Emu(400000)
        total_w = slide_w - 2 * side_margin - col_gap
        col_left_w = int(total_w * 0.60)
        col_right_w = total_w - col_left_w
        cap_h = Emu(600000) if caption else Emu(0)
        img_area_h = avail_h - cap_h

        pic = slide.shapes.add_picture(image_path,
                                        side_margin, top_margin,
                                        width=col_left_w)
        # Scale down if too tall
        if pic.height > img_area_h:
            scale = img_area_h / pic.height
            pic.width = int(pic.width * scale)
            pic.height = img_area_h
            pic.left = side_margin + (col_left_w - pic.width) // 2
        else:
            # Vertically centre in image area
            pic.top = top_margin + (img_area_h - pic.height) // 2
            # Horizontally centre in left column
            pic.left = side_margin + (col_left_w - pic.width) // 2

        # Caption below image (in left column)
        if caption:
            cap_top = pic.top + pic.height + Emu(100000)
            cap_box = slide.shapes.add_textbox(side_margin, cap_top,
                                                col_left_w, cap_h)
            tf = cap_box.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = caption
            p.alignment = PP_ALIGN.CENTER
            for run in p.runs:
                run.font.size = Pt(10)
                run.font.italic = True

        # Bullet text-box in right column
        bullet_left = side_margin + col_left_w + col_gap
        bullet_box = slide.shapes.add_textbox(bullet_left, top_margin,
                                                col_right_w, avail_h)
        tf = bullet_box.text_frame
        tf.word_wrap = True
        for i, b in enumerate(bullets):
            if isinstance(b, tuple):
                level, text = b
            else:
                level, text = 0, b
            if i == 0:
                p = tf.paragraphs[0]
            else:
                p = tf.add_paragraph()
            p.text = text
            p.level = level
            for run in p.runs:
                run.font.size = Pt(14)
    else:
        # ---- Single-column: full width, centred ----
        avail_w = slide_w - 2 * side_margin
        pic = slide.shapes.add_picture(image_path,
                                        side_margin, top_margin,
                                        width=avail_w)
        if pic.height > avail_h:
            scale = avail_h / pic.height
            pic.width = int(pic.width * scale)
            pic.height = avail_h
            pic.left = (slide_w - pic.width) // 2
        else:
            pic.top = top_margin + (avail_h - pic.height) // 2

        if caption:
            cap_top = pic.top + pic.height + Emu(100000)
            cap_h = Emu(500000)
            cap_box = slide.shapes.add_textbox(side_margin, cap_top,
                                                avail_w, cap_h)
            tf = cap_box.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = caption
            p.alignment = PP_ALIGN.CENTER
            for run in p.runs:
                run.font.size = Pt(11)
                run.font.italic = True

    return slide


# ---------------------------------------------------------------------------
# Build the deck
# ---------------------------------------------------------------------------
def main():
    prs = Presentation(TEMPLATE)
    remove_all_slides(prs)

    # ---- 1. Title Slide ----
    slide = prs.slides.add_slide(prs.slide_layouts[LAYOUT_TITLE])
    set_title(slide, "ACMA + PTP + 10BASE-T1S")
    set_subtitle(slide, "Anwendungsfälle für deterministischen TDMA-Bus-Zugriff")

    # ---- 2. Agenda ----
    add_simple(prs, "Agenda", [
        "Was ACMA + PTP an Hardware-Garantien liefert",
        "Hardware-Grundlagen (Microchip-Diagramme)",
        "Industrielle Steuerung",
        "Sensor-Cluster und Datenerfassung",
        "Audio / Video / Multimedia",
        "Automotive",
        "Avionik und Sicherheit",
        "Test- und Mess-Anwendungen",
        "Spezielle Anwendungsfälle",
        "Anwendungs-Übersicht",
        "Grenzen und Markt-Strategie",
    ])

    # ---- 3. Hardware-Garantien ----
    add_simple(prs, "Drei Hardware-Garantien", [
        "Determinismus — jeder Knoten kennt seinen Sende-Slot auf Nanosekunden genau",
        (1, "ACMA-Gate vor TXEN — gesteuert von EG0"),
        (1, "EG0 vergleicht Wall Clock mit programmierter Slot-Zeit"),
        (1, "Keine Backoff-Logik, keine Slot-Konkurrenz"),
        "Garantierte Latenz — Worst-Case = ein Bus-Zyklus",
        (1, "Typisch 1-10 ms je nach Slot-Konfiguration"),
        (1, "Bandbreite linear teilbar zwischen N Knoten"),
        (1, "Worst-Case = (N × Slot-Breite) + Guard-Bands"),
        "Sub-µs synchrone Wall Clock auf allen Knoten",
        (1, "PTP-Sync hält die 94-bit Wall Clock auf Master-Wert"),
        (1, "Microchip-Messung (AN1847 §4): 100 ns p-p, σ = 25 ns"),
        (1, "Pattern Matcher timestempelt am End-of-SFD im PHY"),
        (1, "→ unabhängig von PLCA-Slot-Wartezeit oder Bus-Last"),
    ])

    # ---- NEW SECTION: Hardware-Grundlagen (Microchip-Diagramme) ----
    add_section_header(prs, "Hardware-Grundlagen",
        "Wie ACMA, PTP und TSU im LAN86xx implementiert sind",
        intro_bullets=[
            "Was kommt jetzt:",
            (1, "Microchip's eigene Architektur-Diagramme aus den"),
            (1, "Special-Features und LAN867x-PHY-Slide-Decks"),
            "Themen:",
            (1, "PTP-Standards & Use Cases im Microchip-Verständnis"),
            (1, "IEEE-802.1AS-Support in beiden Chip-Familien"),
            (1, "ACMA-Block-Aufbau und Konfigurations-Beispiel"),
            (1, "Pattern-Matcher und RX-Timestamp-Pfad"),
            (1, "Two-Step gPTP-Frame-Sequenz auf LAN8670/1/2"),
        ])

    add_image_slide(prs,
        "PTP Use Cases & Standards (Microchip Übersicht)",
        "phy_34_usecases_standards.jpg",
        caption="Quelle: Microchip LAN867x PHY Deck",
        bullets=[
            "Use Cases für Zeit-Synchronisation:",
            (1, "Sensor-Daten brauchen Timestamping für Fusion"),
            (1, "Motor- und Bremsensteuerung: Maschinen-Sync"),
            (1, "Audio / Beleuchtung: synchrone Wiedergabe"),
            "Standards:",
            (1, "IEEE 1588 (PTPv2) — fundamentales Protokoll"),
            (1, "IEEE 802.1AS (gPTP) — TSN-Profil"),
            (1, "Strenge Anforderungen für TSN-Anwendungen"),
        ])

    add_image_slide(prs,
        "IEEE 802.1AS Support — LAN8670/1/2 und LAN8650/1",
        "phy_35_8021as_support.png",
        caption="Quelle: Microchip LAN867x PHY Deck",
        bullets=[
            "Beide Familien unterstützen IEEE 802.1AS:",
            (1, "LAN8670/1/2 — separate PHY"),
            (1, "LAN8650/1 — integrierter MAC-PHY"),
            "Identische TSU-Hardware:",
            (1, "94-bit Wall Clock (48s + 30ns + 16 sub-ns)"),
            (1, "Pattern Matcher am End-of-SFD"),
            (1, "4× Event Capture / Generator + 1PPS"),
            "Unterschied nur in der Architektur:",
            (1, "MAC integriert (LAN8650/1)"),
            (1, "MAC im SoC (LAN8670/1/2)"),
        ])

    add_image_slide(prs,
        "LAN8650/1 ACMA — Block-Diagramm",
        "ms_28_lan8650_acma_block.png",
        caption="Quelle: Microchip Special-Features Deck",
        bullets=[
            "ACMA = Application Controlled Media Access",
            "Externe Source-Optionen:",
            (1, "GPIO-Pin DIO2"),
            "Interne Source-Option:",
            (1, "Event Generator 0 (EG0)"),
            (1, "Wall-Clock-getrieben → PTP-synchron"),
            "Funktion:",
            (1, "ACMA-Signal gated den MAC-TXEN direkt"),
            (1, "HIGH = senden erlaubt, LOW = gesperrt"),
            "Aktivierung:",
            (1, "ACMAEN-Bit in ACMACTL-Register"),
            (1, "Polarität: ACMAPOL-Bit"),
        ])

    add_image_slide(prs,
        "ACMA-Konfigurations-Beispiel (LAN8670/1/2)",
        "phy_27_acma_config_example.png",
        caption="Quelle: Microchip LAN867x PHY Deck",
        bullets=[
            "Verfügbare Pins auf LAN8670 in RMII-Mode",
            "Pin-Configuration definiert ACMA-Quelle:",
            (1, "Externer GPIO oder interner EG0"),
            "Software konfiguriert ACMA nach Reset:",
            (1, "ACMAEN = 1 in ACMACTL"),
            (1, "ACMAPOL = 1 in PINCTRL"),
            (1, "Source-Selektion in Pad-Control"),
            "Application monitort den Bus:",
            (1, "Detektion von Slot-Verletzungen"),
            (1, "Fehler-Reaktions-Logik in Software",),
            "Custom Scheduled Access für TDMA",
        ])

    add_image_slide(prs,
        "LAN8650/1 RX-Timestamp-Konfiguration",
        "ms_37_rx_timestamp_config.png",
        caption="Quelle: Microchip Special-Features Deck",
        bullets=[
            "Pattern Matcher triggert Timestamp:",
            (1, "Frame-Pattern wird verglichen"),
            (1, "Trigger am End-of-SFD"),
            "Register-Sequenz:",
            (1, "Erste 3 Writes: Pattern-Match-Konfig"),
            (1, "Letzter Write: Trigger-Signal aktivieren"),
            "Standard-Konfiguration:",
            (1, "Trifft auf alle PTP-Frames"),
            (1, "Über FTSE-Bit in OA_CONFIG0"),
            "Timestamp-Punkt nach Elastic Buffer:",
            (1, "→ unabhängig von PLCA-Slot-Wartezeit"),
            (1, "→ deterministisch trotz Bus-Last"),
        ])

    add_image_slide(prs,
        "IEEE 802.1AS — Clock-Sync-Config LAN8670/1/2",
        "ms_43_lan867x_8021as_config.png",
        caption="Quelle: Microchip Special-Features Deck",
        bullets=[
            "Two-Step-Approach (IEEE-Standard):",
            (1, "Sync-Frame ohne Timestamp"),
            (1, "Follow_up trägt den Sync-Timestamp"),
            "Vorteil Two-Step:",
            (1, "Präzises Hardware-Latching"),
            (1, "Kein Echtzeit-Druck auf Software"),
            "Konfiguration analog LAN8650/1:",
            (1, "Aber über SMI/MDIO statt SPI"),
            (1, "TSU-Block ist eigenständig (kein MAC)"),
            "Cross-Familie identisches Protokoll:",
            (1, "Software-API kann unabhängig sein"),
        ])

    # ---- 4. Section: Industrielle Steuerung ----
    add_section_header(prs, "Industrielle Steuerung",
        "Motion Control · Multi-Achs-Sampling · I/O Triggering",
        intro_bullets=[
            "Industrielle Anwendungen brauchen:",
            (1, "Sub-Millisekunden-Latenz für Steuerschleifen"),
            (1, "Synchron-Sampling über N verteilte Achsen"),
            (1, "Garantierte Reaktion innerhalb fixer Deadline"),
            "Konkurrenz-Technologien:",
            (1, "EtherCAT, PROFINET-IRT, Sercos III auf 100 Mbit/s"),
            (1, "ACMA + T1S = Low-Cost-Alternative für 10 Mbit/s"),
            "Wirtschafts-Argument:",
            (1, "T1S-Verkabelung ist deutlich günstiger"),
            (1, "Für mittlere Bandbreite-Anforderungen ausreichend"),
        ])

    add_use_case(prs,
        "Verteilte SPS / Motion Control",
        "Master-SPS koordiniert Motor-Drives, Sensoren, Aktoren auf einem T1S-Bus",
        [
            "Bus-Aufbau:",
            (1, "Master-SPS = ACMA-Coordinator, Slot 0"),
            (1, "Bis zu 7 Drive/Sensor-Knoten in eigenen Slots"),
            (1, "Typisch 1 ms Zyklus, 100 µs Slot pro Knoten"),
            "Daten-Fluss:",
            (1, "Slot 0: Master → all → Soll-Werte (Broadcast)"),
            (1, "Slot 1..7: Drive_i → Master → Ist-Werte"),
            "Latenz-Garantie:",
            (1, "Worst-Case Reaktion < 1 ms statt mehreren ms"),
            (1, "Deterministisch, keine Konkurrenz-Verluste"),
            "Synchrones Encoder-Sampling zur selben PTP-Zeit",
            "Anwendungen: Kran-Steuerung, Förderband-Aktorik,",
            "Verpackungsmaschinen, Roboter-Achsen-Koordination",
        ],
        footer="Konkurrenz EtherCAT/PROFINET-IRT/Sercos = 100 Mbit/s — hier 10 Mbit/s reichen, T1S ist Low-Cost-Variante")

    add_use_case(prs,
        "Synchrones Multi-Achs-Sampling",
        "8 Encoder-Knoten sampeln Position zur selben Wall-Clock-Zeit",
        [
            "Trigger-Verteilung:",
            (1, "Master setzt Trigger-Zeit T = nächste Sekunde + offset"),
            (1, "Trigger-Frame mit Wert von T an alle Knoten"),
            "Lokale Hardware-Reaktion:",
            (1, "Jeder Knoten programmiert EG1 als Single-Shot zu T"),
            (1, "EG1 löst lokalen ADC-Sample-Trigger aus"),
            (1, "Daten-Übertragung in eigenem ACMA-Slot"),
            "Synchronitäts-Genauigkeit:",
            (1, "< 100 ns zwischen Achsen, limitiert durch PTP"),
            (1, "Alle ADCs sampeln zur selben absoluten Zeit"),
            "Anwendungen:",
            (1, "Robotik mit gekoppelter Kinematik (Mehr-Achs)"),
            (1, "Werkzeugmaschinen mit Mehr-Achs-Interpolation"),
            (1, "Form-Mess-Systeme, 3D-Scanner"),
            (1, "Trajektorien-Verfolgung in Bewegungs-Steuerungen"),
        ])

    add_use_case(prs,
        "Coordinated I/O Triggering",
        "Mehrere Aktoren feuern gleichzeitig — Schwingungs-Anregung, Stempel-Pressen, Sprüh-Düsen",
        [
            "Trigger-Verteilung wie Multi-Achs-Sampling:",
            (1, "Master sendet Sync: 'Trigger zur Zeit T'"),
            (1, "Alle Knoten programmieren EG2 auf T"),
            (1, "Alle Aktoren feuern simultan, sub-µs synchron"),
            "Verkabelungs-Vorteil:",
            (1, "Eine T1S-Bus-Leitung statt Stern-Trigger-Bus"),
            (1, "Reduzierte Verdrahtungs-Komplexität"),
            (1, "Niedrigere BOM-Kosten, schnelleres Debugging"),
            "Konkrete Anwendungen:",
            (1, "Schwingungs-Anregung (Modal-Analyse)"),
            (1, "Multi-Stempel-Pressen für Gleichzeitigkeit"),
            (1, "Sprüh-Düsen-Bahnen in Lackier-Anlagen"),
            (1, "Sicherheits-Abschaltungen in mehreren Ebenen"),
            (1, "Synchron-Schalter in Energie-Verteilung"),
        ])

    # ---- 5. Section: Sensor-Cluster ----
    add_section_header(prs, "Sensor-Cluster und Datenerfassung",
        "ADC-Arrays · Beamforming · LiDAR-Cluster",
        intro_bullets=[
            "Sensor-Cluster nutzen drei ACMA-Eigenschaften:",
            (1, "Synchrones Sampling über alle Kanäle (PTP-getrieben)"),
            (1, "Garantierte Sende-Slots pro Sensor (kein Verlust)"),
            (1, "Konstanter Phasen-Bezug zwischen Kanälen"),
            "Typische Konfiguration:",
            (1, "1 Master-Knoten als Daten-Aggregator"),
            (1, "N = 4 bis 8 Sensor-Knoten am gleichen Bus"),
            (1, "Sample-Raten von 1 kHz bis ~16 kHz typisch"),
        ])

    add_use_case(prs,
        "Verteilte ADC-Arrays",
        "8 ADC-Knoten messen synchron Strom/Spannung an verschiedenen Punkten",
        [
            "Mess-Setup:",
            (1, "Sample-Rate typisch 1 kHz (1 ms ACMA-Zyklus)"),
            (1, "Auflösung 12-24 bit pro Wandler"),
            (1, "Multi-Channel-Wellenform-Rekonstruktion am Master"),
            "Daten-Volumen pro Sensor:",
            (1, "16 bit × 1 kHz = 16 Kbit/s pro Knoten"),
            (1, "8 Knoten × 16 Kbit/s = 128 Kbit/s — passt locker"),
            "Zeitliche Korrelation:",
            (1, "Konstanter Phasen-Bezug zwischen Kanälen"),
            (1, "Sub-µs Sample-Synchronität via PTP-getrieben EG1"),
            "Anwendungen:",
            (1, "Smart-Grid Power-Monitoring (Drei-Phasen-Strom/Spannung)"),
            (1, "Vibrations-Diagnose (Lager, Getriebe, Maschinen)"),
            (1, "Strukturüberwachung (Brücken, Windkraft, Kräne)"),
            (1, "Prozess-Monitoring in Chemie / Pharma"),
        ])

    add_use_case(prs,
        "Akustische Sensor-Arrays / Beamforming",
        "Räumlich verteilte Mikrofone für Quellenortung oder Spracherkennung",
        [
            "Mikrofon-Setup:",
            (1, "4-8 Mikrofone in räumlicher Anordnung"),
            (1, "Sample-Rate typisch 16 kHz oder 48 kHz"),
            (1, "16 bit × 16 kHz × 8 = 2 Mbit/s — knapp aber machbar"),
            "Beamforming-Anforderung:",
            (1, "Phasen-Bezug zwischen Kanälen muss < 1 µs sein"),
            (1, "ACMA garantiert deterministische Datenübertragung"),
            (1, "PTP garantiert Sample-Synchronität < 100 ns"),
            "Algorithmen:",
            (1, "Delay-and-Sum für Quellenortung"),
            (1, "MVDR / GSC für interferenz-robuste Schätzung"),
            "Anwendungen:",
            (1, "Industrie-Akustik — Maschinen-Zustands-Überwachung"),
            (1, "Sicherheits-Systeme — Schuss-Detektion und -Lokalisation"),
            (1, "Intelligente Räume — Spracherkennung mit Sprecher-Tracking"),
            (1, "Wildlife-Monitoring — Vogelgesangs-Identifikation"),
        ])

    add_use_case(prs,
        "LiDAR / ToF-Sensor-Cluster",
        "Mehrere ToF-Distanz-Sensoren synchron, ohne Cross-Talk-Interferenz",
        [
            "Cross-Talk-Problem ohne ACMA:",
            (1, "Mehrere LiDAR-Sensoren senden Lichtpulse"),
            (1, "Pulse anderer Sensoren reflektieren in eigenen Empfänger"),
            (1, "Falsche Distanz-Werte oder gar falsche Objekt-Detektion"),
            "ACMA-Lösung:",
            (1, "ACMA-Slot pro Sensor = exklusiver Sende-Zeitschlitz"),
            (1, "Während Slot N misst nur Sensor N — alle anderen still"),
            (1, "Lichtpuls-Sequenzen sind hardware-getrennt"),
            "Anwendungen:",
            (1, "Roboter mit mehreren ToF-Modulen für 360° Sicht"),
            (1, "Industrielle Sicherheits-Lichtschranken-Arrays"),
            (1, "Automatisierte Lager-Logistik (FTS / AGV-Sensoren)"),
            (1, "Volumetric-Scanning für Verpackungs-Maschinen"),
            "Bandbreiten-Bedarf: gering (Distanz-Werte sind kompakt)",
        ])

    # ---- 6. Section: Audio/Video ----
    add_section_header(prs, "Audio / Video / Multimedia",
        "Distributed Audio · Synchronized Lighting",
        intro_bullets=[
            "Multimedia-Anwendungen brauchen:",
            (1, "Phasen-konstante Wiedergabe / Aufnahme"),
            (1, "Niedrige End-to-End-Latenz unter ~10 ms"),
            (1, "Skalierbarkeit auf viele räumlich verteilte Endpunkte"),
            "Konkurrenz-Standards:",
            (1, "AVB / IEEE 802.1BA auf 100 Mbit/s — etabliert"),
            (1, "TSN auf 1 Gbit/s+ — Premium-Markt"),
            (1, "ACMA + T1S = Low-Cost-Alternative für Sub-Premium"),
        ])

    add_use_case(prs,
        "Distributed Audio (AVB-Light)",
        "Mehrere Lautsprecher oder Mikrofone synchron auf einem T1S-Bus",
        [
            "Audio-Daten-Bandbreite:",
            (1, "48 kHz × 16-bit Stereo = 1.5 Mbit/s pro Stream"),
            (1, "Passt in einen ACMA-Slot von ~200 µs bei 1 ms Zyklus"),
            (1, "Mehrere Streams parallel via Time-Multiplex möglich"),
            "Synchronitäts-Anforderungen:",
            (1, "Wiedergabe-Phasen-Differenz zwischen Speakers < 5 µs"),
            (1, "PTP-Sync via 1PPS-Referenz auf allen Endpunkten"),
            (1, "Konstante Latenz garantiert (ACMA = kein Jitter)"),
            "Vorteile gegenüber AVB / TSN:",
            (1, "Kabel-Kosten deutlich niedriger (T1S vs CAT-Kabel)"),
            (1, "Wenige passive Komponenten dank PoDL-Möglichkeit"),
            (1, "MAC-PHY-Hardware deutlich günstiger"),
            "Anwendungen:",
            (1, "Gebäude-Beschallung mit zonen-synchronen Effekten"),
            (1, "Konferenz-Räume mit Multi-Mikrofon-Anordnung"),
            (1, "Auto-Audio-Systeme (Surround-Sound, Active-Noise)"),
            (1, "Bühnen-Beschallung mit präziser Phasen-Korrelation"),
        ])

    add_use_case(prs,
        "Synchronized Lighting",
        "LED-Strips, Bühnenbeleuchtung, Architektur-Beleuchtung mit präzisen Effekten",
        [
            "Lighting-Steuerung-Anforderung:",
            (1, "Effekt-Wechsel über N Leuchten gleichzeitig"),
            (1, "Synchronitäts-Anspruch: < 1 ms (Mensch wahrnehmbar)"),
            (1, "Mit PTP / EG → < 1 µs (Hardware-perfekt)"),
            "Daten-Fluss:",
            (1, "Master sendet Effekt-Sequenz mit Trigger-Zeitstempel T"),
            (1, "Alle LED-Knoten programmieren ihren EG2 auf T"),
            (1, "Effekt-Wechsel zur exakt gleichen Wall-Clock-Zeit"),
            "Anwendungen:",
            (1, "Bühnenbeleuchtung mit synchronen Farb-Wechseln"),
            (1, "Architektur-Beleuchtung mit Wellen-Effekten"),
            (1, "Werbung / Signage mit zeitlich kohärenten Animationen"),
            (1, "Industrielle Statusanzeigen mit exakter Synchronizität"),
            "Vorteil gegenüber DMX-512:",
            (1, "Höhere Datenrate, niedrigere Latenz, Determinismus garantiert"),
            (1, "Bidirektionalität für Status-Rückmeldung möglich"),
        ])

    # ---- 7. Section: Automotive ----
    add_section_header(prs, "Automotive",
        "Sensor-Backbone · ADAS-Sync · In-Vehicle-Network",
        intro_bullets=[
            "Automotive ist Microchip's primärer Markt für ACMA + T1S:",
            (1, "AEC-Q100 + ISO-26262 Qualifikation aller LAN86xx"),
            (1, "Marketing-Push fokussiert auf Fahrzeug-Sensorik"),
            "Anforderungen:",
            (1, "Lange Lifecycle-Zeit (15+ Jahre Lieferbarkeit)"),
            (1, "Funktional-sichere Designs (ASIL-B / ASIL-D)"),
            (1, "Hohe Temperatur-Resistenz (-40 bis +125 °C)"),
            (1, "Verkabelungs-Reduktion → Gewicht / Kosten"),
            "Konkurrenz: CAN-FD (1-8 Mbit/s) und LIN (max 20 Kbit/s)",
        ])

    add_use_case(prs,
        "Sensor-Backbone",
        "Verteilte Fahrzeug-Sensorik — Park-Sensoren, TPMS, Temperatur, Verbraucher-Steuerung",
        [
            "Bus-Topologie:",
            (1, "1 Master-MCU als Sensor-Aggregator"),
            (1, "Bis zu 8 Sensor-Knoten am gleichen Bus"),
            (1, "Mixing-Segment-Länge bis 25 m (Standard)"),
            "Synchron-Vorteile:",
            (1, "Park-Sensor-Daten + Lenkwinkel + Geschwindigkeit zur selben Zeit"),
            (1, "Sensor-Fusion auf Master ohne zeitliche Korrektur"),
            (1, "Dynamische Manöver mit synchronen Sensor-Reads"),
            "Bandbreiten-Vergleich:",
            (1, "CAN-FD: 1-8 Mbit/s gemeinsam für alle Knoten"),
            (1, "T1S: 10 Mbit/s gemeinsam, aber deterministisch verteilt"),
            (1, "LIN: 20 Kbit/s — viel zu wenig für moderne Sensorik"),
            "Konkrete Anwendungen:",
            (1, "Park-Sensor-Cluster (4-8 Ultraschall-Sensoren)"),
            (1, "TPMS — Reifendrucks-Übertragung von 4 Rädern"),
            (1, "Verbraucher-Steuerung (Sitze, Spiegel, Dachfenster)"),
            (1, "Temperatur-Monitoring im Fahrgastraum / Motor / Akku"),
        ])

    add_use_case(prs,
        "ADAS-Sensor-Synchronisation",
        "Kameras, Radar, LiDAR, Ultraschall müssen frame-synchron sein für Sensorfusion",
        [
            "Bandbreiten-Problem:",
            (1, "T1S 10 Mbit/s reicht NICHT für Sensor-Daten selbst"),
            (1, "Kameras: 100 Mbit/s+ pro Stream"),
            (1, "LiDAR: ähnlich — geht über separate höher-bandbreitige Medien"),
            "Aber: Trigger-Verteilung passt perfekt:",
            (1, "Capture-Trigger zur exakt gleichen PTP-Zeit"),
            (1, "Frame-Synchronität zwischen Kamera, Radar, LiDAR, US"),
            (1, "Sensorfusion ohne aufwändige zeitliche Korrektur"),
            "Architektur:",
            (1, "T1S-Bus für Trigger und Status (10 Mbit/s reicht)"),
            (1, "Daten-Bus separat (Ethernet 100 Mbit/s+ oder MIPI)"),
            "ASIL-Relevanz:",
            (1, "Sensor-Fusion ist Sicherheits-relevant für Notbrems-Assistenz"),
            (1, "Synchron-Triggerung ist Voraussetzung für ASIL-konforme Fusion"),
            "Konkrete Anwendungen:",
            (1, "Front-Kameras + Front-Radar synchron für ACC"),
            (1, "Surround-View 4× Kamera für Park-Manöver"),
            (1, "LiDAR + Radar Fusion für Highway-Pilot"),
        ])

    add_use_case(prs,
        "In-Vehicle-Network-Management",
        "Verteilte ECUs mit zeitlich kontrollierten Steuerungs-Frames",
        [
            "Klassisches Problem mit CAN-FD:",
            (1, "Best-Effort-Latenz, kann unter Last hochgehen"),
            (1, "Prioritäts-basiert, niedrig-prio kann verhungern"),
            (1, "Kein deterministisches Time-Slicing"),
            "ACMA-Lösung:",
            (1, "Jeder ECU bekommt eigenen Slot — keine Verhungerung"),
            (1, "Worst-Case-Latenz garantiert pro ECU"),
            (1, "Mit T1S 10 Mbit/s deutlich höhere Bandbreite als CAN"),
            "Konkrete ECU-Klassen:",
            (1, "Battery-Management — Zellen-Status alle 1 ms"),
            (1, "Bremsen-Steuerung — Sicherheits-relevante Steuerung"),
            (1, "Klimaanlage — komplexe Multi-Zonen-Regelung"),
            (1, "Beleuchtungs-Manager — synchrone Effekte (siehe oben)"),
            "Migrations-Pfad:",
            (1, "T1S kann CAN-FD ergänzen oder schrittweise ersetzen"),
            (1, "PoDL-fähig → Strom + Daten in einer Leitung"),
        ])

    # ---- 8. Section: Avionik & Sicherheit ----
    add_section_header(prs, "Avionik und Sicherheit",
        "Redundante Bus-Systeme · Verteilte Fehlertoleranz",
        intro_bullets=[
            "Sicherheits-kritische Anwendungen verlangen:",
            (1, "Deterministisches Verhalten unter ALLEN Bedingungen"),
            (1, "Fehler-Detektion innerhalb fixer Reaktionszeit"),
            (1, "Redundanz auf Bus- und Knoten-Ebene"),
            (1, "Zertifizierbarkeit (ISO 26262, ARP4761, IEC 61508)"),
            "Microchip Safety-Package:",
            (1, "FMEDA-Daten für Failure-Rate-Berechnung"),
            (1, "Functional Safety Manual mit ASIL-D-Eignung"),
            (1, "Built-in BIST und ECC-Mechanismen"),
        ])

    add_use_case(prs,
        "Redundante Steuerungs-Bus-Systeme",
        "Flugsteuerung oder Sicherheits-kritische Anwendungen mit deterministischen Anforderungen",
        [
            "ACMA als Determinismus-Lieferant:",
            (1, "Jeder Knoten sendet in seinem garantierten Slot"),
            (1, "Slot-Status = Knoten-Liveness-Indikator"),
            (1, "Leerer Slot binnen 1 Zyklus detektierbar"),
            "Watchdog-Mechanik:",
            (1, "Master überwacht alle Slots aktiv"),
            (1, "Knoten-Ausfall → fehlende Sende-Aktivität"),
            (1, "Sofortige Alarm-Eskalation an Sicherheits-Layer"),
            "Zertifizierungs-Aspekte:",
            (1, "ISO 26262 für Automotive-Funktionssicherheit"),
            (1, "ARP4761 für Avionik (DO-178C / DO-254 Hardware)"),
            (1, "IEC 61508 für Industrie-Anlagen"),
            "Microchip Safety-Package:",
            (1, "Pre-certified FMEDA + Safety-Manual von Microchip"),
            (1, "Reduziert Zertifizierungs-Aufwand für OEM signifikant"),
            (1, "AEC-Q100-Qualifikation als Vorbedingung",),
            "Konkrete Anwendungen:",
            (1, "Flugsteuerungs-Bus (Sicherheits-kritisch)"),
            (1, "Bremsen-/Lenkungs-Backup-Pfade im Auto"),
            (1, "Industrie-Sicherheits-Steuerung (Not-Aus, Lichtschranken)"),
        ])

    add_use_case(prs,
        "Verteilte Fehlertoleranz (N+1-Redundanz)",
        "N+1-redundante Knoten teilen sich einen Bus für 2-aus-3-Voting",
        [
            "Redundanz-Modell:",
            (1, "N produktive Knoten + 1 Hot-Standby"),
            (1, "Alle N+1 senden in eigenen Slots"),
            (1, "Voter-Knoten vergleicht alle Werte"),
            "Voting-Logik:",
            (1, "2-aus-3-Voting bei Triple-Modular-Redundancy"),
            (1, "Mehrheits-Entscheid bei Diskrepanz"),
            (1, "Garantiert verfügbare Daten in jedem ACMA-Zyklus"),
            "Hot-Standby-Wechsel:",
            (1, "Deterministisch in einem definierten Slot"),
            (1, "Keine Stör-Möglichkeit zwischen redundanten Pfaden"),
            (1, "Übergangs-Latenz < 1 Zyklus = < 1 ms"),
            "Anwendungen:",
            (1, "Avionik mit 3+ identischen Computer-Channels"),
            (1, "Industrie-Steuerungen mit Hot-Backup-Master"),
            (1, "Power-Generation mit redundanten Steuerungen"),
            (1, "Medizin-Geräte mit Fail-Safe-Modus"),
        ])

    # ---- 9. Section: Test & Mess ----
    add_section_header(prs, "Test- und Mess-Anwendungen",
        "Synchrone Mess-Knoten · HIL-Testbeds",
        intro_bullets=[
            "Test- und Mess-Aufbauten brauchen:",
            (1, "Reproduzierbare Trigger-Sequenzen"),
            (1, "Synchron-Sampling über N Mess-Punkte"),
            (1, "Deterministische Test-Ausführung über Stunden / Tage"),
            "Vorteil gegenüber Trigger-Bus-Verkabelung:",
            (1, "Eine T1S-Leitung statt Stern-Trigger-Bus"),
            (1, "Bidirektionale Daten + Trigger im selben Medium"),
            (1, "Skalierbar von 2 auf 50 Knoten ohne Re-Verkabelung"),
        ])

    add_use_case(prs,
        "Synchrone Mess-Knoten",
        "Mehrere Messgeräte (Oszilloskope, Logger, Power-Analyser) PTP-synchron",
        [
            "Mess-Geräte-Typen:",
            (1, "Digital-Oszilloskope mit externem Trigger"),
            (1, "Datenlogger für Langzeit-Aufzeichnung"),
            (1, "Power-Analyser für Energie-Verbrauch"),
            (1, "Spectrum-Analyser für RF-Mess-Anwendungen"),
            "Synchronisation-Mechanik:",
            (1, "Trigger-Verteilung über ACMA-Master-Slot"),
            (1, "Mess-Daten-Rückführung über jeweils eigene Slots"),
            (1, "Time-Correlation Hardware-garantiert"),
            "Anwendungen im R&D / QA:",
            (1, "Charakterisierung von Halbleiter-Bauelementen"),
            (1, "Verbrauchs-Profile von Embedded-Systemen"),
            (1, "Multi-Domain-Korrelation (Strom / Spannung / Temperatur)"),
            "Vorteil gegenüber GPIB / LXI:",
            (1, "Niedrigere Hardware-Kosten pro Mess-Knoten"),
            (1, "Kabel-Verkabelung dramatisch vereinfacht"),
            (1, "Skalierbar auf viele Knoten ohne Switch-Investition"),
        ])

    add_use_case(prs,
        "Hardware-in-the-Loop (HIL) Testbeds",
        "Test-System mit synchron geschalteten Lasten und Stimuli",
        [
            "HIL-Testaufbau:",
            (1, "DUT (Device-Under-Test) mit mehreren I/Os"),
            (1, "Programmierbare Lasten an verschiedenen Punkten"),
            (1, "Stimuli-Generatoren für Eingangs-Profile"),
            "ACMA-koordinierter Schalter:",
            (1, "Lasten-Schalt-Sequenzen in eigenen ACMA-Slots"),
            (1, "PTP-Sync ermöglicht µs-genaue Test-Sequenzen"),
            (1, "Reproducible über Wochen/Monate gleicher Test-Reihen"),
            "Test-Cases:",
            (1, "Last-Sprünge zur exakt gleichen Zeit"),
            (1, "Komplexe Sequenzen mit phasen-versetzten Stimuli"),
            (1, "Stress-Tests mit max. parallelem Last-Wechsel"),
            "Vorteile:",
            (1, "Tests sind verifizierbar und auditierbar"),
            (1, "Reproducibility für Regressions-Tests"),
            (1, "Geringere Test-Variation-Streuung"),
        ])

    # ---- 10. Section: Spezielle Anwendungen ----
    add_section_header(prs, "Spezielle Anwendungsfälle",
        "IIoT-Mesh · Smart-Building · Smart-Grid",
        intro_bullets=[
            "Anwendungs-Klassen mit niedrigen Bandbreiten,",
            "aber hoher Knoten-Anzahl und/oder PoDL-Bedarf:",
            (1, "Industrial IoT (IIoT) Sensor-Mesh"),
            (1, "Smart-Building / Gebäude-Automatisierung"),
            (1, "Smart-Grid-Edge / Power-Monitoring"),
            "Gemeinsame Eigenschaften:",
            (1, "Bis zu 50 Knoten / 50 m mit AN60001829-Erweiterung"),
            (1, "PoDL-Speisung möglich (Strom + Daten in einer Leitung)"),
            (1, "Determinismus für sicherheits-relevante Funktionen wichtig"),
        ])

    add_use_case(prs,
        "Backbone für IIoT-Sensor-Mesh",
        "Sensor-Knoten aggregieren über Gateway zur Cloud",
        [
            "Topologie:",
            (1, "Bis zu 50 Sensor-Knoten an einem Mixing-Segment"),
            (1, "1 Edge-Gateway als Aggregator und Cloud-Verbindung"),
            (1, "ACMA verteilt Slots fair über alle Sensoren"),
            "Daten-Fluss:",
            (1, "Jeder Sensor sendet pro Zyklus seinen Mess-Wert"),
            (1, "Gateway aggregiert, fügt Zeitstempel hinzu"),
            (1, "Aggregat-Daten an Cloud (MQTT, OPC-UA, etc.)"),
            "Vorteile gegenüber Wireless:",
            (1, "Keine Funk-Lizenz / Frequenz-Genehmigung nötig"),
            (1, "Deterministische Latenz, garantierte Verfügbarkeit"),
            (1, "Robust gegen elektromagnetische Störungen"),
            "Anwendungen:",
            (1, "Industrie-Anlagen mit verteilter Zustands-Überwachung"),
            (1, "Landwirtschaft (Bewässerung, Klima-Monitoring)"),
            (1, "Logistik (Lager-Klima, Bewegungsmelder)"),
        ])

    add_use_case(prs,
        "Smart-Building / Gebäude-Automatisierung",
        "Lichtsteuerung, HVAC, Sicherheit, Zugangskontrolle in einem Gebäude",
        [
            "Knoten-Vielfalt im Smart-Building:",
            (1, "Lichtsteuerung — Dimmer, Schalter, Sensoren"),
            (1, "HVAC — Heizung, Lüftung, Klimaanlage"),
            (1, "Sicherheits-Systeme — Brand-Alarm, Bewegungsmelder"),
            (1, "Zugangskontrolle — Türöffner, Aufzüge"),
            "ACMA-Eignung:",
            (1, "Niedrige Bandbreite reicht (10 Mbit/s gesamt)"),
            (1, "Aber: viele Knoten pro Bus (bis zu 50)"),
            (1, "Determinismus für sicherheits-relevante Geräte"),
            "PoDL-Vorteil:",
            (1, "Strom + Daten über dasselbe Kabel"),
            (1, "Reduzierte Installations-Kosten"),
            (1, "Einfachere Wartung — eine Leitung pro Gerät"),
            "Konkurrenz: KNX, BACnet, Modbus",
            (1, "T1S = höhere Bandbreite + Standard-Ethernet-Stack"),
            (1, "Migration auf TCP/IP-Anwendungen direkt möglich"),
        ])

    add_use_case(prs,
        "Energiemanagement / Smart-Grid-Edge",
        "Verteilte Energie-Mess-Punkte in Gebäuden oder Industrie-Anlagen",
        [
            "Power-Monitoring-Knoten:",
            (1, "An jedem Verbraucher / Erzeuger ein Mess-Knoten"),
            (1, "Strom / Spannung / Phasen-Winkel synchron erfasst"),
            (1, "Aggregation auf Master für Analyse"),
            "Phasor Measurement Units (PMU):",
            (1, "GPS-/PTP-synchrone Phasen-Mess-Werte"),
            (1, "Stabilitäts-Analyse von Energie-Netzen"),
            (1, "Frühwarn-System für Black-Out-Risiken"),
            "Grid-Sync-Anforderung:",
            (1, "Externe 1PPS-Referenz von GPS oder Master-PMU"),
            (1, "Verteilung über T1S-Bus an alle PMU-Knoten"),
            (1, "Sub-µs Sync zwischen Knoten möglich"),
            "Anwendungen:",
            (1, "Industrial Power-Monitoring (große Werke)"),
            (1, "Erneuerbare Energie (Wind- / Solar-Park-Steuerung)"),
            (1, "E-Auto-Lade-Infrastruktur"),
            (1, "Mikrogrids und Energie-Speicher-Systeme"),
        ])

    # ---- 11. Anwendungs-Übersicht (compact summary) ----
    add_simple(prs, "Anwendungs-Übersicht — kompakte Tabelle", [
        "Industrielle Steuerung:",
        (1, "Motion Control — sub-ms Latenz, sub-µs Sync, mittlere Bandbreite"),
        (1, "Multi-Achs-Sampling — exakte Sample-Synchronität, gering-mittel"),
        (1, "Coordinated I/O Triggering — sub-µs Trigger-Sync, gering"),
        "Sensor-Cluster:",
        (1, "Verteilte ADC-Arrays — Phasen-konstante Mess-Werte, mittel-hoch"),
        (1, "Beamforming — sub-µs Phasen-Bezug, mittel"),
        (1, "LiDAR-Cluster — Cross-Talk-Vermeidung, gering"),
        "Multimedia:",
        (1, "Distributed Audio — Phasen-konstante Wiedergabe, mittel"),
        (1, "Synchronized Lighting — sub-µs Effekt-Synchronität, gering"),
        "Automotive:",
        (1, "Sensor-Backbone — CAN-FD-Replacement, gering"),
        (1, "ADAS-Sensor-Sync — Trigger-Verteilung, gering"),
        (1, "In-Vehicle-Network — ECU-Slots mit Determinismus, gering"),
        "Avionik / Sicherheit:",
        (1, "Redundante Bus-Systeme — ISO-26262, gering-mittel"),
        (1, "Verteilte Fehlertoleranz — N+1-Voting, gering"),
        "Spezielle:",
        (1, "Smart-Building, IIoT-Mesh, Smart-Grid — viele Knoten, gering"),
    ])

    # ---- 12. Was ACMA NICHT kann ----
    add_simple(prs, "Was ACMA + T1S nicht kann — die Grenzen", [
        "High-Throughput-Anwendungen:",
        (1, "10 Mbit/s nominell, ~5-7 Mbit/s effektiv nach ACMA-Overhead"),
        (1, "Geteilt auf alle Knoten — pro-Knoten oft unter 1 Mbit/s"),
        (1, "Video-Streaming oder Bulk-Daten passen nicht"),
        "Bandbreite-überschreitende Datenmengen:",
        (1, "Beispiel: 24-bit ADC @ 100 kSPS × 8 Kanäle = 19 Mbit/s"),
        (1, "Passt NICHT in einen 10 Mbit/s-Bus"),
        (1, "Lösung: Sub-Sampling oder kompaktere Datenformate"),
        "Drahtlose Synchronisation:",
        (1, "ACMA ist physisch kabel-gebunden"),
        (1, "Funk-basierte TDMA-Mechanismen (z.B. TSCH) sind anders"),
        "Standardkonformität:",
        (1, "ACMA ist Microchip-spezifisch, kein IEEE-Standard"),
        (1, "Keine vendor-übergreifende Interop ohne IEEE 802.3da"),
        (1, "AVB-Bridges erkennen ACMA nicht direkt",),
        "Multi-Vendor-Bus-Setups:",
        (1, "Nur Microchip hat ACMA"),
        (1, "Andere Vendoren-Knoten würden ungated stören"),
        (1, "Mischen erfordert sorgfältige Architektur",),
    ])

    # ---- 13. Markt-Strategie ----
    add_simple(prs, "Microchip's Markt-Strategie", [
        "Lücke im Standard:",
        (1, "Standardisiertes TDMA für 10BASE-T1S Multidrop existiert nicht"),
        (1, "IEEE 802.3cg deckt nur PLCA + CSMA/CD"),
        (1, "IEEE 802.1Qbv-TAS gibt es nur in TSN-Switches, nicht im T1S-PHY"),
        (1, "IEEE 802.3da arbeitet daran — aber unklar wann publiziert"),
        "Wettbewerbs-Situation:",
        (1, "NXP TJA1120 — kein ACMA-Pendant"),
        (1, "Onsemi NCN26010 — nur Standard-PLCA"),
        (1, "Marvell 88Q4444 — nur Standard-PLCA"),
        (1, "ADI ADIN1110 — nur Standard-PLCA"),
        "Konsequenz:",
        (1, "Microchip ist allein mit ACMA in dieser Nische"),
        (1, "Jeder TDMA-T1S-Kunde kommt automatisch zu Microchip"),
        (1, "Kein Migrations-Pfad zu Konkurrenz-Hardware"),
        "Strategische Implikation:",
        (1, "ACMA-Wegfall = Marktanteils-Verlust — ökonomisch unwahrscheinlich"),
        (1, "Lifecycle-Erwartung: Standard-Microchip-Industrie ~15+ Jahre"),
        (1, "Investition in ACMA-Code ist gerechtfertigt"),
        "Risiko-Mitigation:",
        (1, "ACMA-Layer architektonisch isolieren (Hygiene)"),
        (1, "Migration zu IEEE 802.1Qbv-TAS bei Bedarf möglich",),
    ])

    # ---- 14. Zusammenfassung ----
    slide = prs.slides.add_slide(prs.slide_layouts[LAYOUT_TITLE_CONTENT])
    set_title(slide, "Zusammenfassung & nächste Schritte")
    set_content_bullets(slide, [
        "Was ACMA + PTP auf 10BASE-T1S liefert:",
        (1, "Kostengünstige TSN-Light-Alternative für mittlere Bandbreite"),
        (1, "Drei Hardware-Garantien — Determinismus, Latenz-Bound, sub-µs Sync"),
        (1, "12+ Anwendungs-Klassen vom Smart-Building bis zur Avionik"),
        "Was ACMA nicht ist:",
        (1, "Kein IEEE-Standard — Microchip-spezifisch"),
        (1, "Aber: alleinstellend in der T1S-Nische"),
        (1, "Wirtschaftlich für Microchip wichtig — wahrscheinlich langlebig"),
        "Implementierungs-Pfad in unserem Projekt:",
        (1, "AN1847-Style Sync (heute auf mult-sync Branch)"),
        (1, "ACMA + EG0 als Architektur-Skizze in readme_acma.md"),
        (1, "Errata s9 verlangt Single-Shot-Re-Arm via ISR"),
        "Weiterführende Doku im Repo:",
        (1, "documentation/ptp/readme_acma.md — Architektur"),
        (1, "documentation/ptp/readme_acma_use_cases.md — Use Cases"),
        (1, "documentation/pdf/lan86xx_family.md — Chip-Familie"),
        (1, "documentation/pdf/lan865x_vs_lan867x_architecture.md — Block-Vergleich"),
        (1, "documentation/ptp/readme_802_1as_roadmap.md — voller Standard"),
    ])

    # ---- 15. Closing slide with call-to-action ----
    add_simple(prs, "Vielen Dank — Fragen & Diskussion", [
        "Hauptbotschaften zum Mitnehmen:",
        (1, "ACMA + PTP auf T1S = TDMA für unter 10 Mbit/s, kostengünstig"),
        (1, "Microchip ist allein in dieser Nische — strategisch wertvoll"),
        (1, "Implementierung machbar mit Errata-bewussten Workarounds"),
        (1, "Anwendungs-Spektrum reicht von Smart-Building bis Avionik"),
        "Diskussions-Punkte:",
        (1, "Welcher Use Case passt zu Ihrem Projekt?"),
        (1, "Brauchen Sie volle Standardkonformität oder reicht TSN-Light?"),
        (1, "Welche Latenz- / Bandbreite-Anforderungen haben Sie konkret?"),
        (1, "Wie kritisch ist Multi-Vendor-Interop in Ihrem Setup?"),
        "Kontakt für tiefere Diskussion:",
        (1, "Architektur-Details: documentation/ptp/readme_acma.md"),
        (1, "Implementierungs-Plan: readme_acma.md §12 Roadmap"),
        (1, "Hardware-Fragen: documentation/pdf/readme_pdf.md (Quick-Lookup)"),
    ])

    # ---- Save ----
    prs.save(OUTPUT)
    print(f"Saved: {OUTPUT}")
    print(f"Slides: {len(prs.slides)}")


def ensure_diagrams_extracted():
    """Run the extraction script if the diagrams folder is empty/missing."""
    if not os.path.exists(DIAGRAMS) or not os.listdir(DIAGRAMS):
        print("Diagrams not yet extracted -- running extractor...")
        extractor = os.path.join(os.path.dirname(__file__),
                                  "extract_microchip_diagrams.py")
        subprocess.check_call([sys.executable, extractor])


if __name__ == "__main__":
    ensure_diagrams_extracted()
    main()
