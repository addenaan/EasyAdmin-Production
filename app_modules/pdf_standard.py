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
    p = payslip or {}
    e = employee or {}
    c = company or {}
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14*mm, rightMargin=14*mm, topMargin=12*mm, bottomMargin=14*mm, title='Payslip')
    st = _styles()
    title = 'DRAFT PAYSLIP' if draft else ('ADJUSTMENT PAYSLIP' if str(p.get('payslip_type') or '').lower()=='adjustment' else 'PAYSLIP')
    story = [_company_header_story(c, logo_path, title, 'Payroll period: ' + str(p.get('date') or p.get('period') or '')[:7], None), Spacer(1,3*mm)]
    emp_rows = [
        [_paragraph('Employee', st['body_bold']), _paragraph(e.get('name') or p.get('name') or '', st['body'])],
        [_paragraph('Employee No.', st['body_bold']), _paragraph(e.get('emp_number') or p.get('emp_num') or '', st['body'])],
        [_paragraph('ID / Passport', st['body_bold']), _paragraph(e.get('id_passport') or p.get('id_num') or '', st['body'])],
    ]
    info = Table(emp_rows, colWidths=[38*mm, 135*mm])
    info.setStyle(TableStyle([('BACKGROUND',(0,0),(0,-1),LIGHT),('GRID',(0,0),(-1,-1),0.35,BORDER),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
    story += [info, Spacer(1,4*mm)]

    def f(key, fallback=0):
        return _as_number(p.get(key, fallback), fallback)
    rows = [
        ('Calculated Gross','Earning',f('gross_salary', p.get('gross',0))),
        ('Overtime / Premium Pay','Earning',f('overtime')),
        ('Reimbursable Expenses (Non-taxable)','Earning',f('reimbursable_expenses')),
        ('Bonus','Earning',f('bonus')),
        ('Transport Reimbursement (Tax Free)','Earning',f('transport')),
        ('PAYE Tax','Deduction',f('paye')),
        ('UIF (Employee)','Deduction',f('uif', p.get('uif_emp',0))),
        ('Loan Repayment','Deduction',f('loan_repayment')),
        ('UIF (Employer)','Employer Contribution',f('uif', p.get('uif_er',0))),
        ('SDL (Employer)','Employer Contribution',f('sdl')),
    ]
    body = [[_paragraph('Description', st['body_bold']), _paragraph('Type', st['body_bold']), _paragraph('Amount', st['body_bold'])]]
    for label, typ, val in rows:
        if val == 0 and label not in ('Calculated Gross','PAYE Tax','UIF (Employee)','UIF (Employer)'):
            continue
        body.append([_paragraph(label, st['body']), _paragraph(typ, st['small']), _paragraph(_money_text(val), st['right'])])
    net_val = p.get('net') if p.get('net') not in (None,'') else p.get('net_salary')
    body.append([_paragraph('NET PAY', st['body_bold']), '', _paragraph(_money_text(net_val), st['right'])])
    tbl = Table(body, colWidths=[93*mm, 48*mm, 32*mm], repeatRows=1)
    tbl.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),BLUE),('TEXTCOLOR',(0,0),(-1,0),WHITE),('GRID',(0,0),(-1,-1),0.35,BORDER),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),('LINEABOVE',(0,-1),(-1,-1),0.9,DARK),('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold')]))
    story += [tbl]
    if p.get('adjustment_reason'):
        story += [Spacer(1,4*mm), _paragraph('Adjustment reason', st['section']), _paragraph(p.get('adjustment_reason'), st['body'])]
    if leave_balances:
        story += [Spacer(1,5*mm), _paragraph('Statutory Leave Balances', st['section'])]
        leave_data = [[_paragraph('Annual Leave', st['body']), _paragraph(str(leave_balances.get('annual','N/A')), st['right'])],[_paragraph('Sick Leave',st['body']),_paragraph(str(leave_balances.get('sick','N/A')),st['right'])],[_paragraph('Family Responsibility Leave',st['body']),_paragraph(str(leave_balances.get('family','N/A')),st['right'])]]
        leave_tbl = Table(leave_data, colWidths=[125*mm,48*mm])
        leave_tbl.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),LIGHT),('GRID',(0,0),(-1,-1),0.35,BORDER),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
        story.append(leave_tbl)
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
