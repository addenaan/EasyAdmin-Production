import io


def pdf_escape(value):
    text = str(value or '')
    text = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    return text.encode('latin-1', 'replace').decode('latin-1')


class SimplePdf:
    """Small dependency-free PDF writer used as the server-side PDF foundation."""
    def __init__(self, width=595.28, height=841.89):
        self.width = float(width)
        self.height = float(height)
        self.pages = []
        self.current = []
        self.font_alias = {'Helvetica': 'F1', 'Helvetica-Bold': 'F2'}
        self.current_font = ('Helvetica', 9)

    def new_page(self):
        if self.current:
            self.pages.append('\n'.join(self.current))
        self.current = []
        self.set_font('Helvetica', 9)
        self.set_line_width(0.6)

    def set_font(self, font='Helvetica', size=9):
        if font not in self.font_alias:
            font = 'Helvetica'
        self.current_font = (font, float(size))
        self.current.append(f"/{self.font_alias[font]} {float(size):.2f} Tf")

    def set_line_width(self, width):
        self.current.append(f"{float(width):.2f} w")

    def rect(self, x, y, w, h):
        self.current.append(f"{float(x):.2f} {float(y):.2f} {float(w):.2f} {float(h):.2f} re S")

    def line(self, x1, y1, x2, y2):
        self.current.append(f"{float(x1):.2f} {float(y1):.2f} m {float(x2):.2f} {float(y2):.2f} l S")

    def text_width(self, text, font='Helvetica', size=9):
        total = 0.0
        for ch in str(text or ''):
            if ch in 'il.,:;|! ':
                total += 0.25
            elif ch in 'MW@#%&':
                total += 0.75
            elif ch.isupper() or ch.isdigit():
                total += 0.58
            else:
                total += 0.50
        return total * float(size)

    def draw_string(self, x, y, text, font=None, size=None):
        if font or size:
            self.set_font(font or self.current_font[0], size or self.current_font[1])
        self.current.append(f"BT 1 0 0 1 {float(x):.2f} {float(y):.2f} Tm ({pdf_escape(text)}) Tj ET")

    def draw_right(self, x, y, text, font=None, size=None):
        font = font or self.current_font[0]
        size = float(size or self.current_font[1])
        self.draw_string(float(x) - self.text_width(text, font, size), y, text, font, size)

    def finish(self):
        if self.current:
            self.pages.append('\n'.join(self.current))
            self.current = []
        objects = []
        catalog_id, pages_id, font1_id, font2_id = 1, 2, 3, 4
        next_id = 5
        page_ids, content_ids = [], []
        for _ in self.pages:
            page_ids.append(next_id); next_id += 1
            content_ids.append(next_id); next_id += 1
        objects.append((catalog_id, f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode('latin-1')))
        kids = ' '.join(f'{pid} 0 R' for pid in page_ids)
        objects.append((pages_id, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode('latin-1')))
        objects.append((font1_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
        objects.append((font2_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"))
        for pid, cid, content in zip(page_ids, content_ids, self.pages):
            page_obj = (f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {self.width:.2f} {self.height:.2f}] "
                        f"/Resources << /Font << /F1 {font1_id} 0 R /F2 {font2_id} 0 R >> >> /Contents {cid} 0 R >>")
            stream = content.encode('latin-1', 'replace')
            content_obj = b"<< /Length " + str(len(stream)).encode('ascii') + b" >>\nstream\n" + stream + b"\nendstream"
            objects.append((pid, page_obj.encode('latin-1')))
            objects.append((cid, content_obj))
        out = io.BytesIO()
        out.write(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
        offsets = [0]
        for obj_id, body in objects:
            offsets.append(out.tell())
            out.write(f"{obj_id} 0 obj\n".encode('ascii'))
            out.write(body)
            out.write(b"\nendobj\n")
        xref_pos = out.tell()
        max_id = max(obj_id for obj_id, _ in objects)
        obj_by_id = {obj_id: offsets[i + 1] for i, (obj_id, _) in enumerate(objects)}
        out.write(f"xref\n0 {max_id + 1}\n".encode('ascii'))
        out.write(b"0000000000 65535 f \n")
        for obj_id in range(1, max_id + 1):
            out.write(f"{obj_by_id.get(obj_id, 0):010d} 00000 n \n".encode('ascii'))
        out.write(f"trailer\n<< /Size {max_id + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode('ascii'))
        out.seek(0)
        return out
