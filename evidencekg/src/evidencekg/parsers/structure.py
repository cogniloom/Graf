"""Document structure is evidence: keep revisions and quotation scopes separate."""

from __future__ import annotations

import re
from email.utils import parsedate_to_datetime


def email_sections(text, part, sender=None, date=None):
    """Conservative line-prefix quotation depth; attribution markers stay visible."""
    speaker, anchor, quoted_block = None, None, False
    for index, line in enumerate(text.splitlines()):
        prefix = re.match(r"\s*(?:>\s*)+", line)
        depth = prefix.group().count(">") if prefix else int(quoted_block)
        marker = re.fullmatch(r"(?:On .+ wrote:|Am .+ schrieb .+:)", line.strip())
        if marker:
            speaker = line.strip()
            anchor = None  # A quoted header is not the outer message timestamp.
            quoted_block = True
        if line.strip() in {
            "-----Original Message-----",
            "-----Ursprüngliche Nachricht-----",
            "---------- Forwarded message ---------",
        }:
            quoted_block = True
            speaker = None
        try:
            outer_day = parsedate_to_datetime(date).date().isoformat() if date else None
        except (ValueError, TypeError, OverflowError):
            outer_day = None
        yield dict(
            text=line,
            locator=dict(
                kind="mime_body",
                mime_part=part,
                line=index,
                quotation_depth=depth,
                evidence_role="quoted" if depth else "authored",
                attributed_speaker_surface=speaker if depth else sender,
                source_day=anchor if depth else outer_day,
                attribution_status="header_surface_unverified"
                if (speaker if depth else sender)
                else "unknown",
            ),
            modality="native",
        )


def docx_sections(root, name):
    from lxml import etree

    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    headings = []
    for index, paragraph in enumerate(root.xpath('//*[local-name()="p"]')):
        style = paragraph.find(f"{ns}pPr/{ns}pStyle")
        style_name = style.get(ns + "val") if style is not None else None
        locator = dict(kind="docx", part=name, paragraph=index, style=style_name)
        if "comments" in name:
            locator["evidence_role"] = "comment"
        elif "footnotes" in name or "endnotes" in name:
            locator["evidence_role"] = "note"
        else:
            locator["evidence_role"] = "body"
        cell = next((a for a in paragraph.iterancestors() if a.tag == ns + "tc"), None)
        if cell is not None:
            row = cell.getparent()
            table = row.getparent()
            locator.update(
                table=root.xpath('//*[local-name()="tbl"]').index(table),
                row=list(table.findall(ns + "tr")).index(row) + 1,
                column=list(row.findall(ns + "tc")).index(cell) + 1,
                explicit_header=row.find(f"{ns}trPr/{ns}tblHeader") is not None,
            )
        current, revisions = [], {}
        refs = []
        for node in paragraph.iter():
            tag = etree.QName(node).localname
            if tag in ("footnoteReference", "endnoteReference", "commentReference"):
                refs.append({"kind": tag, "id": node.get(ns + "id")})
            if tag not in ("t", "delText", "tab", "br", "cr"):
                continue
            token = "\t" if tag == "tab" else "\n" if tag in ("br", "cr") else node.text or ""
            revision = next(
                (
                    a
                    for a in node.iterancestors()
                    if a.tag in {ns + "del", ns + "ins", ns + "moveFrom", ns + "moveTo"}
                ),
                None,
            )
            if revision is None:
                current.append(token)
            else:
                key = root.getroottree().getpath(revision)
                record = revisions.setdefault(
                    key,
                    {
                        "text": [],
                        "kind": etree.QName(revision).localname,
                        "author": revision.get(ns + "author"),
                        "date": revision.get(ns + "date"),
                        "id": revision.get(ns + "id"),
                    },
                )
                record["text"].append(token)
                if record["kind"] in ("ins", "moveTo"):
                    current.append(token)
        body = "".join(current)
        if style_name and (match := re.fullmatch(r"(?:Heading|Überschrift)\s*([1-9])", style_name, re.I)):
            level = int(match[1])
            headings = [h for h in headings if h["level"] < level] + [{"level": level, "text": body}]
        locator.update(
            headings=list(headings),
            references=refs,
            revision_state="unaccepted_changes" if revisions else "none",
        )
        yield dict(text=body, locator=locator, modality="native")
        for revision in revisions.values():
            role = "deleted_revision" if revision["kind"] in ("del", "moveFrom") else "inserted_revision"
            yield dict(
                text="".join(revision.pop("text")),
                locator=dict(locator, evidence_role=role, revision=revision),
                modality="native",
            )
