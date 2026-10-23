"""Deterministic org catalog for Salesforce projects.

Dumps a bounded schema snapshot via `sf` (not an LLM) and stores it as
project memory so planning/coding start from real API names instead of
guessing. See docs/GREENFIELD_AND_ORG_MEMORY.md.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

OnEvent = Callable[[str, str], None]

_CUSTOM_DESCRIBE_CAP = 80
_STANDARD_DESCRIBE = ("Account", "Contact", "Product2", "Opportunity", "User")
_METADATA_TYPES = (
    "CustomObject",
    "PermissionSet",
    "CustomApplication",
    "FlexiPage",
    "ApexClass",
    "LightningComponentBundle",
)


def _sf_result(sandbox, command: str, timeout_seconds: int = 120) -> Any:
    result = sandbox.execute(command, timeout_seconds=timeout_seconds)
    text = (result.get("stdout") or "").strip()
    if "{" not in text:
        return None
    try:
        payload = json.loads(text[text.index("{") :])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return payload
    if payload.get("status") not in (0, None):
        return None
    return payload.get("result", payload)


def _names_from_metadata(result: Any) -> list[str]:
    if not isinstance(result, list):
        return []
    names = []
    for row in result:
        if isinstance(row, dict) and row.get("fullName"):
            names.append(str(row["fullName"]))
        elif isinstance(row, str):
            names.append(row)
    return sorted(names)


def _field_line(field: dict) -> str:
    name = field.get("name") or "?"
    ftype = field.get("type") or "?"
    bits = [ftype]
    if field.get("length"):
        bits.append(f"len {field['length']}")
    if field.get("nillable") is False:
        bits.append("required")
    if field.get("unique"):
        bits.append("unique")
    if field.get("externalId"):
        bits.append("external-id")
    refs = field.get("referenceTo") or []
    if refs:
        bits.append("-> " + ",".join(refs))
    if ftype == "picklist":
        values = [
            v.get("value")
            for v in (field.get("picklistValues") or [])
            if isinstance(v, dict) and v.get("active", True) and v.get("value")
        ]
        if values:
            bits.append("[" + ", ".join(values[:20]) + ("…" if len(values) > 20 else "") + "]")
    return f"`{name}` ({', '.join(bits)})"


def collect_catalog(
    sandbox, timeout_seconds: int = 120, on_event: OnEvent | None = None
) -> dict[str, Any]:
    if on_event:
        on_event("step", "Reading org display")
    org = _sf_result(sandbox, "sf org display --json", timeout_seconds) or {}
    if not isinstance(org, dict):
        org = {}

    by_type: dict[str, list[str]] = {}
    for meta_type in _METADATA_TYPES:
        if on_event:
            on_event("step", f"Listing {meta_type}")
        names = _names_from_metadata(
            _sf_result(
                sandbox,
                f"sf org list metadata --metadata-type {meta_type} --json",
                timeout_seconds,
            )
        )
        by_type[meta_type] = names

    custom_objects = [n for n in by_type.get("CustomObject", []) if n.endswith("__c")]
    describe_targets = list(custom_objects[:_CUSTOM_DESCRIBE_CAP])
    for std in _STANDARD_DESCRIBE:
        if std not in describe_targets:
            describe_targets.append(std)

    objects: dict[str, Any] = {}
    truncated = len(custom_objects) > _CUSTOM_DESCRIBE_CAP
    if on_event:
        on_event("step", f"Describing {len(describe_targets)} objects")
    for api_name in describe_targets:
        desc = _sf_result(
            sandbox,
            f"sf sobject describe --sobject {api_name} --json",
            timeout_seconds,
        )
        if not isinstance(desc, dict):
            objects[api_name] = {"missing": True}
            continue
        interesting = []
        for f in desc.get("fields") or []:
            if not isinstance(f, dict):
                continue
            name = f.get("name") or ""
            if f.get("custom") or name in {"Id", "Name", "OwnerId", "IsActive"}:
                interesting.append(f)
        objects[api_name] = {
            "label": desc.get("label"),
            "custom": bool(desc.get("custom")),
            "fields": interesting,
        }

    api_version = str(org.get("apiVersion") or org.get("maxApiVersion") or "")
    return {
        "org": {
            "id": org.get("id"),
            "username": org.get("username"),
            "alias": org.get("alias"),
            "instance_url": org.get("instanceUrl"),
            "api_version": api_version,
        },
        "metadata": by_type,
        "objects": objects,
        "custom_object_count": len(custom_objects),
        "describes_truncated": truncated,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


_NAME_LIST_CAP = 40


def _slim(names: list[str], cap: int = _NAME_LIST_CAP) -> tuple[list[str], int]:
    """Drop managed-package / namespaced entries (`ns__Thing`, but keep custom
    suffixes `__c` / `__mdt` / `__e` / `__b`) and cap the rest. A fresh org
    returns ~800 standard CustomObject names and ~100 standard permission sets
    from `sf org list metadata`; dumping all of them every session is pure
    context bloat (the worker only needs what the org actually customised)."""
    keep_suffix = ("__c", "__mdt", "__e", "__b", "__x", "__Share", "__History")
    slimmed: list[str] = []
    for n in names:
        core = n
        for suf in keep_suffix:
            if core.endswith(suf):
                core = core[: -len(suf)]
                break
        if "__" in core:
            continue  # ns__Name -> managed package, skip
        slimmed.append(n)
    return slimmed[:cap], max(0, len(slimmed) - cap)


def render_catalog_markdown(catalog: dict[str, Any]) -> str:
    org = catalog.get("org") or {}
    meta: dict[str, list[str]] = catalog.get("metadata") or {}
    objects: dict[str, Any] = catalog.get("objects") or {}
    lines = [
        "# Org catalog (generated — do not hand-edit the facts)",
        "",
        "This is a snapshot of the connected Salesforce org. Use it before naming "
        "any object, field, LWC, or permission set. Live MCP describe is for "
        "confirming a *named* API from the ticket, not for discovering the org.",
        "",
        f"- Generated: `{catalog.get('generated_at')}`",
        f"- Org id: `{org.get('id') or 'unknown'}`",
        f"- Username: `{org.get('username') or 'unknown'}`",
        f"- Alias: `{org.get('alias') or '—'}`",
        f"- Instance: `{org.get('instance_url') or '—'}`",
        f"- API version: `{org.get('api_version') or 'unknown'}`",
        f"- Custom objects: **{catalog.get('custom_object_count', 0)}**",
        "",
    ]
    if catalog.get("describes_truncated"):
        lines.append(
            f"> Custom-object describes capped at {_CUSTOM_DESCRIBE_CAP}. "
            "Use MCP to describe any object named in the ticket that is listed "
            "below but has no field block."
        )
        lines.append("")

    custom_suffix = ("__c", "__mdt", "__e", "__b", "__x")

    def _is_local_custom(n: str) -> bool:
        for suf in custom_suffix:
            if n.endswith(suf):
                return "__" not in n[: -len(suf)]  # drop managed `ns__Thing__c`
        return False

    custom_names = [n for n in (meta.get("CustomObject") or []) if _is_local_custom(n)]
    lines.append("## Custom objects")
    lines.append("")
    if not custom_names:
        lines.append("None. The org has no custom objects yet — create metadata in git.")
        lines.append("")
    else:
        lines.append(", ".join(f"`{n}`" for n in custom_names))
        lines.append("")

    lines.append("## Object fields")
    lines.append("")
    for api_name, info in objects.items():
        if info.get("missing"):
            lines.append(f"### `{api_name}`")
            lines.append("Not describable (may be a platform/metadata-only name).")
            lines.append("")
            continue
        label = info.get("label") or api_name
        kind = "custom" if info.get("custom") else "standard"
        lines.append(f"### `{api_name}` — {label} ({kind})")
        fields = info.get("fields") or []
        if not fields:
            lines.append("- (no custom/identity fields dumped)")
        for field in fields:
            lines.append(f"- {_field_line(field)}")
        lines.append("")

    def _section(title: str, key: str) -> None:
        names, dropped = _slim(meta.get(key) or [])
        lines.append(f"## {title}")
        lines.append("")
        if not names:
            lines.append("None.")
        else:
            lines.append(", ".join(f"`{n}`" for n in names))
            if dropped:
                lines.append("")
                lines.append(f"> +{dropped} more (and managed-package entries omitted). "
                             f"`sf org list metadata --metadata-type {key}` for the full list.")
        lines.append("")

    _section("Permission sets", "PermissionSet")
    _section("Apps", "CustomApplication")
    _section("Lightning pages", "FlexiPage")
    _section("Apex classes", "ApexClass")
    _section("LWC bundles", "LightningComponentBundle")
    return "\n".join(lines).rstrip() + "\n"
