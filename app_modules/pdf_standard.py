import io
import os
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether


DARK = colors.HexColor('#1f2937')
BLUE = colors.HexColor('#0d6efd')
GREY = colors.HexColor('#6c757d')
LIGHT = colors.HexColor('#f5f7fa')
BORDER = colors.HexColor('#d8dde5')
GREEN = colors.HexColor('#198754')
RED = colors.HexColor('#dc3545')
WHITE = colors.white


def validate_pdf_bytes(data):
    if hasattr(data, 'getvalue'):
        data = data.getvalue()
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError('Generated PDF is not binary data.')
    data = bytes(data)
    if len(data) < 100 or not data.startswith(b'%PDF-') or b'%%EOF' not in data[-4096:]:
        raise ValueError('Generated PDF failed compatibility validation.')
    return data


def _paragraph(text, style):
    return Paragraph(escape(str(text or '')).replace('\n', '<br/>'), style)


def _as_number(value, default=0.0):
    """Parse Easy Admin display-money strings and normal numeric values safely."""
    if value is None or value == '':
        return float(default or 0.0)
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace('\u00a0', ' ').replace('R', '').replace('r', '')
    raw = re.sub(r'[^0-9, .+\-]', '', raw).replace(' ', '')
    if not raw or raw in {'-', '+', '.', ','}:
        return float(default or 0.0)
    if ',' in raw and '.' in raw:
        # The right-most separator is the decimal separator.
        if raw.rfind(',') > raw.rfind('.'):
            raw = raw.replace('.', '').replace(',', '.')
        else:
            raw = raw.replace(',', '')
    elif ',' in raw:
        parts = raw.split(',')
        if len(parts) == 2 and len(parts[1]) <= 2:
            raw = parts[0].replace(',', '') + '.' + parts[1]
        else:
            raw = raw.replace(',', '')
    try:
        return float(raw)
    except Exception:
        return float(default or 0.0)


def _money_text(value):
    amount = _as_number(value, 0.0)
    sign = '-' if amount < 0 else ''
    raw = f"{abs(amount):,.2f}"
    raw = raw.replace(',', '__THOUSANDS__').replace('.', ',').replace('__THOUSANDS__', ' ')
    return f"{sign}R {raw}"


def _logo_flowable(logo_path, max_w=36*mm, max_h=20*mm):
    if not logo_path or not os.path.exists(logo_path):
        return None
    try:
        img = Image(logo_path)
        iw, ih = float(img.imageWidth or 1), float(img.imageHeight or 1)
        ratio = min(max_w / iw, max_h / ih)
        img.drawWidth = iw * ratio
        img.drawHeight = ih * ratio
        return img
    except Exception:
        return None


def _styles():
    s = getSampleStyleSheet()
    return {
        'title': ParagraphStyle('EA_Title', parent=s['Title'], fontName='Helvetica-Bold', fontSize=17, leading=20, textColor=BLUE, spaceAfter=4),
        'subtitle': ParagraphStyle('EA_Subtitle', parent=s['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=GREY),
        'company': ParagraphStyle('EA_Company', parent=s['Normal'], fontName='Helvetica-Bold', fontSize=11, leading=13, textColor=DARK),
        'small': ParagraphStyle('EA_Small', parent=s['Normal'], fontName='Helvetica', fontSize=7.5, leading=9.5, textColor=GREY),
        'body': ParagraphStyle('EA_Body', parent=s['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=DARK),
        'body_bold': ParagraphStyle('EA_BodyBold', parent=s['Normal'], fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=DARK),
        'section': ParagraphStyle('EA_Section', parent=s['Heading2'], fontName='Helvetica-Bold', fontSize=10, leading=12, textColor=DARK, spaceBefore=5, spaceAfter=5),
        'right': ParagraphStyle('EA_Right', parent=s['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=DARK, alignment=TA_RIGHT),
        'center': ParagraphStyle('EA_Center', parent=s['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=DARK, alignment=TA_CENTER),
    }


def _company_header_story(company, logo_path, title, subtitle='', framework=None):
    st = _styles()
    company = company or {}
    logo = _logo_flowable(logo_path)
    left_bits = []
    if logo:
        left_bits.append(logo)
        left_bits.append(Spacer(1, 2*mm))
    left_bits.append(_paragraph(company.get('name') or 'Company', st['company']))
    for value in (
        company.get('address') or '',
        ('Reg No: ' + str(company.get('registration_number'))) if company.get('registration_number') else '',
        ('Email: ' + str(company.get('contact_email'))) if company.get('contact_email') else '',
        ('Tel: ' + str(company.get('contact_number'))) if company.get('contact_number') else '',
        ('VAT No: ' + str(company.get('vat_number'))) if company.get('vat_number') else '',
    ):
        if value:
            left_bits.append(_paragraph(value, st['small']))
    right_bits = [_paragraph(title or 'Easy Admin PDF', st['title'])]
    if subtitle:
        right_bits.append(_paragraph(subtitle, st['subtitle']))
    if framework:
        right_bits.append(_paragraph(framework, st['body_bold']))
    left = Table([[x] for x in left_bits], colWidths=[None])
    left.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
    right = Table([[x] for x in right_bits], colWidths=[None])
    right.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('ALIGN',(0,0),(-1,-1),'RIGHT'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
    return Table([[left, right]], colWidths=[90*mm, 90*mm], style=[('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),4*mm)])


def build_table_report_pdf(payload, logo_path=None):
    payload = payload or {}
    orient = str(payload.get('orientation') or 'portrait').lower()
    pagesize = landscape(A4) if orient == 'landscape' else A4
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=12*mm, rightMargin=12*mm, topMargin=11*mm, bottomMargin=13*mm, title=str(payload.get('title') or 'Easy Admin PDF'))
    st = _styles()
    story = [_company_header_story(payload.get('company') or {}, logo_path, payload.get('title') or 'Report', payload.get('subtitle') or '', payload.get('framework')),
             Spacer(1, 2*mm)]

    columns = payload.get('columns') or [{'label':'Description','align':'left'},{'label':'Amount','align':'right'}]
    page_w = pagesize[0] - doc.leftMargin - doc.rightMargin

    def column_widths(active_columns):
        defined = [float(c.get('width') or 0) for c in active_columns]
        if any(defined):
            total = sum(defined) or 1.0
            return [page_w * (v / total) for v in defined]
        weights = []
        for c in active_columns:
            label = str(c.get('label') or '').lower()
            if any(k in label for k in ('description','account','client','employee','project','notes')):
                weights.append(2.0)
            elif any(k in label for k in ('amount','total','balance','debit','credit','vat','paid','outstanding')):
                weights.append(1.15)
            else:
                weights.append(1.0)
        total = sum(weights) or 1.0
        return [page_w * (w/total) for w in weights]

    def pcell(value, idx, active_columns, bold=False, white=False):
        align = str(active_columns[idx].get('align') or 'left').lower() if idx < len(active_columns) else 'left'
        base = st['body_bold'] if bold else st['body']
        style = ParagraphStyle('tmp', parent=base, fontSize=7.2, leading=8.7,
                               alignment=TA_RIGHT if align == 'right' else TA_CENTER if align == 'center' else TA_LEFT,
                               textColor=WHITE if white else DARK)
        return _paragraph(value, style)

    def build_table(rows, active_columns):
        widths = column_widths(active_columns)
        header = []
        for idx, c in enumerate(active_columns):
            style = ParagraphStyle('hdr', parent=st['body_bold'], fontSize=7.3, leading=8.5, textColor=WHITE,
                                   alignment=TA_RIGHT if str(c.get('align') or '').lower()=='right' else TA_CENTER if str(c.get('align') or '').lower()=='center' else TA_LEFT)
            header.append(_paragraph(c.get('label') or '', style))
        data = [header]
        row_styles = []
        for ridx, row in enumerate(rows or [], start=1):
            meta = row if isinstance(row, dict) else {}
            cells = meta.get('cells') if isinstance(row, dict) else row
            cells = list(cells or [])
            data.append([pcell(cells[i] if i < len(cells) else '', i, active_columns, bool(meta.get('bold')), bool(meta.get('dark'))) for i in range(len(active_columns))])
            row_styles.append((ridx, meta))
        if len(data) == 1:
            data.append([pcell('No records found',0,active_columns)] + [pcell('',i,active_columns) for i in range(1,len(active_columns))])
        tbl = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
        commands = [
            ('BACKGROUND',(0,0),(-1,0),BLUE), ('TEXTCOLOR',(0,0),(-1,0),WHITE),
            ('GRID',(0,0),(-1,-1),0.35,BORDER), ('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LEFTPADDING',(0,0),(-1,-1),4), ('RIGHTPADDING',(0,0),(-1,-1),4),
            ('TOPPADDING',(0,0),(-1,-1),4), ('BOTTOMPADDING',(0,0),(-1,-1),4),
        ]
        for ridx, meta in row_styles:
            if meta.get('dark'):
                commands += [('BACKGROUND',(0,ridx),(-1,ridx),DARK), ('TEXTCOLOR',(0,ridx),(-1,ridx),WHITE)]
            elif meta.get('shade'):
                commands.append(('BACKGROUND',(0,ridx),(-1,ridx),LIGHT))
            if meta.get('total'):
                commands.append(('LINEABOVE',(0,ridx),(-1,ridx),0.8,DARK))
        tbl.setStyle(TableStyle(commands))
        return tbl

    groups = payload.get('groups') or [{'rows': payload.get('rows') or []}]
    for gi, group in enumerate(groups):
        if group.get('title'):
            story.append(_paragraph(group.get('title'), st['section']))
        story.append(build_table(group.get('rows') or [], group.get('columns') or columns))
        if gi < len(groups)-1:
            story.append(Spacer(1, 4*mm))

    footer_company = (payload.get('company') or {}).get('name') or ''
    def footer(canvas, doc_obj):
        canvas.saveState()
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.4)
        canvas.line(doc.leftMargin, 8*mm, pagesize[0]-doc.rightMargin, 8*mm)
        canvas.setFillColor(GREY)
        canvas.setFont('Helvetica', 7)
        canvas.drawString(doc.leftMargin, 4.8*mm, str(footer_company))
        canvas.drawRightString(pagesize[0]-doc.rightMargin, 4.8*mm, f'Page {doc_obj.page}')
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return validate_pdf_bytes(buf.getvalue())


def build_payslip_pdf(payslip, employee, company, logo_path=None, leave_balances=None, draft=False):
    """Render the PDF with the same information hierarchy as the browser preview."""
    p = payslip or {}
    e = employee or {}
    c = company or {}
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=4*mm, rightMargin=4*mm,
                            topMargin=5*mm, bottomMargin=8*mm, title='Payslip')
    st = _styles()
    title = 'DRAFT PAYSLIP' if draft else ('ADJUSTMENT PAYSLIP' if str(p.get('payslip_type') or '').lower()=='adjustment' else 'PAYSLIP')
    page_width = A4[0] - doc.leftMargin - doc.rightMargin

    title_style = ParagraphStyle('PayslipPreviewTitle', parent=st['title'], fontSize=17,
                                 leading=20, alignment=TA_RIGHT,
                                 textColor=RED if draft else GREEN, spaceAfter=3*mm)
    company_style = ParagraphStyle('PayslipPreviewCompany', parent=st['company'],
                                   fontSize=21, leading=24, spaceAfter=5*mm)
    detail_style = ParagraphStyle('PayslipPreviewDetail', parent=st['body'],
                                  fontSize=8.5, leading=11, alignment=TA_RIGHT)
    small_company_style = ParagraphStyle('PayslipPreviewCompanySmall', parent=st['small'],
                                         fontSize=10.5, leading=17, textColor=GREY)

    logo = _logo_flowable(logo_path, max_w=34*mm, max_h=18*mm)
    left_bits = []
    if logo:
        left_bits += [logo, Spacer(1, 2*mm)]
    left_bits.append(_paragraph(c.get('name') or 'Company', company_style))
    company_lines = [
        ('Reg No: ' + str(c.get('registration_number'))) if c.get('registration_number') else '',
        c.get('address') or '',
        ('Email: ' + str(c.get('contact_email'))) if c.get('contact_email') else '',
        ('Tel: ' + str(c.get('contact_number'))) if c.get('contact_number') else '',
    ]
    left_bits.extend(_paragraph(value, small_company_style) for value in company_lines if value)
    period = str(p.get('date') or p.get('period') or '')[:7]
    employee_name = e.get('name') or p.get('name') or ''
    employee_number = e.get('emp_number') or p.get('emp_num') or ''
    identity_number = e.get('id_passport') or p.get('id_num') or ''
    right_bits = [
        _paragraph(title, title_style),
        Paragraph('<b>Date:</b> ' + escape(str(period)), detail_style),
        Paragraph('<b>Employee:</b> ' + escape(str(employee_name)) +
                  (' (' + escape(str(employee_number)) + ')' if employee_number else ''), detail_style),
        Paragraph('<b>ID:</b> ' + escape(str(identity_number)), detail_style),
    ]
    no_padding = [('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),
                  ('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),
                  ('BOTTOMPADDING',(0,0),(-1,-1),0)]
    left = Table([[item] for item in left_bits], colWidths=[page_width*0.61], style=no_padding)
    right = Table([[item] for item in right_bits], colWidths=[page_width*0.37], style=no_padding)
    header = Table([[left, right]], colWidths=[page_width*0.62, page_width*0.38])
    header.setStyle(TableStyle(no_padding + [('LINEBELOW',(0,0),(-1,-1),0.35,BORDER),
                                             ('BOTTOMPADDING',(0,0),(-1,-1),8*mm)]))
    story = [header, Spacer(1, 7*mm)]

    def f(key, fallback=0):
        return _as_number(p.get(key, fallback), fallback)

    def preview_money(value):
        return f"R {_as_number(value):.2f}"

    desc_width = page_width * 0.64
    earning_width = page_width * 0.17
    deduction_width = page_width - desc_width - earning_width
    note_style = ParagraphStyle('PayslipPreviewNote', parent=st['small'], fontSize=7,
                                leading=8.5, alignment=TA_LEFT, textColor=GREY)
    code_style = ParagraphStyle('PayslipPreviewCode', parent=note_style,
                                fontName='Helvetica-Oblique', alignment=TA_RIGHT)
    employer_label_style = ParagraphStyle('PayslipPreviewEmployerLabel', parent=st['body'],
                                          fontName='Helvetica-Oblique', textColor=GREY)
    employer_amount_style = ParagraphStyle('PayslipPreviewEmployerAmount', parent=st['right'],
                                           fontName='Helvetica-Oblique', textColor=GREY)

    def description_cell(label, note='', code='', employer=False):
        label_text = escape(str(label))
        if note:
            label_text += ' <font color="#6c757d" size="7"><i>' + escape(str(note)) + '</i></font>'
        usable_width = desc_width - 10
        parts = [[Paragraph(label_text, employer_label_style if employer else st['body']),
                  _paragraph(code, code_style)]]
        cell = Table(parts, colWidths=[usable_width*0.73, usable_width*0.27])
        cell.setStyle(TableStyle(no_padding + [('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
        return cell

    rows = []
    gross_note = p.get('salary_proration_note') or p.get('days_worked_display') or ''
    rows.append((description_cell('Calculated Gross', gross_note, 'Code: 3601'), f('gross_salary', p.get('gross',0)), None, 'normal'))
    rows.append((description_cell('Overtime / Premium Pay', p.get('sundays_display') or '', 'Code: 3607'), f('overtime'), None, 'normal'))
    optional_earnings = [
        ('Reimbursable Expenses (Non-taxable)', '', '', f('reimbursable_expenses')),
    ]
    bonus = f('bonus')
    if bonus > 0:
        treatment = str(p.get('bonus_tax_treatment_code') or p.get('bonus_tax_treatment') or 'annual').lower()
        annual = 'annual' in treatment
        optional_earnings.append(('Bonus', '(Annual / Once-off)' if annual else '(Current-period / Production)',
                                  'Code: 3605' if annual else 'Included in Code: 3601', bonus))
    leave_payout = f('leave_payout_amount')
    if leave_payout > 0:
        leave_detail = (f"{f('leave_payout_days'):.2f} days @ R {f('leave_payout_daily_rate'):.6f} "
                        f"through {p.get('leave_payout_date') or ''}; balance after payout: "
                        f"{f('annual_leave_balance_after'):.2f} days")
        optional_earnings.append(('Annual Leave Payout', leave_detail, 'Code: 3605', leave_payout))
    optional_earnings.append(('Transport Reimbursement (Tax Free)', '', 'Code: 3702', f('transport')))
    for label, note, code, amount in optional_earnings:
        if amount > 0:
            rows.append((description_cell(label, note, code), amount, None, 'normal'))
    rows.extend([
        (description_cell('PAYE Tax', '', 'Code: 4102'), None, f('paye'), 'normal'),
        (description_cell('UIF (Employee 1%)', '', 'Code: 4141'), None, f('uif', p.get('uif_emp',0)), 'normal'),
    ])
    if f('loan_repayment') > 0:
        rows.append((description_cell('Loan Repayment'), None, f('loan_repayment'), 'normal'))
    rows.append((description_cell('UIF (Employer 1%)', employer=True), f('uif', p.get('uif_er',0)), None, 'employer'))
    if f('sdl') > 0 or p.get('sdl_applicable') in (True, 1, '1'):
        rows.append((description_cell('SDL (Employer 1%)', '', 'Code: 4142', employer=True), f('sdl'), None, 'employer'))

    body = [[_paragraph('Description', st['body_bold']), _paragraph('Earnings', st['right']),
             _paragraph('Deductions', st['right'])]]
    for description, earning, deduction, kind in rows:
        amount_style = employer_amount_style if kind == 'employer' else st['right']
        body.append([description,
                     _paragraph(preview_money(earning), amount_style) if earning is not None else '',
                     _paragraph(preview_money(deduction), amount_style) if deduction is not None else ''])
    net_value = p.get('net') if p.get('net') not in (None,'') else p.get('net_salary')
    net_style = ParagraphStyle('PayslipPreviewNet', parent=st['right'], fontName='Helvetica-Bold',
                               fontSize=10.5, leading=12, textColor=GREEN)
    body.append([_paragraph('NET PAY', st['body_bold']), _paragraph(preview_money(net_value), net_style), ''])
    table = Table(body, colWidths=[desc_width, earning_width, deduction_width], repeatRows=1)
    commands = [
        ('BACKGROUND',(0,0),(-1,0),LIGHT), ('GRID',(0,0),(-1,-2),0.35,BORDER),
        ('BOX',(0,0),(-1,-1),0.5,BORDER), ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('LEFTPADDING',(0,0),(-1,-1),5), ('RIGHTPADDING',(0,0),(-1,-1),5),
        ('TOPPADDING',(0,0),(-1,-2),11.4), ('BOTTOMPADDING',(0,0),(-1,-2),11.4),
        ('SPAN',(1,-1),(2,-1)), ('ALIGN',(1,-1),(2,-1),'RIGHT'),
        ('LINEABOVE',(0,-1),(-1,-1),0.75,DARK), ('TOPPADDING',(0,-1),(-1,-1),6),
        ('BOTTOMPADDING',(0,-1),(-1,-1),6), ('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold'),
    ]
    for index, row in enumerate(rows, start=1):
        if row[3] == 'employer':
            commands += [('TEXTCOLOR',(0,index),(-1,index),GREY), ('FONTNAME',(0,index),(-1,index),'Helvetica-Oblique')]
    table.setStyle(TableStyle(commands))
    story.append(table)

    def balance_text(value):
        text = str(value if value not in (None, '') else 'N/A').strip()
        if text.upper() == 'N/A':
            return 'N/A'
        text = re.sub(r'\s+Days(?:\s+left.*)?$', '', text, flags=re.IGNORECASE)
        try:
            text = f"{float(text):g}"
        except (TypeError, ValueError):
            pass
        return text

    if leave_balances:
        leave_title = ParagraphStyle('PayslipPreviewLeaveTitle', parent=st['body'], fontSize=8,
                                     leading=10, textColor=BLUE, spaceAfter=2*mm)
        leave_body = ParagraphStyle('PayslipPreviewLeaveBody', parent=st['body'], fontSize=8.5, leading=14)
        annual = balance_text(leave_balances.get('annual'))
        sick = balance_text(leave_balances.get('sick'))
        family = balance_text(leave_balances.get('family'))
        annual_suffix = '' if annual == 'N/A' else ' Days'
        sick_suffix = '' if sick == 'N/A' else ' Days left in 36-month cycle'
        family_suffix = '' if family == 'N/A' else ' Days left in annual cycle'
        leave_items = [
            _paragraph('Statutory Leave Balances', leave_title),
            Table([['']], colWidths=[page_width-10*mm], rowHeights=[0.1],
                  style=[('LINEABOVE',(0,0),(-1,-1),0.35,BORDER),('LEFTPADDING',(0,0),(-1,-1),0),
                         ('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]),
            Paragraph('Annual Leave Balance: <b>' + escape(annual) + '</b>' + annual_suffix, leave_body),
            Paragraph('Sick Leave Balance: <b>' + escape(sick) + '</b>' + sick_suffix, leave_body),
            Paragraph('Family Responsibility Leave: <b>' + escape(family) + '</b>' + family_suffix, leave_body),
        ]
        leave_inner = Table([[item] for item in leave_items], colWidths=[page_width-13*mm], style=no_padding)
        leave_box = Table([[leave_inner]], colWidths=[page_width], cornerRadii=[4,4,4,4])
        leave_box.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHT), ('BOX',(0,0),(-1,-1),0.4,BORDER),
                                       ('LEFTPADDING',(0,0),(-1,-1),4.5*mm), ('RIGHTPADDING',(0,0),(-1,-1),4.5*mm),
                                       ('TOPPADDING',(0,0),(-1,-1),4.5*mm), ('BOTTOMPADDING',(0,0),(-1,-1),7.5*mm)]))
        story += [Spacer(1, 7.5*mm), leave_box]

    # Adjustment reasons are internal audit data and must never appear on a payslip.
    doc.build(story)
    return validate_pdf_bytes(buf.getvalue())


def build_irp5_pdf(irp5, company=None, logo_path=None):
    i = irp5 or {}
    company = company or {}
    payload = {
        'title': 'IRP5 / IT3(a) - Internal Use',
        'subtitle': f"Tax Year {i.get('tax_year','')} | {i.get('period','')}",
        'company': company,
        'columns': [{'label':'Field','align':'left'},{'label':'Value','align':'right'}],
        'groups': [
            {'title':'Employee Details','rows':[
                {'cells':['Employee', i.get('name') or '']},
                {'cells':['Employee No.', i.get('emp_num') or '']},
                {'cells':['ID / Passport', i.get('id_num') or '']},
                {'cells':['Tax Number', i.get('tax_number') or '']},
            ]},
            {'title':'Tax Certificate Codes','rows':[
                {'cells':['3601 - Salary / Wages + Current-period Bonus', i.get('code_3601') or '0.00']},
                {'cells':['3605 - Annual Payment', i.get('code_3605') or '0.00']},
                {'cells':['3607 - Overtime', i.get('code_3607') or '0.00']},
                {'cells':['3702 - Travel', i.get('code_3702') or '0.00']},
                {'cells':['3699 - Gross Employment Income', i.get('code_3699') or '0.00'], 'bold':True, 'shade':True, 'total':True},
                {'cells':['4102 - PAYE', i.get('code_4102') or '0.00']},
                {'cells':['4141 - Employee + Employer UIF', i.get('code_4141') or '0.00']},
                {'cells':['4142 - Employer SDL', i.get('code_4142') or '0.00']},
                {'cells':['4149 - Total Tax / SDL / UIF', i.get('code_4149') or '0.00'], 'bold':True, 'shade':True, 'total':True},
            ]},
        ]
    }
    if i.get('warning'):
        payload['groups'].append({'title':'Compliance Note','rows':[{'cells':['Historical payroll', i.get('warning')]}]})
    return build_table_report_pdf(payload, logo_path)
