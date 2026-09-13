"""Builds examples/demo-spec.pdf: a fabricated, non-proprietary requirements
spec for a fictional "Aria Smart Thermostat" used to demo reqcast end to end.

Not part of the reqcast package - a one-off generator, kept here so the
example PDF can be regenerated or tweaked without hand-editing bytes.
Run: .venv/bin/python examples/_build_demo_spec.py
"""
from __future__ import annotations

import pathlib

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

OUT = pathlib.Path(__file__).parent / "demo-spec.pdf"
PAGE_W, PAGE_H = LETTER
MARGIN = 0.9 * inch
HEADER_TEXT = "Demo Systems Inc. - Confidential"
FOOTER_TEXT = "Aria Smart Thermostat SRS - Rev A"


def draw_header_footer(c: canvas.Canvas, page_num: int) -> None:
    c.setFont("Helvetica", 8)
    c.setFillGray(0.45)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 0.5 * inch, HEADER_TEXT)
    c.drawCentredString(PAGE_W / 2, 0.5 * inch, FOOTER_TEXT)
    c.setFillGray(0)


class Flow:
    """Minimal top-down text cursor with automatic page breaks."""

    def __init__(self, c: canvas.Canvas):
        self.c = c
        self.page_num = 1
        self.y = PAGE_H - MARGIN
        draw_header_footer(self.c, self.page_num)

    def _new_page(self) -> None:
        self.c.showPage()
        self.page_num += 1
        self.y = PAGE_H - MARGIN
        draw_header_footer(self.c, self.page_num)

    def ensure(self, height: float) -> None:
        if self.y - height < MARGIN:
            self._new_page()

    def heading(self, number: str, title: str) -> None:
        self.ensure(0.4 * inch)
        self.c.setFont("Helvetica-Bold", 13)
        self.c.drawString(MARGIN, self.y, f"{number} {title}")
        self.y -= 0.3 * inch

    def para(self, text: str, font: str = "Helvetica", size: int = 10, indent: float = 0,
              gray: float = 0) -> None:
        self.ensure(0.22 * inch)
        self.c.setFont(font, size)
        self.c.setFillGray(gray)
        self.c.drawString(MARGIN + indent, self.y, text)
        self.c.setFillGray(0)
        self.y -= 0.22 * inch

    def gap(self, height: float = 0.12 * inch) -> None:
        self.y -= height


def requirement(flow: Flow, req_id: str, title: str, description: str,
                  rationale: str, priority: str) -> None:
    flow.para(f"{req_id}  {title}", font="Helvetica-Bold", size=10.5)
    flow.para(f"Description: {description}", indent=0.2 * inch)
    flow.para(f"Rationale: {rationale}", indent=0.2 * inch)
    flow.para(f"Priority: {priority}", indent=0.2 * inch)
    flow.gap()


def draw_figure(flow: Flow) -> None:
    flow.ensure(2.4 * inch)
    box_w, box_h = 4.2 * inch, 1.6 * inch
    x0 = MARGIN
    y0 = flow.y - box_h
    c = flow.c

    c.setLineWidth(1)
    c.rect(x0, y0, box_w, box_h)

    # Three sub-blocks wired together, just enough to look like a system diagram.
    blocks = [
        ("Sensor\nModule", x0 + 0.15 * inch, y0 + 0.4 * inch, 1.1 * inch, 0.8 * inch),
        ("Control\nUnit", x0 + 1.6 * inch, y0 + 0.4 * inch, 1.1 * inch, 0.8 * inch),
        ("HVAC\nRelay", x0 + 3.05 * inch, y0 + 0.4 * inch, 1.0 * inch, 0.8 * inch),
    ]
    c.setFont("Helvetica", 8)
    for label, bx, by, bw, bh in blocks:
        c.rect(bx, by, bw, bh)
        lines = label.split("\n")
        ty = by + bh / 2 + (len(lines) - 1) * 5
        for line in lines:
            c.drawCentredString(bx + bw / 2, ty, line)
            ty -= 10

    c.line(x0 + 1.25 * inch, y0 + 0.8 * inch, x0 + 1.6 * inch, y0 + 0.8 * inch)
    c.line(x0 + 2.7 * inch, y0 + 0.8 * inch, x0 + 3.05 * inch, y0 + 0.8 * inch)

    flow.y = y0 - 0.18 * inch
    flow.para("Figure 1: System Overview", font="Helvetica-Oblique", size=9, gray=0.3)
    flow.gap(0.2 * inch)


def build() -> None:
    c = canvas.Canvas(str(OUT), pagesize=LETTER)
    flow = Flow(c)

    flow.para("Aria Smart Thermostat", font="Helvetica-Bold", size=16)
    flow.gap(0.05 * inch)
    flow.para("Software Requirements Specification (fabricated example document)", size=10, gray=0.3)
    flow.gap(0.3 * inch)

    flow.heading("3.1", "Power Management")
    requirement(
        flow, "REQ-DEMO-001", "Low-power standby",
        "The thermostat shall enter standby mode after 120 seconds of no user interaction "
        "and no active heating or cooling demand.",
        "Reduces average daily power draw for battery-backed installations.",
        "High",
    )
    requirement(
        flow, "REQ-DEMO-002", "Battery backup runtime",
        "On mains power loss, the thermostat shall maintain display and sensor operation "
        "for at least 4 hours from the internal battery.",
        "Prevents loss of schedule and setpoint state during short outages.",
        "Medium",
    )
    requirement(
        flow, "REQ-DEMO-003", "Charge indicator",
        "The thermostat shall display a battery charge indicator whenever charge is below 20%.",
        "Gives the user advance warning before backup power is exhausted.",
        "Low",
    )

    draw_figure(flow)

    flow.heading("3.2", "User Interface")
    requirement(
        flow, "REQ-DEMO-004", "Setpoint adjustment",
        "The user shall be able to adjust the target temperature in 0.5 degree increments "
        "using the front-panel dial.",
        "Matches the granularity users expect from comparable thermostats.",
        "High",
    )
    requirement(
        flow, "REQ-DEMO-005", "Display readability",
        "The display shall remain readable under direct sunlight of up to 10,000 lux.",
        "Thermostats are frequently installed near windows or glass doors.",
        "Medium",
    )
    requirement(
        flow, "REQ-DEMO-006", "Child lock",
        "The thermostat shall support a child-lock mode that disables the front-panel "
        "controls until a 3-second button-press sequence is performed.",
        "Prevents accidental setpoint changes in households with young children.",
        "Low",
    )

    flow.heading("3.3", "Connectivity")
    requirement(
        flow, "REQ-DEMO-007", "Wi-Fi provisioning",
        "The thermostat shall support Wi-Fi provisioning via a companion mobile app "
        "using WPA2 or WPA3.",
        "Required for remote schedule management and firmware updates.",
        "High",
    )
    requirement(
        flow, "REQ-DEMO-008", "Offline schedule fallback",
        "If connectivity is lost, the thermostat shall continue to run the last "
        "synchronized schedule without interruption.",
        "Loss of Wi-Fi must never affect core climate-control behavior.",
        "High",
    )

    flow.heading("3.4", "Diagnostics")
    requirement(
        flow, "REQ-DEMO-009", "Sensor self-test",
        "On power-up, the thermostat shall run a self-test of the temperature and "
        "humidity sensors and report a fault code on the display if either fails.",
        "Silent sensor failure would otherwise show as a plausible but wrong reading.",
        "High",
    )
    requirement(
        flow, "REQ-DEMO-010", "Diagnostic log retention",
        "The thermostat shall retain the last 30 days of fault codes and HVAC-relay "
        "activations in local non-volatile storage.",
        "Lets a technician review recent history during a service visit without "
        "needing cloud connectivity.",
        "Medium",
    )
    requirement(
        flow, "REQ-DEMO-011", "Relay cycle counter",
        "The thermostat shall count and persist the total number of HVAC-relay "
        "activation cycles over the device's lifetime.",
        "Relay wear is a known field-failure mode; a cycle count supports "
        "predictive maintenance.",
        "Low",
    )

    flow.heading("3.5", "Compliance")
    requirement(
        flow, "REQ-DEMO-012", "Standby power limit",
        "The thermostat shall draw no more than 1.0 W average power in standby mode.",
        "Required to meet the target energy-efficiency labeling class.",
        "High",
    )
    requirement(
        flow, "REQ-DEMO-013", "Operating temperature range",
        "The thermostat shall operate correctly across an ambient range of 0C to 40C.",
        "Covers unconditioned hallway and utility-room installations.",
        "Medium",
    )
    requirement(
        flow, "REQ-DEMO-014", "RF emissions",
        "The thermostat's Wi-Fi radio shall comply with the applicable regional "
        "RF emissions and coexistence standards for its target markets.",
        "Required for regulatory certification prior to sale.",
        "High",
    )

    c.save()


if __name__ == "__main__":
    build()
    print(f"Wrote {OUT}")
