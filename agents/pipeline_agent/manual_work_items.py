from __future__ import annotations

import re
import uuid
from datetime import datetime
from html import escape
from html.parser import HTMLParser

from database.models.manual_work_item import MANUAL_WORK_ITEM_STATUSES, ManualWorkItem
from database.session import SessionLocal, init_db

_schema_ready = False


class _SafeHtmlParser(HTMLParser):
    allowed_tags = {"p", "br", "strong", "em", "ul", "ol", "li", "div"}
    allowed_alignments = {"left", "center", "right"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.text_parts: list[str] = []
        self._open_blocks = 0
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "iframe", "object", "svg"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag not in self.allowed_tags:
            return
        if tag == "br":
            self.parts.append("<br>")
            self.text_parts.append("\n")
            return
        alignment = next((value for key, value in attrs if key == "data-align"), None)
        if alignment not in self.allowed_alignments:
            style = next((value for key, value in attrs if key == "style"), "") or ""
            match = re.search(r"text-align\s*:\s*(left|center|right)", style, re.I)
            alignment = match.group(1).lower() if match else None
        align_attr = f' data-align="{alignment}"' if alignment in self.allowed_alignments else ""
        self.parts.append(f"<{tag}{align_attr}>")
        if tag in {"p", "div", "li"}:
            if self.text_parts and not self.text_parts[-1].endswith("\n"):
                self.text_parts.append("\n")
            self._open_blocks += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "iframe", "object", "svg"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag in self.allowed_tags and tag != "br":
            self.parts.append(f"</{tag}>")
            if tag in {"p", "div", "li"} and self._open_blocks:
                self.text_parts.append("\n")
                self._open_blocks -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        value = re.sub(r"\s+", " ", data)
        if value.strip() or value == " ":
            self.parts.append(escape(value))
            self.text_parts.append(value)


def sanitize_manual_html(raw_html: str) -> tuple[str, str]:
    parser = _SafeHtmlParser()
    parser.feed(raw_html or "")
    parser.close()
    safe_html = "".join(parser.parts).strip()
    body_text = re.sub(r"[ \t]+", " ", "".join(parser.text_parts))
    body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()
    return safe_html, body_text


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        init_db()
        _schema_ready = True


def _new_public_id(db) -> str:
    while True:
        public_id = f"MT-{uuid.uuid4().hex[:8].upper()}"
        if db.query(ManualWorkItem).filter(ManualWorkItem.public_id == public_id).first() is None:
            return public_id


def create_work_item(project_id: str, title: str, body_html: str) -> ManualWorkItem | None:
    _ensure_schema()
    safe_html, body_text = sanitize_manual_html(body_html)
    with SessionLocal() as db:
        item = ManualWorkItem(
            public_id=_new_public_id(db),
            project_id=project_id,
            title=title.strip(),
            body_html=safe_html,
            body_text=body_text,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        db.expunge(item)
        return item


def list_work_items(project_id: str, status: str | None = None) -> list[ManualWorkItem]:
    _ensure_schema()
    with SessionLocal() as db:
        query = db.query(ManualWorkItem).filter(ManualWorkItem.project_id == project_id)
        if status is not None:
            query = query.filter(ManualWorkItem.status == status)
        items = query.order_by(ManualWorkItem.updated_at.desc()).all()
        db.expunge_all()
        return items


def get_work_item(project_id: str, public_id: str) -> ManualWorkItem | None:
    _ensure_schema()
    with SessionLocal() as db:
        item = (
            db.query(ManualWorkItem)
            .filter(
                ManualWorkItem.project_id == project_id,
                ManualWorkItem.public_id == public_id,
            )
            .first()
        )
        if item is None:
            return None
        db.expunge(item)
        return item


def update_work_item(
    project_id: str, public_id: str, title: str, body_html: str
) -> ManualWorkItem | None:
    _ensure_schema()
    safe_html, body_text = sanitize_manual_html(body_html)
    with SessionLocal() as db:
        item = (
            db.query(ManualWorkItem)
            .filter(
                ManualWorkItem.project_id == project_id,
                ManualWorkItem.public_id == public_id,
            )
            .first()
        )
        if item is None:
            return None
        item.title = title.strip()
        item.body_html = safe_html
        item.body_text = body_text
        db.commit()
        db.refresh(item)
        db.expunge(item)
        return item


def update_work_item_status(
    project_id: str, public_id: str, status: str
) -> ManualWorkItem | None:
    if status not in MANUAL_WORK_ITEM_STATUSES:
        raise ValueError(f"Invalid manual work item status: {status!r}")
    _ensure_schema()
    with SessionLocal() as db:
        item = (
            db.query(ManualWorkItem)
            .filter(
                ManualWorkItem.project_id == project_id,
                ManualWorkItem.public_id == public_id,
            )
            .first()
        )
        if item is None:
            return None
        now = datetime.utcnow()
        item.status = status
        if status == "progress" and item.started_at is None:
            item.started_at = now
        elif status == "agentcompleted" and item.agent_completed_at is None:
            item.agent_completed_at = now
        elif status == "completed" and item.completed_at is None:
            item.completed_at = now
        db.commit()
        db.refresh(item)
        db.expunge(item)
        return item


def delete_work_item(project_id: str, public_id: str) -> bool:
    _ensure_schema()
    with SessionLocal() as db:
        item = (
            db.query(ManualWorkItem)
            .filter(
                ManualWorkItem.project_id == project_id,
                ManualWorkItem.public_id == public_id,
            )
            .first()
        )
        if item is None:
            return False
        if item.status == "progress":
            raise ValueError("A ticket in progress cannot be deleted")
        db.delete(item)
        db.commit()
        return True


def work_item_to_markdown(item: ManualWorkItem) -> str:
    return f"# {item.public_id}: {item.title}\n\n{item.body_text or '(no specification)'}\n"
