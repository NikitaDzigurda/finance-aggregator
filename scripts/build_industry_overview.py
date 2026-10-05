from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets" / "industry-overview"
OUTPUT = ROOT / "docs" / "FINANCE_AGGREGATOR_TECHNICAL_OVERVIEW_RU.docx"

BLACK = "000000"
GRAY = "F2F2F2"
LIGHT_GRAY = "D9D9D9"
MID_GRAY = "A6A6A6"


def set_run_font(run, name: str = "Arial", size: float | None = None, bold: bool | None = None):
    run.font.name = name
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), name)
    run.font.color.rgb = RGBColor(0, 0, 0)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    return run


def set_cell_shading(cell, fill: str):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=100, start=120, bottom=100, end=120):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for m, v in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{m}"))
        if node is None:
            node = OxmlElement(f"w:{m}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(v))
        node.set(qn("w:type"), "dxa")


def set_table_borders(table, color=LIGHT_GRAY, size="6", val="single"):
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = borders.find(qn(f"w:{edge}"))
        if tag is None:
            tag = OxmlElement(f"w:{edge}")
            borders.append(tag)
        tag.set(qn("w:val"), val)
        tag.set(qn("w:sz"), size)
        tag.set(qn("w:space"), "0")
        tag.set(qn("w:color"), color)


def remove_table_borders(table):
    set_table_borders(table, color="FFFFFF", size="0", val="nil")


def set_repeat_table_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def set_cant_split(row):
    tr_pr = row._tr.get_or_add_trPr()
    cant = OxmlElement("w:cantSplit")
    tr_pr.append(cant)


def set_cell_width(cell, width_inches: float):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(int(width_inches * 1440)))
    tc_w.set(qn("w:type"), "dxa")


def set_paragraph_keep(paragraph, keep_next=False, keep_lines=True, page_break_before=False):
    p_pr = paragraph._p.get_or_add_pPr()
    if keep_next:
        p_pr.append(OxmlElement("w:keepNext"))
    if keep_lines:
        p_pr.append(OxmlElement("w:keepLines"))
    if page_break_before:
        p_pr.append(OxmlElement("w:pageBreakBefore"))


def add_field(paragraph, instruction: str):
    run = paragraph.add_run()
    fld_char = OxmlElement("w:fldChar")
    fld_char.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([fld_char, instr, separate, text, end])
    set_run_font(run, size=8.5)


def add_body(doc: Document, text: str, *, bold_lead: str | None = None, keep_next=False):
    p = doc.add_paragraph(style="Body Text")
    if bold_lead and text.startswith(bold_lead):
        set_run_font(p.add_run(bold_lead), bold=True)
        set_run_font(p.add_run(text[len(bold_lead) :]))
    else:
        set_run_font(p.add_run(text))
    set_paragraph_keep(p, keep_next=keep_next)
    return p


def add_bullets(doc: Document, items: list[str]):
    for item in items:
        p = doc.add_paragraph(style="List Bullet")
        set_run_font(p.add_run(item))
        set_paragraph_keep(p)


def add_numbered(doc: Document, items: list[str]):
    for item in items:
        p = doc.add_paragraph(style="List Number")
        set_run_font(p.add_run(item))
        set_paragraph_keep(p)


def add_manual_numbered(doc: Document, items: list[str], start: int = 1):
    for offset, item in enumerate(items):
        p = doc.add_paragraph(style="Body Text")
        p.paragraph_format.left_indent = Inches(0.24)
        p.paragraph_format.first_line_indent = Inches(-0.24)
        set_run_font(p.add_run(f"{start + offset}. "), bold=False)
        set_run_font(p.add_run(item))
        set_paragraph_keep(p)


def add_heading(doc: Document, text: str, level: int = 1, *, new_page=False):
    p = doc.add_heading(text, level=level)
    for run in p.runs:
        set_run_font(run, bold=True)
    set_paragraph_keep(p, keep_next=True, page_break_before=new_page)
    return p


def add_table(doc: Document, headers: list[str], rows: list[list[str]], widths: list[float] | None = None, font_size=9.0):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_table_borders(table)
    header = table.rows[0]
    set_repeat_table_header(header)
    set_cant_split(header)
    for idx, text in enumerate(headers):
        cell = header.cells[idx]
        set_cell_shading(cell, GRAY)
        set_cell_margins(cell, top=110, bottom=110)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        set_run_font(p.add_run(text), size=font_size, bold=True)
        if widths:
            set_cell_width(cell, widths[idx])
    for row_index, values in enumerate(rows):
        row = table.add_row()
        set_cant_split(row)
        for idx, value in enumerate(values):
            cell = row.cells[idx]
            set_cell_margins(cell, top=95, bottom=95)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if row_index % 2 == 1:
                set_cell_shading(cell, "FAFAFA")
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.05
            set_run_font(p.add_run(value), size=font_size)
            if widths:
                set_cell_width(cell, widths[idx])
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(0)
    return table


def add_placeholder(doc: Document, number: int, title: str, instruction: str, height=2.75):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_table_borders(table, color=MID_GRAY, size="8", val="dashed")
    row = table.rows[0]
    row.height = Inches(height)
    row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    set_cant_split(row)
    cell = row.cells[0]
    set_cell_margins(cell, top=220, start=260, bottom=220, end=260)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(p.add_run(f"Место для схемы {number}\n"), size=12, bold=True)
    set_run_font(p.add_run(title), size=11, bold=True)
    detail = cell.add_paragraph()
    detail.alignment = WD_ALIGN_PARAGRAPH.CENTER
    detail.paragraph_format.space_before = Pt(8)
    detail.paragraph_format.line_spacing = 1.1
    set_run_font(detail.add_run(instruction), size=9.5)
    caption = doc.add_paragraph(style="Caption")
    caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(caption.add_run(f"Схема {number}. {title}"), size=8.5)
    set_paragraph_keep(caption)
    return table


def add_figure(doc: Document, filename: str, number: int, caption_text: str, width=6.75):
    path = ASSETS / filename
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    remove_table_borders(table)
    row = table.rows[0]
    set_cant_split(row)
    cell = row.cells[0]
    set_cell_margins(cell, top=0, start=0, bottom=0, end=0)
    pic_p = cell.paragraphs[0]
    pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = pic_p.add_run()
    inline = run.add_picture(str(path), width=Inches(width))
    doc_pr = inline._inline.docPr
    doc_pr.set("name", f"Figure {number}")
    doc_pr.set("descr", caption_text)
    cap = cell.add_paragraph()
    cap.style = doc.styles["Caption"]
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_before = Pt(5)
    cap.paragraph_format.space_after = Pt(5)
    set_run_font(cap.add_run(f"Рисунок {number}. {caption_text}"), size=8.5, bold=False)
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(0)
    spacer.paragraph_format.line_spacing = 0.5
    return table


def add_page_break(doc: Document):
    p = doc.add_paragraph()
    p.add_run().add_break(WD_BREAK.PAGE)


def configure_styles(doc: Document):
    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Arial"
    normal._element.rPr.rFonts.set(qn("w:ascii"), "Arial")
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), "Arial")
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor(0, 0, 0)

    body = styles["Body Text"]
    body.font.name = "Arial"
    body._element.rPr.rFonts.set(qn("w:ascii"), "Arial")
    body._element.rPr.rFonts.set(qn("w:hAnsi"), "Arial")
    body.font.size = Pt(10.5)
    body.font.color.rgb = RGBColor(0, 0, 0)
    body.paragraph_format.space_after = Pt(6)
    body.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    body.paragraph_format.line_spacing = 1.13

    title = styles["Title"]
    title.font.name = "Arial"
    title._element.rPr.rFonts.set(qn("w:ascii"), "Arial")
    title._element.rPr.rFonts.set(qn("w:hAnsi"), "Arial")
    title.font.size = Pt(27)
    title.font.bold = True
    title.font.color.rgb = RGBColor(0, 0, 0)
    title.paragraph_format.space_after = Pt(10)
    title_ppr = title._element.get_or_add_pPr()
    for border in title_ppr.findall(qn("w:pBdr")):
        title_ppr.remove(border)

    subtitle = styles["Subtitle"]
    subtitle.font.name = "Arial"
    subtitle._element.rPr.rFonts.set(qn("w:ascii"), "Arial")
    subtitle._element.rPr.rFonts.set(qn("w:hAnsi"), "Arial")
    subtitle.font.size = Pt(14)
    subtitle.font.italic = False
    subtitle.font.color.rgb = RGBColor(0, 0, 0)
    subtitle.paragraph_format.space_after = Pt(18)

    for name, size, before, after in (
        ("Heading 1", 17, 12, 8),
        ("Heading 2", 13, 10, 5),
        ("Heading 3", 11, 8, 4),
    ):
        style = styles[name]
        style.font.name = "Arial"
        style._element.rPr.rFonts.set(qn("w:ascii"), "Arial")
        style._element.rPr.rFonts.set(qn("w:hAnsi"), "Arial")
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    caption = styles["Caption"]
    caption.font.name = "Arial"
    caption._element.rPr.rFonts.set(qn("w:ascii"), "Arial")
    caption._element.rPr.rFonts.set(qn("w:hAnsi"), "Arial")
    caption.font.size = Pt(8.5)
    caption.font.italic = False
    caption.font.bold = False
    caption.font.color.rgb = RGBColor(0, 0, 0)
    caption.paragraph_format.space_after = Pt(8)

    for name in ("List Bullet", "List Number"):
        style = styles[name]
        style.font.name = "Arial"
        style._element.rPr.rFonts.set(qn("w:ascii"), "Arial")
        style._element.rPr.rFonts.set(qn("w:hAnsi"), "Arial")
        style.font.size = Pt(10.5)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.paragraph_format.space_after = Pt(3)
        style.paragraph_format.line_spacing = 1.08


def configure_sections(doc: Document):
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.68)
    section.bottom_margin = Inches(0.82)
    section.left_margin = Inches(0.72)
    section.right_margin = Inches(0.72)
    section.header_distance = Inches(0.3)
    section.footer_distance = Inches(0.3)


def add_footer(section):
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    set_run_font(p.add_run("Finance Aggregator  |  Техническое описание  |  "), size=8.5)
    add_field(p, "PAGE")


def build_document():
    doc = Document()
    configure_sections(doc)
    configure_styles(doc)
    add_footer(doc.sections[0])
    doc.core_properties.title = "Finance Aggregator Техническое описание сервиса"
    doc.core_properties.subject = "Архитектура текущее состояние и план развития"
    doc.core_properties.author = "Finance Aggregator"
    doc.core_properties.keywords = "portfolio accounting, ledger, broker import, CEX, analytics"
    doc.core_properties.comments = "Техническое описание сервиса"

    # Cover
    p = doc.add_paragraph(style="Title")
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    set_run_font(p.add_run("Finance Aggregator"), size=27, bold=True)
    p2 = doc.add_paragraph(style="Subtitle")
    p2.alignment = WD_ALIGN_PARAGRAPH.LEFT
    set_run_font(p2.add_run("Техническое описание сервиса"), size=16)
    intro = doc.add_paragraph(style="Body Text")
    intro.paragraph_format.space_after = Pt(14)
    set_run_font(
        intro.add_run(
            "Локальная система консолидации брокерских счетов и криптоактивов с трассируемым импортом, точным Ledger и объяснимой аналитикой"
        ),
        size=11.5,
    )
    add_figure(
        doc,
        "01-overview.png",
        1,
        "Главный экран текущей реализации на искусственном демонстрационном портфеле. Снимок показывает сохранённую оценку, показатели P&L, фактическое распределение и предупреждения о неполных данных.",
        width=6.75,
    )
    meta = doc.add_table(rows=3, cols=2)
    meta.alignment = WD_TABLE_ALIGNMENT.LEFT
    meta.autofit = False
    set_table_borders(meta)
    meta_rows = [
        ("Назначение", "Материал для обсуждения с представителями финансовой индустрии и техническими специалистами"),
        ("Состояние проекта", "Phases 01–05 завершены. Phase 06 подготовлена как следующий этап и ещё не реализована"),
        ("Дата актуальности", "16 сентября 2026 года"),
    ]
    for idx, (k, v) in enumerate(meta_rows):
        for c in meta.rows[idx].cells:
            set_cell_margins(c, top=80, bottom=80)
        set_cell_width(meta.rows[idx].cells[0], 1.45)
        set_cell_width(meta.rows[idx].cells[1], 5.25)
        set_cell_shading(meta.rows[idx].cells[0], GRAY)
        set_run_font(meta.rows[idx].cells[0].paragraphs[0].add_run(k), size=8.8, bold=True)
        set_run_font(meta.rows[idx].cells[1].paragraphs[0].add_run(v), size=8.8)

    add_page_break(doc)

    # Executive summary
    add_heading(doc, "Краткое резюме", 1)
    add_body(
        doc,
        "Finance Aggregator объединяет историю инвестиционных операций из брокерских отчётов и выгрузок централизованных криптобирж. Система приводит разные форматы к одному неизменяемому Ledger, рассчитывает позиции и финансовый результат на сервере и показывает не только итоговые суммы, но и границы их достоверности.",
    )
    add_body(
        doc,
        "На 16 сентября 2026 года реализован полный локальный пользовательский путь: создание портфеля и счетов, загрузка отчётов, проверка неоднозначностей, атомарное подтверждение, пересчёт, текущая аналитика, фактическое распределение, временные ряды событий и диагностика качества данных. Для пяти проверенных криптоактивов работают автоматические глобальные цены CoinPaprika; дневной курс USD/RUB получает отдельный worker из официального источника Банка России. Российские ценные бумаги используют ручные цены до появления разрешённого и экономически оправданного источника.",
    )
    add_heading(doc, "Главный вывод", 2)
    add_body(
        doc,
        "Проект уже является работающим однопользовательским локальным приложением, а не макетом. Он закрывает ручную консолидацию файлов и текущую оценку портфеля, но пока не подключается к приватным API инвестиционных счетов, не строит воспроизводимую ежедневную историю стоимости и не предназначен для сетевого многопользовательского размещения.",
    )
    add_heading(doc, "Что получает читатель", 2)
    add_bullets(
        doc,
        [
            "описание продуктовой границы и пользовательского сценария без инвестиционных обещаний",
            "архитектурные и расчётные решения, влияющие на точность и аудит",
            "состав реализованных файловых адаптеров и правила сверки",
            "состояние интерфейса с актуальными снимками экранов",
            "ограничения текущей версии и проверяемый план следующего этапа",
        ],
    )
    add_heading(doc, "Навигация по документу", 2)
    add_table(
        doc,
        ["Раздел", "Содержание"],
        [
            ["1–3", "Назначение, границы, текущая готовность и пользовательский путь"],
            ["4–8", "Архитектура, доменная модель, импорт, расчёты и аналитика"],
            ["9–12", "Рыночные данные, API, безопасность и эксплуатация"],
            ["13–15", "Проверки, ограничения и план развития"],
            ["Приложения", "Карта API, словарь и задания на внешние схемы"],
        ],
        widths=[1.2, 5.5],
    )

    # 1
    add_heading(doc, "1 Назначение и продуктовая граница", 1, new_page=True)
    add_body(
        doc,
        "Сервис отвечает на практический вопрос владельца нескольких инвестиционных счетов: как свести операции, позиции и денежные остатки из неоднородных источников так, чтобы итог можно было проверить. Он ориентирован на брокерские активы и spot-криптоактивы. Обычные банковские счета, карты, вклады и покупки не входят в продуктовую область.",
    )
    add_heading(doc, "Поддерживаемые объекты", 2)
    add_bullets(
        doc,
        [
            "портфели с базовой валютой RUB или USD",
            "брокерские и CEX-счета",
            "акции, облигации, ETF и фонды на уровне канонической модели",
            "spot-криптоактивы, комиссии в отдельном активе, вводы и выводы",
            "денежные остатки, цены, валютные курсы, категории и фактические доли",
        ],
    )
    add_heading(doc, "Осознанные исключения", 2)
    add_bullets(
        doc,
        [
            "торговые поручения, автоматическая ребалансировка и вывод средств",
            "персональные инвестиционные или налоговые рекомендации",
            "банковские продукты, кошельки и DeFi",
            "опционы и производные инструменты в расширенной аналитике",
            "публичный SaaS, роли пользователей и сетевое размещение",
            "график ежедневной стоимости, TWR, XIRR, Sharpe, Sortino и прогнозы",
        ],
    )
    add_heading(doc, "Принципы доверия", 2)
    add_body(
        doc,
        "Неизвестное значение не подменяется нулём. Остаток из отчёта не превращается в вымышленную покупку. Текущая цена не восстанавливает отсутствующую историю приобретения. Несовпадение при сверке остаётся диагностикой, а не скрытой корректировкой. Эти правила важнее полноты красивого итогового экрана.",
    )

    # 2
    add_heading(doc, "2 Текущее состояние реализации", 1, new_page=True)
    add_table(
        doc,
        ["Этап", "Состояние", "Фактический результат"],
        [
            ["Phase 01", "Завершена", "Локальный backend, точные типы, Ledger, универсальный CSV, импортный цикл, расчёт позиций и P&L"],
            ["Phase 02", "Завершена", "Т Инвестиции XLSX, Альфа Инвестиции XML, Bybit Spot CSV bundle, reconciliation и двухактивные криптосделки"],
            ["Phase 03", "Завершена", "RUB и USD аналитика, цены и FX, allocation, события Ledger, единый contract качества данных"],
            ["Phase 04", "Завершена", "React frontend, generated OpenAPI client, onboarding, import review, overview, holdings, allocation, events и quality"],
            ["Phase 05", "Завершена 16.09.2026", "Автоматические глобальные USD цены CoinPaprika для BTC, ETH, SOL, USDT и USDC, selective recalculation и тихое обновление UI"],
            ["Phase 06", "Запланирована", "Read-only подключения одного брокера и одной CEX с initial preview, incremental sync и безопасным хранением credentials"],
        ],
        widths=[0.9, 1.25, 4.55],
        font_size=8.7,
    )
    add_heading(doc, "Состав работающей версии", 2)
    add_bullets(
        doc,
        [
            "FastAPI API и интерактивная OpenAPI документация",
            "Python worker с очередью PostgreSQL и автоматическими задачами FX и market data",
            "PostgreSQL с Alembic миграциями и точными NUMERIC полями",
            "React 19 и TypeScript 5.9 frontend с generated OpenAPI client",
            "локальное объектное хранилище исходных импортированных файлов за интерфейсом ObjectStorage",
            "Docker Compose для согласованного локального запуска четырёх процессов",
        ],
    )
    add_body(
        doc,
        "C++ модуль не создан. Измерения Python ядра до 100 000 искусственных операций не показали узкого места, которое оправдало бы вторую реализацию точной десятичной арифметики.",
    )

    # 3
    add_heading(doc, "3 Пользовательский путь", 1, new_page=True)
    add_numbered(
        doc,
        [
            "Пользователь создаёт портфель и брокерский либо CEX счёт.",
            "Выбирает поддерживаемый формат и загружает исходный отчёт без предварительного преобразования.",
            "Система определяет внутреннюю структуру файла, нормализует строки и выполняет сверки.",
            "Пользователь видит только финансово значимые вопросы: неоднозначный инструмент, возможный дубль или нарушение связности операции.",
            "Подтверждение одной транзакцией записывает канонические операции и запускает пересчёт.",
            "Обзор показывает текущую стоимость, себестоимость, P&L, фактическое распределение и причины неполноты.",
            "Новые сохранённые курсы или поддерживаемые криптоцены делают старый snapshot устаревшим до следующего пересчёта; интерфейс показывает это состояние явно.",
        ],
    )
    add_figure(
        doc,
        "01-overview.png",
        2,
        "Обзор портфеля после пересборки текущей версии. Состояние «Расчёт устарел» возникло после появления новых сохранённых входных данных и показывает, что сервер не выдаёт старый snapshot за актуальный.",
        width=6.75,
    )

    # 4
    add_heading(doc, "4 Архитектура", 1, new_page=True)
    add_body(
        doc,
        "Реализация остаётся модульным монолитом с отдельными процессами API, worker, frontend и PostgreSQL. Доменные границы определены в коде и прикладных интерфейсах, но проект не вводит микросервисную инфраструктуру до появления измеримой необходимости.",
    )
    add_placeholder(
        doc,
        1,
        "Контейнерная архитектура в нотации C4",
        "Показать браузер, frontend static server, FastAPI API, Python worker, PostgreSQL, ObjectStorage и внешние read-only источники Банк России и CoinPaprika. Отметить границы локальной установки, HTTP через /api/v1, очередь заданий в PostgreSQL и отсутствие прямого доступа browser к БД и storage.",
        height=2.6,
    )
    add_heading(doc, "Распределение ответственности", 2)
    add_table(
        doc,
        ["Компонент", "Ответственность"],
        [
            ["Frontend", "Навигация, формы, отображение точных строковых значений, complete partial unavailable states и безопасных diagnostics"],
            ["FastAPI", "Публичный /api/v1, валидация, orchestration прикладных сервисов, OpenAPI и единый error envelope"],
            ["Worker", "Разбор импорта, валидация, FX sync, market data sync и пересчёт без сетевых вызовов из analytics endpoints"],
            ["PostgreSQL", "Ledger, staging, jobs, pricing observations, snapshots, metadata и ограничения целостности"],
            ["ObjectStorage", "Оригинальные файлы под случайными ключами, SHA 256 и трассируемость источника"],
        ],
        widths=[1.3, 5.4],
        font_size=9.0,
    )
    add_body(
        doc,
        "Frontend является клиентом публичного API. Он не читает ORM, не обращается к PostgreSQL и не рассчитывает стоимость, доли или P&L. Recharts используется только для геометрии уже рассчитанных рядов; рядом доступно точное табличное представление.",
    )

    # 5
    add_heading(doc, "5 Доменная модель и источник истины", 1, new_page=True)
    add_body(
        doc,
        "Основой служит неизменяемый канонический Ledger. Общая оболочка операции хранит портфель, счёт, время, точность времени, источник и audit timestamps. Экономический смысл находится в типизированном payload. Исправление создаёт новую correction operation и не переписывает подтверждённую запись.",
    )
    add_placeholder(
        doc,
        2,
        "Доменная модель в UML или ER нотации",
        "Включить Portfolio, Account, Instrument и InstrumentIdentifier, Operation, ImportBatch, ImportRow, ImportResolution, MarketPrice, MarketMapping, ExchangeRate, CalculationSnapshot, PositionSnapshot, AllocationCategory и InstrumentCategoryOverride. Показать связи import row с operation, snapshot с использованными price observations и ограничения ownership по portfolio и account.",
        height=2.65,
    )
    add_heading(doc, "Канонические типы событий", 2)
    add_table(
        doc,
        ["Группа", "Типы", "Назначение"],
        [
            ["Торговля", "trade, crypto_trade", "Покупка или продажа ценной бумаги и обмен двух криптоактивов"],
            ["Доходы и расходы", "income, fee, tax", "Дивиденды, купоны, комиссии в валюте или активе и налоги"],
            ["Движения", "cash_movement, crypto_transfer, currency_exchange", "Внешние вводы и выводы, переводы и обмен валют"],
            ["События актива", "corporate_action, bond_redemption", "Корпоративные события и погашение"],
            ["Явная корректировка", "balance_adjustment", "Только при установленном основании; не используется для скрытого устранения расхождений"],
        ],
        widths=[1.2, 1.8, 3.7],
        font_size=8.7,
    )
    add_heading(doc, "Точность", 2)
    add_body(
        doc,
        "Money, Quantity и Price поддерживают до 38 цифр с не более чем 18 дробными знаками; Rate допускает до 24 дробных знаков. Python использует Decimal, PostgreSQL — NUMERIC, а JSON передаёт значения строками. JSON numbers, exponent notation и значения, требующие неявного округления, отклоняются.",
    )

    # 6 Import
    add_heading(doc, "6 Импорт и трассируемость", 1, new_page=True)
    add_body(
        doc,
        "Импорт разделён на staging и commit. Адаптер определяет формат, разбирает файл и возвращает кандидаты операций с диагностикой. До подтверждения эти записи не влияют на Ledger. Исходный файл и исходная строка остаются связаны с нормализованным результатом.",
    )
    add_figure(
        doc,
        "03-imports.png",
        3,
        "Журнал импортов текущего искусственного портфеля. Видны источник, отчётный период, число разобранных строк и итоговый статус каждого batch.",
        width=6.75,
    )
    add_placeholder(
        doc,
        3,
        "BPMN процесса импорта",
        "Рекомендуемые дорожки: Пользователь, Browser, API, Worker, ObjectStorage и PostgreSQL. События: upload, проверка размера и сигнатуры, SHA 256 deduplication, detect, parse, validate, reconciliation, preview, manual resolution, atomic confirm, recalculation и optional rollback. Отдельно показать terminal failure и идемпотентный повтор confirm.",
        height=2.65,
    )
    add_heading(doc, "Поддерживаемые файловые форматы", 2)
    add_table(
        doc,
        ["Формат", "Состав", "Ключевые правила"],
        [
            ["Universal broker CSV 1.0", "Один CSV", "UTF 8, несколько разделителей и локальные decimal форматы. Неизвестный инструмент не создаётся автоматически"],
            ["Т Инвестиции XLSX 1.0", "Один XLSX", "Историческая одно листовая и актуальная много листовая структуры. Сделки и fee идут в Ledger, остатки — только в reconciliation"],
            ["Альфа Инвестиции XML 1.0", "Один XML для импорта", "DTD и entities запрещены. Trade linked money не дублируется. Transfers сохраняют неизвестную basis"],
            ["Bybit Spot CSV bundle 1.0", "Четыре связанных CSV", "Spot execution связывается с двумя asset legs и фактическим fee asset. Внутренние transfers и withdrawal не дублируются"],
        ],
        widths=[1.45, 1.15, 4.1],
        font_size=8.5,
    )
    add_heading(doc, "Граница автоматической обработки", 2)
    add_body(
        doc,
        "Поддержанный официальный отчёт должен проходить happy path без ручного исключения каждой технической строки. Review требуется только там, где автоматическое решение может изменить финансовый смысл: при неоднозначном инструменте, нарушенной связи ног сделки, возможном дубле или ошибке структуры.",
    )
    add_heading(doc, "Безопасность исходных файлов", 2)
    add_body(
        doc,
        "Upload проверяет размер, расширение, content type и сигнатуру до сохранения. Storage key генерируется сервером и не публикуется. Для XML ограничения применяются повторно в worker, а metadata Bybit UID удаляется из staging raw data и diagnostics.",
    )

    # 7 Review
    add_heading(doc, "7 Проверка подтверждение и откат", 1, new_page=True)
    add_figure(
        doc,
        "04-import-review.png",
        4,
        "Экран проверки подтверждённого Bybit batch. Технические и reconciliation строки исключены из Ledger, а действие отката доступно отдельно.",
        width=6.75,
    )
    add_heading(doc, "Дедупликация", 2)
    add_body(
        doc,
        "Приоритет отдан устойчивому source operation ID в контексте provider и account. Если его нет, используется fingerprint нормализованного экономического содержания с типом операции, временем и его точностью, инструментами, количествами, валютами и комиссиями. Повторная загрузка одинакового набора файлов на тот же счёт возвращает существующий batch.",
    )
    add_heading(doc, "Атомарность и идемпотентность", 2)
    add_body(
        doc,
        "Confirm блокирует batch и строки, повторно валидирует кандидаты и записывает все операции одной транзакцией. Ошибка в середине не оставляет частичный импорт. Повторный confirm возвращает существующий результат. Rollback затрагивает только операции выбранного batch и запускает пересчёт; явные correction dependencies блокируют разрушительный откат.",
    )
    add_heading(doc, "Reconciliation", 2)
    add_body(
        doc,
        "Для периодического отчёта система сравнивает opening и closing controls с экономическими эффектами строк того же периода. Overlap duplicates участвуют в сверке отчёта, но не дублируются в Ledger. Mismatch не порождает скрытый balance adjustment.",
    )

    # 8 Calculation
    add_heading(doc, "8 Расчёт позиций и аналитика", 1, new_page=True)
    add_body(
        doc,
        "Чистое Python ядро получает нормализованные операции, цены и policy. Оно не знает о HTTP, файлах или ORM. Результат включает позиции, денежные остатки, среднюю себестоимость, realised и unrealised P&L, income, fees, taxes и structured diagnostics.",
    )
    add_heading(doc, "Методика", 2)
    add_table(
        doc,
        ["Показатель", "Правило"],
        [
            ["Количество", "Сумма точных экономических ног операции в хронологическом и детерминированном порядке"],
            ["Себестоимость", "Средневзвешенная стоимость оставшейся позиции; неизвестная история не заменяется нулём"],
            ["Realised P&L", "Разница между выручкой и списанной средней себестоимостью закрытой части позиции"],
            ["Unrealised P&L", "Текущая оценка открытой позиции минус известная себестоимость"],
            ["Текущая оценка", "Последняя допустимая price observation с observed_at не позднее valuation_as_of"],
            ["Консолидация валют", "Direct, inverse или один pivot через RUB. Отсутствующий курс не заменяется единицей"],
        ],
        widths=[1.5, 5.2],
        font_size=8.9,
    )
    add_body(
        doc,
        "В расчёте различаются complete, partial и unavailable. Например, при известном количестве и цене, но неизвестной истории приобретения текущая стоимость доступна, а себестоимость и зависимый P&L остаются частичными или недоступными.",
    )
    add_heading(doc, "Контрольный пример", 2)
    add_body(
        doc,
        "Эталон buy 10 по 100, затем buy 10 по 200 и sell 5 по 180 оставляет quantity 15 и average cost 150. Realised P&L равен 150; при текущей цене 210 unrealised P&L равен 900. Этот сценарий закрепляет знаки и метод средневзвешенной себестоимости.",
    )
    add_body(
        doc,
        "Crypto trade переносит известную basis между двумя asset legs без искусственного realised P&L. Комиссия в активе уменьшает соответствующую позицию отдельно и не меняет торговый notional.",
    )
    add_page_break(doc)
    add_heading(doc, "Активы в интерфейсе", 2)
    add_figure(
        doc,
        "02-holdings.png",
        5,
        "Таблица активов. Каждая строка показывает счёт, категорию, точное количество, исходную валюту, себестоимость, рыночную стоимость и финансовый результат.",
        width=6.75,
    )

    # 9 Allocation
    add_heading(doc, "9 Фактическое распределение", 1, new_page=True)
    add_figure(
        doc,
        "05-allocation.png",
        6,
        "Фактическое распределение по системным категориям. Значения и доли рассчитаны backend относительно известной стоимости; предупреждения сохраняют сведения о неполной истории приобретения и отрицательной позиции.",
        width=6.75,
    )
    add_body(
        doc,
        "Семь системных классов имеют устойчивые идентификаторы: equity, fixed income, fund, derivative, crypto, cash и other. Пользователь может создать категорию внутри портфеля и назначить инструменту override, не меняя глобальный справочник и другие портфели.",
    )
    add_body(
        doc,
        "Целевые доли и deviation удалены из публичного контракта после уточнения MVP. Сервис показывает фактическое состояние и концентрацию, но не формирует предложение по ребалансировке. Отрицательные позиции сохраняют знак; круговая диаграмма не должна маскировать leverage.",
    )

    # 10 market data
    add_heading(doc, "10 Цены и валютные курсы", 1, new_page=True)
    add_body(
        doc,
        "Price и FX observations хранятся append only. Для каждого наблюдения фиксируются источник, provider, вид цены, валюта, observed_at, fetched_at и качество времени. Расчётная позиция сохраняет ссылку на использованное наблюдение, поэтому происхождение оценки можно восстановить.",
    )
    add_heading(doc, "Автоматические источники", 2)
    add_table(
        doc,
        ["Источник", "Данные", "Текущая граница"],
        [
            ["Банк России", "Официальный дневной USD/RUB", "Worker получает системный курс без портфельных или пользовательских данных. Ручной fallback сохранён"],
            ["CoinPaprika Free", "Глобальная агрегированная цена одной криптоединицы в USD", "BTC, ETH, SOL, USDT и USDC при verified mapping. Интервал по умолчанию один час"],
            ["Ручной ввод", "Цена инструмента или fiat FX", "Используется для российских бумаг и остальных неподдерживаемых активов"],
        ],
        widths=[1.3, 2.4, 3.0],
        font_size=8.8,
    )
    add_placeholder(
        doc,
        4,
        "Последовательность автоматического обновления цены",
        "UML sequence: Scheduler → PostgreSQL queue → Worker → CoinPaprika global snapshot → mapping validation → append only market prices → affected portfolio selection → calculation service → snapshot → frontend polling. Показать, что наружу не передаются состав портфеля, счета, количества, операции и пользовательские идентификаторы.",
        height=2.65,
    )
    add_heading(doc, "Почему не включены автоматические цены российских бумаг", 2)
    add_body(
        doc,
        "Проектная проверка зафиксировала, что программная обработка и накопление данных Московской биржи требуют отдельного договорного режима. Поэтому наличие публичного URL не было принято за достаточное право использования. Это ограничение продукта, а не техническая неспособность добавить HTTP адаптер.",
    )

    # 11 events quality
    add_heading(doc, "11 События и качество данных", 1, new_page=True)
    add_figure(
        doc,
        "06-events.png",
        7,
        "Временной ряд событий Ledger. График показывает пополнения и выводы в исходных единицах и не выдаётся за историю стоимости или доходности портфеля.",
        width=6.75,
    )
    add_body(
        doc,
        "Cash flow, income, costs и trading строятся как воспроизводимый replay подтверждённых операций. События группируются по локальным календарным границам IANA timezone, но не объединяются между валютами и активами с помощью текущего курса. Диапазон одного запроса ограничен 366 днями.",
    )
    add_page_break(doc)
    add_heading(doc, "Экран качества", 2)
    add_figure(
        doc,
        "07-data-quality.png",
        8,
        "Экран качества и готовности данных. Показаны устаревший расчёт, неизвестная себестоимость, ограниченный отчётный период, отрицательная позиция и доступные действия с ценами и FX.",
        width=6.75,
    )
    add_body(
        doc,
        "Public diagnostics содержат стабильный code, severity, count и impacts, но не включают исходные строки, финансовые значения, номера счетов или storage keys. Provenance сообщает версию расчётного контракта, время snapshot и времена реально использованных observations.",
    )

    # 12 API
    add_heading(doc, "12 Публичный API", 1, new_page=True)
    add_body(
        doc,
        "Все пользовательские функции frontend выполняются через публичный /api/v1. Контракты определены Pydantic schemas и экспортируются в OpenAPI. TypeScript типы генерируются из схемы, а отдельная проверка отклоняет drift между backend и frontend.",
    )
    add_table(
        doc,
        ["Группа", "Основные ресурсы"],
        [
            ["Справочники", "/portfolios, /accounts, /instruments"],
            ["Ledger", "/operations с фильтрами по счёту, типу, периоду и инструменту"],
            ["Импорт", "/imports, /preview, /rows, /confirm, /rollback, /import-formats"],
            ["Pricing", "/prices, /fx-rates, /market-data/sync/latest, /market-mappings"],
            ["Расчёт", "/positions и /positions/recalculate"],
            ["Аналитика", "/analytics/overview, /holdings, /breakdown, /exposure, /data-quality"],
            ["Распределение", "/allocation, /allocation/categories и instrument overrides"],
            ["События", "/analytics/cash-flows, /income, /costs и /trading"],
        ],
        widths=[1.35, 5.35],
        font_size=8.9,
    )
    add_heading(doc, "Ошибки и точные значения", 2)
    add_body(
        doc,
        "HTTP ошибки используют единый envelope с code, message и безопасными details. Ошибочное входное значение не копируется в ответ. Точные деньги, количество, цена, курс и вес передаются десятичными строками. Коллекции в empty state остаются массивами; null используется только для действительно недоступной метрики.",
    )
    add_heading(doc, "Совместимость", 2)
    add_body(
        doc,
        "Публичный API стабилизирован для текущего frontend, но сервис пока не объявлен внешней платформой. Перед интеграцией стороннего клиента потребуется формализовать versioning policy, deprecation window, authentication и rate limits.",
    )

    # 13 Security
    add_heading(doc, "13 Безопасность и приватность", 1, new_page=True)
    add_table(
        doc,
        ["Область", "Реализованная мера", "Текущая граница"],
        [
            ["Файлы", "Размер, extension, content type и сигнатура проверяются до storage; имя не используется как путь", "Локальное файловое хранилище"],
            ["XML", "DTD и внешние entities запрещены; лимиты глубины и числа элементов применяются до полного дерева", "Только поддержанный XML для импорта"],
            ["Логи", "Полные строки отчётов, токены и персональные финансовые значения не выводятся", "Нет централизованного production SIEM"],
            ["Сеть", "Analytics endpoints не выполняют внешние запросы; worker ходит только на фиксированные provider hosts", "Нет TLS ingress и сетевого deployment"],
            ["Данные", "Тесты и документация используют synthetic fixtures и отдельные БД", "Рабочая установка рассчитана на одного доверенного владельца"],
            ["Credentials", "В текущей версии пользовательские provider credentials не используются", "CredentialStore является задачей Phase 06"],
        ],
        widths=[1.0, 3.65, 2.05],
        font_size=8.4,
    )
    add_heading(doc, "Что потребуется перед сетевым размещением", 2)
    add_bullets(
        doc,
        [
            "identity и authentication",
            "authorization и tenant isolation",
            "TLS termination, CSRF и сетевые политики",
            "encrypted backups и регламент восстановления",
            "аудит доступа к credentials и чувствительным объектам",
            "формализованный жизненный цикл исходных отчётов",
        ],
    )

    # 14 Operations and QA
    add_heading(doc, "14 Эксплуатация и проверки", 1, new_page=True)
    add_heading(doc, "Локальный запуск", 2)
    add_body(
        doc,
        "Команда docker compose up --build запускает PostgreSQL, API, worker и frontend. По умолчанию пользовательский интерфейс доступен на localhost:5173, API на localhost:8000, Swagger на localhost:8000/docs, а liveness и readiness — на /health/live и /health/ready.",
    )
    add_heading(doc, "Проверенные зоны высокого риска", 2)
    add_bullets(
        doc,
        [
            "round trip Decimal через API и PostgreSQL без потери представления",
            "weighted average basis и P&L, включая CEX trade с двумя asset legs",
            "двойной confirm, duplicate upload, overlap периодов и atomic rollback",
            "T Инвестиции, Альфа Инвестиции и Bybit end to end workflows",
            "market mapping, as of price selection, idempotent snapshot ingestion и selective recalculation",
            "frontend complete, partial, unavailable, empty и persistent import states",
        ],
    )
    add_heading(doc, "Измерения", 2)
    add_table(
        doc,
        ["Сценарий", "Наблюдение", "Интерпретация"],
        [
            ["Calculation contract v2", "100 000 synthetic operations, median 0.354798 s", "Локальная контрольная точка, не SLA"],
            ["Analytics Phase 03", "Overview около 16 ms, allocation около 16 ms, trading около 8 ms", "Семь локальных прогонов на mixed synthetic portfolio"],
            ["Market price storage", "43 800 observations записаны за 1.109 s, размер с индексами около 19 MB", "Искусственный годовой ряд пяти активов"],
            ["CoinPaprika snapshot", "Live серии 0.33–2.48 s, ответ около 1.63 MB", "Одно окружение и кешированные ответы; не гарантия latency"],
        ],
        widths=[1.55, 2.4, 2.75],
        font_size=8.5,
    )
    add_body(
        doc,
        "Полное покрытие не является целью. Проверки сосредоточены на точности денег, идемпотентности, дедупликации, rollback, форматах, финансовых инвариантах и одном сквозном пользовательском пути.",
    )

    # 15 Limitations and roadmap
    add_heading(doc, "15 Ограничения и план развития", 1, new_page=True)
    add_heading(doc, "Текущие ограничения", 2)
    add_bullets(
        doc,
        [
            "автоматическая цена доступна только для пяти проверенных криптоактивов; российские бумаги используют ручной ввод",
            "ограниченный отчётный период не доказывает полную историю счёта и может оставлять basis неизвестной",
            "нет воспроизводимой ежедневной исторической стоимости портфеля и performance metrics",
            "нет приватных read only API брокеров или CEX и безопасного хранилища пользовательских credentials",
            "нет corporate actions engine полного охвата и налоговой отчётности",
            "локальный однопользовательский режим нельзя считать готовой production security model",
        ],
    )
    add_heading(doc, "Следующий этап Phase 06", 2)
    add_body(
        doc,
        "Phase 06 должна уменьшить зависимость от ручных файлов. Владелец локальной установки подключит один брокерский и один CEX Spot счёт с минимальными read only правами. Initial backfill пройдёт preview и одно подтверждение; последующие точные события смогут поступать по incremental sync.",
    )
    add_manual_numbered(
        doc,
        [
            "Сравнить официальные API, условия использования, scopes, глубину истории, cursors, limits и sandbox; только после этого выбрать поставщиков.",
            "Ввести connection и CredentialStore так, чтобы дамп PostgreSQL или frontend не раскрывал secret.",
            "Добавить sync run, cursor, overlap window и идемпотентное продвижение watermark.",
            "Нормализовать executions, actual fees и движения в существующий Ledger, не принимая provider P&L за канонический результат.",
            "Сохранять balances и positions только как reconciliation snapshots без скрытых adjustments.",
            "Определить measurable coverage для будущей исторической аналитики и отдельно спланировать daily value, TWR и XIRR.",
        ],
    )
    add_heading(doc, "Критерий архитектурной непрерывности", 2)
    add_body(
        doc,
        "Файловый импорт и API sync должны давать совместимый Ledger. Pricing и account data остаются независимыми контурами: публичная цена не требует пользовательского CEX key, а личное исполнение не используется как общая котировка.",
    )

    # Appendices
    add_heading(doc, "Приложение А Задания на внешние схемы", 1, new_page=True)
    add_table(
        doc,
        ["Номер", "Нотация", "Что должно быть отражено"],
        [
            ["1", "C4 Container", "Процессы, хранилища, внешние источники, сетевые направления и trust boundary локальной установки"],
            ["2", "UML class или ER", "Ключевые сущности, cardinality, ownership, provenance и immutable ссылки price snapshot"],
            ["3", "BPMN 2.0", "Import workflow по дорожкам с review, idempotent confirm, failure и rollback"],
            ["4", "UML sequence", "Автоматический market data sync и selective recalculation без передачи портфельных данных provider"],
        ],
        widths=[0.6, 1.3, 4.8],
        font_size=8.8,
    )
    add_body(
        doc,
        "Для всех схем рекомендуется сохранять чёрно белую палитру, использовать один уровень детализации на рисунок и подписывать границы ответственности, а не только названия технологий. После вставки следует обновить подписи и ссылки в тексте без изменения нумерации разделов.",
    )
    add_heading(doc, "Приложение Б Краткий словарь", 1)
    add_table(
        doc,
        ["Термин", "Определение в этом проекте"],
        [
            ["Ledger", "Неизменяемый реестр подтверждённых экономических событий"],
            ["Staging", "Промежуточные строки импорта до влияния на финансовую историю"],
            ["Reconciliation", "Сверка рассчитанного эффекта с контрольными остатками источника"],
            ["Snapshot", "Сохранённый результат расчёта портфеля на конкретный момент"],
            ["Observation", "Неизменяемое наблюдение цены или курса с источником и временем"],
            ["Provenance", "Сведения, позволяющие восстановить происхождение операции или использованного значения"],
            ["Coverage", "Известная и исключённая часть расчёта без подмены пропуска нулём"],
            ["Idempotency", "Повтор операции не создаёт дополнительного эффекта после первого успешного выполнения"],
        ],
        widths=[1.35, 5.35],
        font_size=8.9,
    )
    add_heading(doc, "Приложение В Контроль актуальности", 1)
    add_body(
        doc,
        "Документ отражает состояние исходного кода и проектных решений на 16 сентября 2026 года. Перед внешней отправкой после существенных изменений следует проверить README, ARCHITECTURE, IMPORT DESIGN, активный PHASE файл и журнал решений, а также переснять экраны на synthetic портфеле.",
    )
    add_heading(doc, "Материалы проекта", 2)
    add_table(
        doc,
        ["Материал", "Назначение"],
        [
            ["README", "Текущий результат, локальный запуск, API и эксплуатационные команды"],
            ["PRODUCT SPEC", "Продуктовая граница, пользовательские вопросы и планируемые версии"],
            ["ARCHITECTURE", "Технологии, доменные границы, data flow и planned account connectors"],
            ["IMPORT DESIGN", "Staging, adapters, reconciliation, deduplication и rollback"],
            ["DECISIONS", "Журнал ADR с контекстом, принятыми решениями и последствиями"],
            ["PHASE 06", "Проверяемый план read only подключений инвестиционных счетов"],
        ],
        widths=[1.45, 5.25],
        font_size=8.8,
    )
    add_heading(doc, "Контроль перед внешней отправкой", 2)
    add_bullets(
        doc,
        [
            "обновить дату актуальности и таблицу этапов",
            "проверить, что screenshots сделаны на synthetic портфеле",
            "заменить места для схем, не меняя их номера и подписи",
            "повторно отрисовать DOCX и проверить каждую страницу",
        ],
    )

    # Normalize all runs to black and prevent accidental theme colors.
    for paragraph in doc.paragraphs:
        for run in paragraph.runs:
            run.font.color.rgb = RGBColor(0, 0, 0)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.color.rgb = RGBColor(0, 0, 0)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build_document()
