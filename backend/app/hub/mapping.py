"""Turning one system's data into another's: field mapping and transforms,
{{placeholder}} templates for what is sent, and XML read safely.

A mapping is either the simple form - {"CustomerName": "name"} - or a list of
rules, for when a rename isn't enough:

    [{"source": "customer.fullName", "target": "name", "transform": "trim"},
     {"source": "kind", "target": "segment", "map": {"C": "B2C", "E": "B2B"}, "default": "B2B"},
     {"target": "source", "value": "SAP"},
     {"source": "gstin", "target": "tax_id", "required": true}]
"""

from __future__ import annotations

import datetime as dt
import json
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote
from xml.parsers import expat
from xml.sax.saxutils import escape


def dig(obj: Any, path: str) -> Any:
    """'data.items' / 'result[0].lines' / 'result.0.lines' / '' (the object itself)."""
    if not path:
        return obj
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", str(path)):
        index = part[1:-1] if part.startswith("[") else part if part.isdigit() and isinstance(obj, list) else None
        if index is not None:
            obj = obj[int(index)] if isinstance(obj, list) and len(obj) > int(index) else None
        else:
            obj = obj.get(part) if isinstance(obj, dict) else None
        if obj is None:
            return None
    return obj


def _number(v: Any) -> float | int:
    text = re.sub(r"[^\d.\-]", "", str(v))
    number = float(text)  # ValueError: not a number
    return int(number) if number.is_integer() else number


def _boolean(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on", "t")


def _date(v: Any) -> str:
    text = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y%m%d", "%d.%m.%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise ValueError(f"'{v}' is not a date") from exc


TRANSFORMS = {
    "trim": lambda v: str(v).strip(),
    "upper": lambda v: str(v).strip().upper(),
    "lower": lambda v: str(v).strip().lower(),
    "title": lambda v: str(v).strip().title(),
    "digits": lambda v: re.sub(r"\D", "", str(v)),
    "number": _number,
    "integer": lambda v: int(float(re.sub(r"[^\d.\-]", "", str(v)))),
    "boolean": _boolean,
    "date": _date,
    "string": str,
}


def _key(name: Any) -> str:
    return str(name).strip().lower().replace(" ", "_")


def _apply_rule(row: dict[str, Any], rule: dict[str, Any]) -> tuple[str, Any]:
    target = _key(rule.get("target") or rule.get("source") or "")
    if not target:
        raise ValueError("A mapping rule needs a target")
    value = rule["value"] if "value" in rule else dig(row, str(rule.get("source") or ""))
    if isinstance(rule.get("map"), dict) and value is not None:
        table = {str(k).strip().lower(): v for k, v in rule["map"].items()}
        value = table.get(str(value).strip().lower(), value)
    if value is None or value == "":
        if rule.get("default") not in (None, ""):
            value = rule["default"]
        elif rule.get("required"):
            raise ValueError(f"{rule.get('source') or target} is required")
    if value is not None and value != "" and rule.get("transform"):
        name = str(rule["transform"])
        if name not in TRANSFORMS:
            raise ValueError(f"Unknown transform '{name}' - use one of {', '.join(TRANSFORMS)}")
        try:
            value = TRANSFORMS[name](value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{rule.get('source') or target}: {exc if str(exc).startswith(chr(39)) else f'is not a valid {name}'}") from exc
    return target, value


def apply(row: dict[str, Any], mapping: Any, keep_unmapped: bool = True) -> dict[str, Any]:
    """row with the mapping applied. keep_unmapped: columns the mapping doesn't name
    come along under their own name. A ValueError names what is wrong with the row."""
    out: dict[str, Any] = {}
    if isinstance(mapping, dict):
        for src, target in mapping.items():
            if "." in str(src) or "[" in str(src):  # a path into a nested record
                out[_key(target)] = dig(row, str(src))
        for k, v in row.items():
            target = mapping.get(k) or mapping.get(str(k).strip())
            if target:
                out[_key(target)] = v
            elif keep_unmapped:
                out[_key(k)] = v
        return out
    used = set()
    for rule in mapping or []:
        if not isinstance(rule, dict):
            raise ValueError("A mapping is a list of rules")
        target, value = _apply_rule(row, rule)
        if value is not None:
            out[target] = value
        used.add(re.split(r"[.\[]", str(rule.get("source") or ""))[0])
    if keep_unmapped:
        for k, v in row.items():
            if k not in used and _key(k) not in out:
                out[_key(k)] = v
    return out


def valid_mapping(mapping: Any) -> Any:
    """The mapping, or a ValueError a person can act on."""
    if isinstance(mapping, dict):
        return {str(k).strip(): str(v).strip() for k, v in mapping.items() if str(k).strip() and str(v).strip()}
    if not isinstance(mapping, list):
        raise ValueError("A mapping is {source: target} or a list of rules")
    for rule in mapping:
        if not isinstance(rule, dict) or not (rule.get("target") or rule.get("source")):
            raise ValueError("Each mapping rule needs a target")
        if rule.get("transform") and rule["transform"] not in TRANSFORMS:
            raise ValueError(f"Unknown transform '{rule['transform']}' - use one of {', '.join(TRANSFORMS)}")
    return mapping


def load_mapping(raw: str | None) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


# ---------------------------------------------------------------- {{templates}}

_TOKEN = re.compile(r"\{\{\s*([\w.\[\]-]+)\s*\}\}")


def value_of(ctx: dict[str, Any], path: str) -> Any:
    """A placeholder's value: from the context, or the payload without saying payload."""
    found = dig(ctx, path)
    return found if found is not None else dig(ctx.get("payload"), path)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return json.dumps(value, default=str) if isinstance(value, (dict, list)) else "true" if value is True else "false" if value is False else str(value)


def render_text(template: str, ctx: dict[str, Any], encode=None) -> str:
    def fill(match: re.Match) -> str:
        text = _text(value_of(ctx, match.group(1)))
        return encode(text) if encode else text

    return _TOKEN.sub(fill, template or "")


def render_path(template: str, ctx: dict[str, Any]) -> str:
    return render_text(template, ctx, lambda v: quote(v, safe=""))


def _render_value(node: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        return {k: _render_value(v, ctx) for k, v in node.items()}
    if isinstance(node, list):
        return [_render_value(v, ctx) for v in node]
    if isinstance(node, str):
        whole = _TOKEN.fullmatch(node.strip())
        return value_of(ctx, whole.group(1)) if whole else render_text(node, ctx)  # a number stays a number
    return node


def render_body(template: str, ctx: dict[str, Any]) -> tuple[str, str]:
    """(the body, its kind: json | xml | text). In JSON a value that is one placeholder keeps its type;
    values going into XML are escaped, so a customer's name can't change the document."""
    text = (template or "").strip()
    if text.startswith(("{", "[")):
        try:
            return json.dumps(_render_value(json.loads(text), ctx), default=str), "json"
        except ValueError:
            pass
    if text.startswith("<"):
        return render_text(text, ctx, escape), "xml"
    return render_text(text, ctx), "text"


# ---------------------------------------------------------------- XML


def parse_xml(data: bytes | str) -> ET.Element:
    """An XML document from outside. A DOCTYPE or entity declaration is refused outright - that closes
    XXE and "billion laughs", and no SOAP message has any business carrying one (SOAP 1.1 section 3)."""
    builder = ET.TreeBuilder()

    def name_of(raw: str) -> str:
        return "{" + raw if "}" in raw else raw

    def refuse(*_args) -> None:
        raise ValueError("XML with a DOCTYPE or entity declaration is not accepted")

    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    parser.StartElementHandler = lambda name, attrs: builder.start(name_of(name), {name_of(k): v for k, v in attrs.items()})
    parser.EndElementHandler = lambda name: builder.end(name_of(name))
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.UnparsedEntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse
    try:
        parser.Parse(data, True)
        return builder.close()
    except expat.ExpatError as exc:
        raise ValueError(f"That is not well-formed XML: {exc}") from exc
    except AssertionError as exc:  # the builder had no root: an empty document
        raise ValueError("That is not well-formed XML") from exc


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def to_dict(element: ET.Element) -> Any:
    """An element as plain data: children by their local names, a repeated child as a list, a leaf as text."""
    children = list(element)
    if not children:
        text = (element.text or "").strip()
        return text if text else None
    out: dict[str, Any] = {}
    for child in children:
        name, value = local(child.tag), to_dict(child)
        if name in out:
            if not isinstance(out[name], list):
                out[name] = [out[name]]
            out[name].append(value)
        else:
            out[name] = value
    return out


def _tag(name: Any) -> str:
    tag = re.sub(r"[^\w.-]", "_", str(name)) or "item"
    return tag if re.match(r"[A-Za-z_]", tag) else f"_{tag}"


def to_xml(tag: str, value: Any) -> str:
    """Plain data as XML, every value escaped."""
    name = _tag(tag)
    if isinstance(value, list):
        return "".join(to_xml(name, v) for v in value)
    if isinstance(value, dict):
        return f"<{name}>" + "".join(to_xml(k, v) for k, v in value.items()) + f"</{name}>"
    if value is None:
        return f"<{name}/>"
    return f"<{name}>{escape(_text(value))}</{name}>"
