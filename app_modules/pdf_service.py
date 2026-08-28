"""Compatibility PDF drawing service backed by ReportLab.

This module intentionally keeps the historic ``SimplePdf`` API so older or future
Easy Admin code that imports it still receives standards-compliant ReportLab PDF
output instead of the former hand-built raw PDF byte writer.
"""

import io

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from app_modules.pdf_standard import validate_pdf_bytes


class SimplePdf:
    """Small ReportLab drawing adapter preserving the legacy Easy Admin API."""

    def __init__(self, width=595.28, height=841.89):
        self.width = float(width)
        self.height = float(height)
        self.buffer = io.BytesIO()
        self.canvas = canvas.Canvas(
            self.buffer,
            pagesize=(self.width, self.height),
            pdfVersion=(1, 4),
            pageCompression=1,
        )
        self.current_font = ('Helvetica', 9.0)
        self._page_started = False

    def new_page(self):
        if self._page_started:
            self.canvas.showPage()
        self._page_started = True
        self.set_font('Helvetica', 9)
        self.set_line_width(0.6)

    def set_font(self, font='Helvetica', size=9):
        if font not in ('Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique', 'Helvetica-BoldOblique'):
            font = 'Helvetica'
        self.current_font = (font, float(size))
        self.canvas.setFont(font, float(size))

    def set_line_width(self, width):
        self.canvas.setLineWidth(float(width))

    def rect(self, x, y, w, h):
        self.canvas.rect(float(x), float(y), float(w), float(h), stroke=1, fill=0)

    def line(self, x1, y1, x2, y2):
        self.canvas.line(float(x1), float(y1), float(x2), float(y2))

    def text_width(self, text, font='Helvetica', size=9):
        try:
            return stringWidth(str(text or ''), font, float(size))
        except Exception:
            return stringWidth(str(text or ''), 'Helvetica', float(size))

    def draw_string(self, x, y, text, font=None, size=None):
        if font or size:
            self.set_font(font or self.current_font[0], size or self.current_font[1])
        self.canvas.drawString(float(x), float(y), str(text or ''))

    def draw_right(self, x, y, text, font=None, size=None):
        font = font or self.current_font[0]
        size = float(size or self.current_font[1])
        self.set_font(font, size)
        self.canvas.drawRightString(float(x), float(y), str(text or ''))

    def finish(self):
        if not self._page_started:
            self.new_page()
        self.canvas.save()
        return io.BytesIO(validate_pdf_bytes(self.buffer.getvalue()))
