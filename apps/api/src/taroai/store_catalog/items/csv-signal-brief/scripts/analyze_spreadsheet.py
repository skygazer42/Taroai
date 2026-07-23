#!/usr/bin/env python3
"""Create an aggregate data-quality report for CSV or XLSX without dependencies."""

import argparse
import csv
import math
import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _missing(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _outlier_count(values):
    if len(values) < 4:
        return 0
    q1 = _percentile(values, 0.25)
    q3 = _percentile(values, 0.75)
    spread = q3 - q1
    lower, upper = q1 - 1.5 * spread, q3 + 1.5 * spread
    return sum(value < lower or value > upper for value in values)


def _column_index(reference):
    letters = re.match(r"[A-Z]+", reference.upper())
    index = 0
    for letter in letters.group(0) if letters else "A":
        index = index * 26 + ord(letter) - 64
    return index - 1


def _cell_value(cell, shared_strings):
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))
    value = cell.find(f"{{{MAIN_NS}}}v")
    if value is None or value.text is None:
        return None
    raw = value.text
    if cell_type == "s":
        return shared_strings[int(raw)]
    if cell_type == "b":
        return raw == "1"
    if cell_type in {"str", "e"}:
        return raw
    try:
        number = float(raw)
        return int(number) if number.is_integer() else number
    except ValueError:
        return raw


def _xlsx_sheets(path):
    with zipfile.ZipFile(path) as archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t"))
                for item in root.findall(f"{{{MAIN_NS}}}si")
            ]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            item.get("Id"): item.get("Target")
            for item in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
        }
        for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
            target = targets[sheet.get(f"{{{REL_NS}}}id")]
            member = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
            root = ET.fromstring(archive.read(member))
            rows = []
            for row in root.findall(f".//{{{MAIN_NS}}}row"):
                values = {}
                for cell in row.findall(f"{{{MAIN_NS}}}c"):
                    values[_column_index(cell.get("r", "A1"))] = _cell_value(cell, shared_strings)
                width = max(values, default=-1) + 1
                rows.append([values.get(index) for index in range(width)])
            yield sheet.get("name", "Sheet"), rows


def _csv_sheet(path):
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("CSV must be UTF-8 or GB18030 encoded")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    return [(Path(path).stem, list(csv.reader(text.splitlines(), dialect)))]


def _headers(row, width):
    headers, used = [], set()
    for index in range(width):
        base = str(row[index]).strip() if index < len(row) and not _missing(row[index]) else f"Column {index + 1}"
        name = base
        suffix = 2
        while name in used:
            name = f"{base} ({suffix})"
            suffix += 1
        used.add(name)
        headers.append(name)
    return headers


def _sheet_report(name, rows):
    if not rows:
        return [f"## {name}", "", "工作表为空。", ""]
    width = max(map(len, rows))
    headers = _headers(rows[0], width)
    data = [row + [None] * (width - len(row)) for row in rows[1:]]
    duplicate_count = len(data) - len({tuple("" if _missing(value) else value for value in row) for row in data})
    lines = [f"## {name}", "", f"- 数据行数：{len(data)}", f"- 列数：{width}", f"- 完全重复行：{duplicate_count}", "", "### 缺失值", "", "| 列 | 缺失数 | 缺失率 |", "|---|---:|---:|"]
    for index, header in enumerate(headers):
        missing = sum(_missing(row[index]) for row in data)
        rate = missing / len(data) * 100 if data else 0
        lines.append(f"| {header} | {missing} | {rate:.2f}% |")
    lines.extend(["", "### 数值范围与 IQR 异常值", ""])
    numeric_found = False
    for index, header in enumerate(headers):
        values = [row[index] for row in data if _number(row[index])]
        if not values:
            continue
        numeric_found = True
        lines.append(f"- {header}：最小值 {min(values):g}，最大值 {max(values):g}，IQR 异常值 {_outlier_count(values)} 个。")
    if not numeric_found:
        lines.append("- 未发现数值列，数值范围不适用。")
    lines.extend(["", "### 文本长度异常", ""])
    text_found = False
    for index, header in enumerate(headers):
        lengths = [len(row[index]) for row in data if isinstance(row[index], str) and row[index].strip()]
        if not lengths:
            continue
        text_found = True
        lines.append(f"- {header}：长度范围 {min(lengths)}–{max(lengths)}，IQR 异常值 {_outlier_count(lengths)} 个。")
    if not text_found:
        lines.append("- 未发现文本列。")
    lines.append("")
    return lines


def analyze(input_path, output_path):
    suffix = Path(input_path).suffix.casefold()
    sheets = _xlsx_sheets(input_path) if suffix == ".xlsx" else _csv_sheet(input_path) if suffix == ".csv" else None
    if sheets is None:
        raise ValueError("Only .csv and .xlsx files are supported")
    lines = ["# 数据质量报告", "", f"文件：{Path(input_path).name}", "", "异常值使用 1.5×IQR 规则；仅输出汇总，不输出敏感行级数据。", ""]
    for name, rows in sheets:
        lines.extend(_sheet_report(name, rows))
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(output_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", default="/workspace/artifacts/data_quality_report.md")
    args = parser.parse_args()
    analyze(args.input, args.output)


if __name__ == "__main__":
    main()
