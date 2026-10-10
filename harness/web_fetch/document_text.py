"""Deterministic text extraction for raw documents returned by a provider."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile


def extract_document_text(
    raw: bytes, *, preserve_whitespace: bool = False
) -> tuple[str, str]:
    """Return extracted text and the extractor identity for supported documents."""
    if raw.startswith(b"%PDF"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages), "pypdf"
    if raw.startswith(b"PK"):
        return _extract_docx(raw, preserve_whitespace=preserve_whitespace), "docx_xml"
    raise ValueError("Unsupported raw document type")


def _extract_docx(raw: bytes, *, preserve_whitespace: bool = False) -> str:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    paragraph = f"{{{namespace}}}p"
    text = f"{{{namespace}}}t"
    spacing = {f"{{{namespace}}}tab", f"{{{namespace}}}br"}

    def text_nodes(node):
        if node.tag == text:
            yield node.text or ""
        elif node.tag in spacing:
            yield " "
        for child in node:
            yield from text_nodes(child)
        if node.tag == paragraph:
            yield "\n"

    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        parts = ["word/document.xml"] + sorted(
            name
            for name in archive.namelist()
            if re.fullmatch(r"word/(footnotes|endnotes|header\d+|footer\d+)\.xml", name)
        )
        lines = []
        for name in parts:
            root = ET.fromstring(archive.read(name))
            lines.extend(
                (line if preserve_whitespace else line.strip())
                for line in "".join(text_nodes(root)).splitlines()
                if line.strip()
            )
    return "\n".join(lines)
