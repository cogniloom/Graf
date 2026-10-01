"""Bounded raw recovery never promotes raw numeric/style evidence to display values."""

import io
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from evidencekg.config import DEFAULTS
from evidencekg.parsers import documents, parse

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def workbook(sheet, *, second=None, strings=None, padding=0, bad_style=True):
    """A real OOXML package with deliberately invalid font family (Excel max 14)."""
    book = Workbook()
    book.active['A1'] = 'placeholder'
    if second is not None:
        book.create_sheet('Later')
    out = io.BytesIO()
    book.save(out)
    source = zipfile.ZipFile(out)
    replacement = io.BytesIO()
    with zipfile.ZipFile(replacement, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in source.namelist():
            data = source.read(name)
            if name == 'xl/styles.xml' and bad_style:
                data = data.replace(b'<family val="2"', b'<family val="99"')
            if name == 'xl/worksheets/sheet1.xml':
                data = sheet.encode()
            if name == 'xl/worksheets/sheet2.xml':
                data = second.encode()
            if name == 'xl/_rels/workbook.xml.rels' and strings is not None:
                data = data.replace(b'</Relationships>', (
                    f'<Relationship Id="shared" Type="{REL}/sharedStrings" '
                    'Target="sharedStrings.xml"/></Relationships>'
                ).encode())
            archive.writestr(name, data)
        if strings is not None:
            archive.writestr('xl/sharedStrings.xml', strings)
        if padding:
            archive.writestr('xl/media/unreviewed.bin', b'X' * padding)
    source.close()
    return replacement.getvalue()


def sheet(cells):
    return f'<worksheet xmlns="{NS}"><sheetData><row r="1">{cells}</row></sheetData></worksheet>'


def extract(data, **limits):
    return documents.extract_document(data, '.xlsx', DEFAULTS | limits, Path('/tmp'))


def test_malformed_style_preserves_raw_formula_cache_type_coordinates_and_gaps():
    data = workbook(sheet(
        '<c r="B2" s="1"><f t="shared" si="0" ref="B2:B3">A2*2</f><v>0.50</v></c>'
        '<c r="B3" s="1"><f t="shared" si="0"/><v>45123</v></c>'
        '<c r="XFD1048576" t="b" s="3"><v>1</v></c>'
        '<c r="D4" s="5"/>'
    ))
    result = parse(data, '.xlsx', DEFAULTS)
    assert result['status'] == 'partial'
    assert len(result['sections']) == 3
    first, shared, boolean = result['sections']
    assert first['text'] == '=A2*2'
    assert first['locator']['raw_value'] == first['locator']['cached_value'] == '0.50'
    assert first['locator']['formula_attributes'] == {'t': 'shared', 'si': '0', 'ref': 'B2:B3'}
    assert first['locator']['style_index'] == '1'
    assert first['locator']['number_format'] is None
    assert shared['locator']['formula'] is None
    assert shared['locator']['formula_attributes'] == {'t': 'shared', 'si': '0'}
    assert shared['text'] == '[formula reference without expression]'
    assert boolean['locator']['row'] == 1048576
    assert boolean['locator']['column'] == 16384
    assert boolean['locator']['raw_type'] == 'b'
    assert any('formatting semantics are not interpreted' in w for w in result['warnings'])
    assert any('1 blank/style-only' in w for w in result['warnings'])


def test_archive_expansion_recovers_selected_parts_without_reading_padding():
    data = workbook(sheet('<c r="A1"><v>7</v></c>'), padding=1_000_000)
    result = extract(data, max_attachment_bytes=20_000, max_file_bytes=10_000)
    assert result['sections'][0]['text'] == '7'
    assert any('expansion limit' in w for w in result['warnings'])


def test_byte_limit_preserves_prefix_and_continues_later_sheet():
    data = workbook(sheet('<c r="A1"><v>7</v></c>' + '<c r="B1" s="1"/>' * 1000),
                    second=sheet('<c r="C4"><v>9</v></c>'))
    result = extract(data, max_file_bytes=4096)
    assert sorted((s['locator']['sheet'], s['text']) for s in result['sections']) == [('Later', '9'), ('Sheet', '7')]
    assert any('incomplete' in w and 'expansion limit' in w for w in result['warnings'])


def test_explicit_blank_records_count_against_cell_limit(monkeypatch):
    monkeypatch.setattr(documents, '_SPREADSHEET_CELL_LIMIT', 3)
    result = extract(workbook(sheet('<c r="A1"><v>7</v></c>' + '<c r="B1" s="1"/>' * 3 +
                                    '<c r="C1"><v>9</v></c>')))
    assert [s['text'] for s in result['sections']] == ['7']
    assert any('cell limit' in w and '3 cell records' in w for w in result['warnings'])


def test_shared_rich_strings_and_unresolved_reference_are_distinct():
    strings = f'<sst xmlns="{NS}"><si><r><t>Hello </t></r><r><t>World</t></r><rPh><t>guide</t></rPh></si></sst>'
    result = extract(workbook(sheet('<c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>8</v></c>'), strings=strings))
    assert [s['text'] for s in result['sections']] == ['Hello World', '[unresolved shared string]']
    assert result['sections'][1]['locator']['raw_value'] == '8'
    assert any('1 unresolved shared strings' in w for w in result['warnings'])


def test_malformed_sheet_keeps_complete_cells():
    content = f'<worksheet xmlns="{NS}"><sheetData><row><c r="A1"><v>7</v></c></row><broken'
    result = extract(workbook(content, second=sheet('<c r="A1"><v>8</v></c>')))
    assert sorted(s['text'] for s in result['sections']) == ['7', '8']
    assert any('incomplete' in w for w in result['warnings'])


def test_forbidden_entities_do_not_resolve_and_later_sheet_recovers():
    content = '<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]>' + sheet('<c r="A1" t="str"><v>&secret;</v></c>')
    result = extract(workbook(content, second=sheet('<c r="A1"><v>8</v></c>')))
    assert [s['text'] for s in result['sections']] == ['8']
    assert any('DTDForbidden' in w for w in result['warnings'])


def test_no_recoverable_cells_is_failed_and_budget_is_not_increased():
    result = extract(workbook(sheet('<c r="A1"><v>7</v></c>')), max_attachment_bytes=100)
    assert result['status'] == 'failed'
    assert result['sections'] == []
    assert any('expansion limit' in w for w in result['warnings'])


def test_invalid_coordinates_never_fabricated():
    result = extract(workbook(sheet('<c><v>1</v></c><c r="XFE1"><v>2</v></c><c r="A1"><v>3</v></c>')))
    assert [s['text'] for s in result['sections']] == ['3']
    assert any('2 invalid/missing' in w for w in result['warnings'])


def test_duplicate_zip_members_are_rejected():
    data = workbook(sheet('<c r="A1"><v>7</v></c>'))
    out = io.BytesIO(data)
    with zipfile.ZipFile(out, 'a') as archive, pytest.warns(UserWarning):
        archive.writestr('xl/worksheets/sheet1.xml', sheet('<c r="A1"><v>9</v></c>'))
    with pytest.raises(ValueError, match='duplicate ZIP'):
        extract(out.getvalue())


def test_inflated_read_budget_counts_all_members_and_never_reads_past_ceiling():
    budget = [7]
    first_source = io.BytesIO(b'123456')
    reader = documents._SpreadsheetReader(first_source, budget, 4, 6)
    assert reader.read(100) == b'1234'
    with pytest.raises(ValueError, match='expansion limit'):
        reader.read()
    assert first_source.tell() == 4
    second_source = io.BytesIO(b'abcdef')
    reader = documents._SpreadsheetReader(second_source, budget, 6, 6)
    assert reader.read() == b'abc'
    with pytest.raises(ValueError, match='expansion limit'):
        reader.read()
    assert second_source.tell() == 3
    assert budget == [0]


def test_exact_member_byte_limit_allows_eof():
    content = sheet('<c r="A1"><v>7</v></c>').encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('sheet.xml', content)
    with zipfile.ZipFile(out) as archive:
        budget = [len(content)]
        values = [e.find('{'+NS+'}v').text for e in documents._spreadsheet_records(
            archive, 'sheet.xml', {'{'+NS+'}c'}, budget, len(content))]
    assert values == ['7']
    assert budget == [0]


def test_aggregate_limit_keeps_first_sheet_and_labels_unread_remainder():
    first = sheet('<c r="A1"><v>7</v></c>')
    data = workbook(first, second=sheet('<c r="A1"><v>8</v></c>'))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        limit = sum(archive.getinfo(n).file_size for n in (
            'xl/workbook.xml', 'xl/_rels/workbook.xml.rels', 'xl/worksheets/sheet1.xml'))
    result = extract(data, max_attachment_bytes=limit)
    assert [s['text'] for s in result['sections']] == ['7']
    assert any("Sheet 'Later'" in w and 'incomplete' in w for w in result['warnings'])


def test_cell_lookalikes_outside_sheet_data_are_not_evidence():
    content = sheet('<c r="A1"><v>7</v></c>').replace(
        '</worksheet>', '<extLst><c r="B1"><v>fabricated</v></c></extLst></worksheet>')
    result = extract(workbook(content))
    assert [s['text'] for s in result['sections']] == ['7']


def test_output_limit_keeps_earlier_cells():
    result = extract(workbook(sheet('<c r="A1"><v>7</v></c>' +
                                    ''.join(f'<c r="A{i}"><v>8</v></c>' for i in range(2, 40)))),
                     max_file_bytes=2048)
    assert 0 < len(result['sections']) < 39
    assert result['sections'][0]['text'] == '7'
    assert any('output byte limit' in w for w in result['warnings'])


def test_valid_styles_malformed_xml_triggers_recovery_without_losing_other_sheet():
    content = sheet('<c r="A1"><v>7</v></c>').replace('</worksheet>', '<broken></worksheet>')
    result = parse(workbook(content, second=sheet('<c r="A1"><v>8</v></c>'), bad_style=False), '.xlsx', DEFAULTS)
    assert result['status'] == 'partial'
    assert sorted(s['text'] for s in result['sections']) == ['7', '8']
    assert any('incomplete' in w for w in result['warnings'])


def test_recovery_output_has_conservative_ceiling_and_preserves_small_sheets(monkeypatch):
    monkeypatch.setattr(documents, '_SPREADSHEET_OUTPUT_BYTES', 2048)
    large = sheet(''.join(f'<c r="A{i}"><v>8</v></c>' for i in range(1, 40)))
    result = extract(workbook(large, second=sheet('<c r="A1"><v>7</v></c>')))
    assert result['sections'][0]['locator']['sheet'] == 'Later'
    assert result['sections'][0]['text'] == '7'
    assert 1 < len(result['sections']) < 40
    assert any('output byte limit reached (2048 bytes)' in w for w in result['warnings'])
