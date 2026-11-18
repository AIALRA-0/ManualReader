"""Generate translation, resource explanations and chapter attachments from frozen inputs."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any, Iterable

from PIL import Image


def _hash(*values: Any) -> str:
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _items(value: Any) -> list:
    return value if isinstance(value, list) else []


def _objects(value: Any) -> list[dict]:
    return [row for row in _items(value) if isinstance(row, dict)]


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _strings(value: Any) -> list[str]:
    return [x for x in _items(value) if isinstance(x, str) and x]


def _read(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _chunks(rows: Iterable[dict], limit: int, chars: int):
    batch = []
    size = 0
    for row in rows:
        cost = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        if batch and (len(batch) >= limit or size + cost > chars):
            yield batch
            batch = []
            size = 0
        batch.append(row)
        size += cost
    if batch:
        yield batch


async def _request(ask, stage: str, payload: dict, images: dict | None = None) -> tuple[dict, str]:
    try:
        value = await (ask(stage, payload, images) if images else ask(stage, payload))
        if not isinstance(value, dict):
            return {}, "invalid_response"
        return value, ""
    except Exception as error:
        return {}, type(error).__name__


def source_identity(source: dict, unit: dict) -> tuple[str, str, str, str]:
    files = source.get("source_files") or {}
    file_id = unit["file_id"]
    version = files.get(file_id) if isinstance(files, dict) else None
    if isinstance(version, dict):
        version = version.get("sha256") or version.get("source_version")
    version = version or unit.get("source_version") or source.get("snapshot_sha256")
    source_sha = hashlib.sha256(_str(unit.get("text")).encode("utf-8")).hexdigest()
    return file_id, unit["source_id"], version or source_sha, source_sha


_MATH_FENCES = re.compile(r"\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\${1,2}[^$]+\${1,2}")
_INLINE_CODE = re.compile(r"\x60[^\x60]+\x60|https?://\S+")
_HAN = re.compile(r"[\u3400-\u9fff]")
_OTHER_LANGUAGE = re.compile(r"[a-zA-Z\u00c0-\u024f\u0370-\u052f\u3040-\u30ff\uac00-\ud7af]")
_MATLAB_LINE = re.compile(
    r"^(?:>>\s*)?(?:function\s+|(?:[A-Za-z]\w*|\[[A-Za-z\w, ~]+\])\s*=\s*"
    r"(?:[A-Za-z]\w*\s*\(|\[[^\n]*\]|[-+0-9].*;\s*$)|(?:clear|clc|close all)\s*;?\s*$)"
)
_MATH_NAME = {"arctan","cos","det","exp","inf","log","max","min","mod","sin","sup","tan","lim"}


def code_source(text):
    lines=[x.strip() for x in text.splitlines() if x.strip()]
    return bool(lines) and all(line.startswith("%") or _MATLAB_LINE.match(line) or (
        line.endswith(";") and re.match(r"^[A-Za-z]\w*\(",line)) for line in lines)


def formula_source(text):
    if not re.search(r"[≈≠∑∏√∈∉≤≥⩽⩾→←∂∫∧∨]|:=",text):
        return False
    # Multiple lower-case letters are normally prose, except for mathematical operators.
    lower_words = {v for v in re.findall(r"(?<![A-Za-z])([a-z]{2,})(?![A-Za-z])",text)}
    return lower_words <= _MATH_NAME


_ASSET_KINDS = {"image", "figure", "vector", "page_visual", "svg"}
_PRODUCT_HEADING = re.compile(r"[A-Z][A-Z0-9-]{1,15} [A-Z]{2,12}[0-9]{2,8}")
_NUMERIC_CELL = re.compile(r"[-+]?\d+(?:[.,]\d+)*(?:ns|ms|μs|s|min|h|%)?")


def source_literal_retention(unit):
    """Identify a product label or table column whose recorded values can be kept verbatim."""
    text = _str(unit.get("text")).strip()
    if (unit.get("kind") == "heading" and _PRODUCT_HEADING.fullmatch(text)):
        return "product_label"
    cells = [line.strip() for line in text.splitlines() if line.strip()]
    if ((unit.get("position") or {}).get("type") == "pdf_page" and len(cells) >= 5
            and re.fullmatch(r"[A-Z][A-Z0-9-]{2,24}", cells[0])
            and all(_NUMERIC_CELL.fullmatch(cell) for cell in cells[1:])):
        return "numeric_table_column"
    return None


def _source_relation(unit: dict) -> str:
    kind = unit.get("kind")
    text = _str(unit.get("text")).strip()
    if kind in _ASSET_KINDS:
        return "source_asset"
    if kind in {"equation", "formula", "math", "math_display", "math_inline"}:
        return "preserved_math"
    if kind in {"code", "fence", "code_block"} or code_source(text):
        return "preserved_code"
    if source_literal_retention(unit):
        return "identity"
    content = _INLINE_CODE.sub("", _MATH_FENCES.sub("", text))
    if (unit.get("position") or {}).get("type") == "pdf_page" and formula_source(content):
        return "preserved_math"
    if not content.strip() and _MATH_FENCES.search(text):
        return "preserved_math"
    counts = Counter("han" if _HAN.match(c) else "foreign" for c in content if c.isalpha())
    if not content.strip() or not _OTHER_LANGUAGE.search(content):
        return "identity" if text else "unresolved"
    if counts["han"] and counts["han"] >= counts["foreign"] * 0.5:
        return "identity"
    return "translated"


def needs_translation(unit: dict) -> bool:
    return _source_relation(unit) == "translated"


def source_units(source: dict) -> list[dict]:
    rows = []
    for unit in _objects(source.get("sources")):
        if isinstance(unit.get("source_id"), str) and isinstance(unit.get("file_id"), str):
            file_id, source_id, file_version, text_sha = source_identity(source, unit)
            rows.append({
                **unit, "file_id": file_id, "source_id": source_id,
                "source_version": file_version, "original_sha256": text_sha,
                "relation": _source_relation(unit),
            })
    return rows


def _key(unit: dict) -> tuple[str, str, str, str]:
    return unit["file_id"], unit["source_id"], unit["source_version"], unit["original_sha256"]


def _position(unit: dict) -> tuple:
    position = unit.get("position") or {}
    start = position.get("start", position.get("number", 0))
    end = position.get("end", start)
    return (start if isinstance(start, (int, float)) else 0,
            end if isinstance(end, (int, float)) else 0, unit["source_id"], unit["original_sha256"])


@dataclass
class TranslationCatalog:
    by_source: dict
    historical: dict
    glossary: tuple


def build_translation_catalog(sources: Iterable[dict], historical: dict | None = None,
                              glossary: Iterable[dict] = ()) -> TranslationCatalog:
    historical = historical or {}
    file_units = defaultdict(dict)
    lexicon = tuple(glossary)
    for source in sources:
        for unit in source_units(source):
            file_units[(unit["file_id"], unit["source_version"])][_key(unit)] = unit
    lookup = {}
    for (file_id, version), known in sorted(file_units.items()):
        units = sorted(known.values(), key=_position)
        seq = []
        seen = set()

        def register(consecutive: list[dict]):
            if not consecutive:
                return
            for group in _chunks(consecutive, 16, 8200):
                ids = {(u["source_id"], u["original_sha256"]) for u in group}
                first = min(k for k, u in enumerate(units) if (u["source_id"], u["original_sha256"]) in ids)
                last = max(k for k, u in enumerate(units) if (u["source_id"], u["original_sha256"]) in ids)
                neighbors = (units[first-1:first] if first else []) + units[last+1:last+2]
                payload = {
                    "file_id": file_id, "source_version": version,
                    "units": [
                        {"source_id": u["source_id"], "original": u["text"], "kind": u.get("kind"),
                         "original_sha256": u["original_sha256"],
                         "source_unit_ids": _strings(u.get("registered_unit_ids")),
                         "position": u.get("position")}
                        for u in group
                    ],
                    "surrounding_read_only": [
                        {"source_id": u["source_id"], "original": _str(u.get("text"))[:700]}
                        for u in neighbors
                    ],
                    "known_terms": list(lexicon),
                }
                for unit in group:
                    lookup[_key(unit)] = payload

        for unit in units:
            identity = _key(unit)
            candidate = (unit["relation"] == "translated" and unit["source_id"] not in seen
                         and not (historical.get(identity, {}).get("status") == "complete"
                                  and _str(historical[identity].get("text")).strip()))
            if not candidate:
                register(seq)
                seq.clear()
                seen.clear()
                if unit["relation"] != "translated" or identity in historical:
                    continue
            seq.append(unit)
            seen.add(unit["source_id"])
        register(seq)
    return TranslationCatalog(lookup, historical, lexicon)


def _translation_reply(raw: dict, batch: dict) -> dict[str, dict]:
    allowed = {row["source_id"] for row in batch["units"]}
    accepted = {}
    ambiguous = set()
    original={row["source_id"]:row["original"] for row in batch["units"]}
    unresolved = set(_strings(raw.get("unresolved_source_ids")))
    for group in _objects(raw.get("groups")):
        for seg in _objects(group.get("segments")):
            sid = seg.get("source_id")
            if not isinstance(sid, str) or sid not in allowed or sid in unresolved:
                continue
            text, status = _str(seg.get("text")), seg.get("status")
            relation=seg.get("relation")
            if status not in {"complete", "uncertain"} or not text.strip():
                continue
            if relation in {"identity","preserved_math"}:
                text=original[sid]
            elif relation!="translated" or text.strip()==original[sid].strip():
                continue
            if sid in accepted:
                ambiguous.add(sid)
                continue
            accepted[sid] = {
                "text": text, "status": status, "relation":relation,
                "translation_group_id": _str(group.get("translation_group_id")),
            }
    return {sid: row for sid, row in accepted.items() if sid not in ambiguous}


async def translate(root: Path, sample: str, source: dict, ask,
                    catalog: TranslationCatalog | None = None, on_partial=None, *, existing=None) -> dict:
    units = source_units(source)
    catalog = catalog or build_translation_catalog([source])
    needed = {_key(u) for u in units if u["relation"] == "translated"}
    found = {}
    by_id={u['source_id']:u for u in units}
    for group in _objects((existing or {}).get('groups')):
        for row in _objects(group.get('segments')):
            unit=by_id.get(row.get('source_id'))
            if (unit and row.get('source_version')==unit['source_version'] and
                    row.get('original_sha256')==unit['original_sha256'] and
                    row.get('status') in ('complete','uncertain') and _str(row.get('text')).strip()):
                found[_key(unit)]={**row,'translation_group_id':group.get('translation_group_id','')}
    for row in _objects((existing or {}).get('links')):
        unit=by_id.get(row.get('source_id'))
        if (unit and row.get('source_version')==unit['source_version'] and
                row.get('status') in ('complete','uncertain') and row.get('translation_id') and
                row.get('relation') in ('identity','preserved_math','preserved_code')):
            found[_key(unit)]={**row,'text':unit['text']}
    for key,row in found.items():
        if row['status']=='complete':
            catalog.historical.setdefault(key,row)
    tasks = {}
    for key in sorted(needed):
        payload = catalog.by_source.get(key)
        if payload:
            pending=[u for u in payload['units'] if
                     (payload['file_id'],u['source_id'],payload['source_version'],u['original_sha256']) not in found and
                     not catalog.historical.get((payload['file_id'],u['source_id'],payload['source_version'],u['original_sha256']))]
            if pending:
                scoped={**payload,'units':pending}
                tasks.setdefault(_hash(scoped),scoped)

    async def lookup(payload: dict):
        response, failure = await _request(ask, "B1", payload)
        if failure:
            return {}, [u["source_id"] for u in payload["units"]], failure
        accepted = _translation_reply(response, payload)
        translated = {}
        for unit in payload["units"]:
            sid = unit["source_id"]
            if sid in accepted:
                key = (payload["file_id"], sid, payload["source_version"], unit["original_sha256"])
                row=accepted[sid]
                group=row.get("translation_group_id")
                translated[key]={**row,"translation_group_id":_hash(payload)[:12]+"/"+group if group else ""}
        missing=[u["source_id"] for u in payload["units"] if u["source_id"] not in accepted]
        wire_unresolved=set(_strings(response.get("unresolved_source_ids")))
        declarations={r.get("source_id"):r.get("relation")
                      for g in _objects(response.get("groups")) for r in _objects(g.get("segments"))}
        detail={sid:("provider_unresolved" if sid in wire_unresolved else
                    "provider_omitted_id" if sid not in declarations else
                    f"rejected_response_relation:{declarations[sid]}") for sid in missing}
        return translated, missing, detail

    issues = []
    for done in asyncio.as_completed(lookup(d) for d in tasks.values()):
        translated,missing,failure=await done
        found.update(translated)
        if failure:
            used = sorted(sid for sid in missing if any(sid == key[1] for key in needed))
            if used:
                issues.append({"stage":"B1","source_ids":used,
                               "reason":failure if isinstance(failure,str) else "响应中部分来源缺少可用译文",
                               "per_source_reason":{sid:failure[sid] for sid in used} if isinstance(failure,dict) else {}})
        if on_partial and translated:
            await on_partial(translation_output(sample,units,needed,catalog,found,issues),list(translated))
    return translation_output(sample,units,needed,catalog,found,issues)


def translation_output(sample,units,needed,catalog,found,issues):
    counts = Counter({"needed": len(needed), "complete": 0, "uncertain": 0, "unresolved": 0,
                      "identity": 0, "preserved_math": 0, "preserved_code": 0,
                      "source_asset": 0, "unavailable_source": 0, "fresh": 0, "reused": 0})
    groups = []
    links = []
    unresolved = []
    for unit in units:
        key = _key(unit)
        file_id, sid, version, original_hash = key
        relation = unit["relation"]
        if relation in {"source_asset", "unresolved"}:
            counts["source_asset" if relation == "source_asset" else "unavailable_source"] += 1
            continue
        if relation != "translated":
            counts[relation] += 1
            links.append({"source_id": sid, "translation_id": sid, "relation": relation,
                          "source_version": version, "status": "complete"})
            continue
        old = catalog.historical.get(key)
        row = found.get(key)
        reused = bool(old and old.get("status") == "complete" and _str(old.get("text")).strip())
        if reused:
            row = old
        if not row:
            counts["unresolved"] += 1
            unresolved.append(sid)
            continue
        status = row["status"]
        if row.get("relation") in {"identity","preserved_math"}:
            counts["provider_preserved"] += 1
            counts["preserved_uncertain"] += status=="uncertain"
            links.append({"source_id":sid,"translation_id":sid,"relation":row["relation"],
                          "source_version":version,"original_sha256":original_hash,
                          "status":status,"provenance":"provider_preserved"})
            continue
        counts[status] += 1
        if status == "complete":
            counts["reused" if reused else "fresh"] += 1
        tid = "tr-" + _hash(*key)[:22]
        segment = {"source_id": sid, "source_ids": [sid], "translation_id": tid,
                   "source_version": version, "original_sha256": original_hash,
                   "source_unit_ids": _strings(unit.get("registered_unit_ids")),
                   "relation": "translated", "status": status, "text": row["text"],
                   "provenance": "reused" if reused else "fresh"}
        previous = groups[-1] if groups else None
        provider_group = row.get("translation_group_id") or ""
        same_group = bool(provider_group and previous and previous["file_id"] == file_id
                          and previous["source_version"] == version
                          and previous["provider_group"] == provider_group)
        if same_group:
            previous["segments"].append(segment)
            previous["source_ids"].append(sid)
        else:
            groups.append({
                "translation_group_id": "trg-" + _hash(file_id, version, provider_group or sid,
                                                       key[3] if not provider_group else "")[:22],
                "file_id": file_id, "source_version": version, "source_ids": [sid],
                "provider_group": provider_group, "kind": unit.get("kind"), "segments": [segment],
            })
        links.append({k: segment[k] for k in ("source_id", "translation_id", "source_version",
                                             "relation", "status", "original_sha256")})
    return {"schema_version": "translation-groups-v1", "sample_id": sample, "groups": groups,
            "links": links, "unresolved_source_ids": unresolved, "counts": dict(counts), "issues": issues}


def translation_processing(source, links):
    """Count handled text units separately from units that received translated text."""
    units = [u for u in source_units(source) if u["relation"]!="source_asset" and u.get("text","").strip()]
    by_id = {r["source_id"]:r for r in links}
    translated = preserved = uncertain = 0
    failures = []
    for unit in units:
        row = by_id.get(unit["source_id"], {})
        if (row.get("status") not in {"complete","uncertain"} or not row.get("translation_id")
                or row.get("source_version") != unit["source_version"]):
            failures.append(unit["source_id"])
        elif row.get("relation") in {"preserved_math","preserved_code","identity"}:
            preserved += 1
            uncertain += row["status"]=="uncertain"
        elif row.get("relation")=="translated":
            translated += 1
            uncertain += row["status"]=="uncertain"
        else:
            failures.append(unit["source_id"])
    return {"processed":translated+preserved,"total":len(units),"translated":translated,
            "preserved":preserved,"uncertain":uncertain,"failed_source_ids":failures}


_MATH_KINDS = {"formula_inline", "formula_display", "equation", "math"}
P2_KINDS = {"formula_display", "equation", "image", "svg", "vector", "table", "mermaid",
            "code", "code_block", "fence"}


def p2_inventory(structure):
    blocks={r["block_id"]:r for r in _structure_items(structure,"blocks")}
    totals=defaultdict(int)
    out={}
    for item in _resources(structure):
        rid=item.get("resource_id")
        if not rid:
            continue
        kind=_kind(item)
        block=blocks.get(item.get("block_id"))
        reason=None
        if kind=="formula_inline":
            reason="inline_math"
        elif kind not in P2_KINDS:
            reason="unsupported_kind"
        elif item.get("surface","body")!="body":
            reason="outside_visible_body"
        elif not block:
            reason="missing_display_anchor"
        elif kind in {"image","svg","vector"} and not _standalone_image(item,block):
            reason="inline_media"
        if not reason:
            totals[kind]+=1
        out[rid]={"eligible":reason is None,"exclude_reason":reason,"ordinal":totals[kind] if not reason else None,
                  "kind":kind,"block_id":item.get("block_id")}
    return out


def _standalone_image(item,block):
    raw=_str(block.get("markdown"))
    left=item["start"]-block["start"]
    right=item["end"]-block["start"]
    if not 0<=left<right<=len(raw):
        return False
    prefix=raw[raw.rfind("\n",0,left)+1:left]
    suffix=raw[right:].split("\n",1)[0]
    return not prefix.strip(" \t>") and not suffix.strip()


def applicable_resources(structure):
    inventory=p2_inventory(structure)
    return [row for row in _resources(structure) if inventory.get(row.get("resource_id"),{}).get("eligible")]


_VISUAL_KINDS = {"image", "vector", "svg"}
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_TEX_ELEMENTS = re.compile(r"\\(?:[A-Za-z]+|[%_#&$])|[A-Za-z]+(?:_\{?[A-Za-z0-9]+\}?)?|[+-]?\d+(?:\.\d+)?|[+*/=<>^∑≤≥→∞]")
_SKIP_TEX = {"begin", "end", "left", "right", "quad", "qquad", "displaystyle", "text", "mathrm",
             "mathbb", "mathbf", "dfrac", "tfrac", "aligned", "matrix", "bmatrix", "array"}


def _resources(structure: dict) -> list[dict]:
    value = structure.get("resources", [])
    if isinstance(value, dict):
        value = value.get("items", [])
    return _objects(value)


def _structure_items(structure: dict, key: str) -> list[dict]:
    value = structure.get(key)
    return _objects(list(value.values()) if isinstance(value, dict) else value)


def _kind(resource: dict) -> str:
    return _str(resource.get("kind"))


def _script_elements(kind: str, raw: str) -> dict:
    if kind in _MATH_KINDS:
        literals = list(dict.fromkeys(x for x in _TEX_ELEMENTS.findall(raw) if x.lstrip("\\") not in _SKIP_TEX))
        return {"symbols_and_operators": literals[:100]}
    if kind == "table":
        rows = [re.split(r"(?<!\\)\|", line.strip().strip("|")) for line in raw.splitlines() if "|" in line]
        return {"rows": [[s.strip() for s in row] for row in rows]}
    if kind == "mermaid":
        return {"diagram_lines": [x.strip() for x in raw.splitlines() if x.strip() and not x.startswith(chr(96) * 3)]}
    if kind in {"code", "code_block", "fence"}:
        fence = chr(96) * 3
        lines = raw.splitlines()
        return {"language": lines[0].strip().removeprefix(fence).strip() if lines and fence in lines[0] else "",
                "line_count": len(lines), "code_lines": [x for x in lines if not x.strip().startswith(fence)]}
    if kind == "svg":
        return {"visible_text": re.findall(r"<text[^>]*>([^<]+)</text>", raw)}
    return {}


def _source_links(structure: dict) -> dict[str, list[str]]:
    direct = defaultdict(list)
    for edge in _structure_items(structure, "source_edges"):
        if edge.get("relation_type") == "navigation_only":
            continue
        block_id, sid = edge.get("block_id"), edge.get("source_id")
        if isinstance(block_id, str) and isinstance(sid, str) and sid not in direct[block_id]:
            direct[block_id].append(sid)
    return direct


def _contexts(structure: dict):
    blocks = {b["block_id"]: b for b in _structure_items(structure, "blocks") if b.get("block_id")}
    return blocks, _source_links(structure)


def _context_bits(resource: dict, blocks: dict, direct: dict) -> dict:
    bid = resource.get("block_id")
    row = blocks.get(bid, {})
    return {"block_id": bid, "part_id": row.get("part_id"),
            "heading": resource.get("heading"), "surface": resource.get("surface", "body"),
            "nearby_markdown": _str(row.get("markdown") or row.get("text"))[:1600],
            "source_ids": sorted(set(_strings(resource.get("source_ids")) + direct.get(bid, [])))}


def _resource_row(resource: dict) -> dict:
    raw = _str(resource.get("raw_content") or resource.get("raw"))
    return {"resource_id": resource["resource_id"], "kind": _kind(resource), "raw": raw,
            "script_elements": _script_elements(_kind(resource), raw)}


def _math_groups(structure: dict):
    blocks, direct = _contexts(structure)
    by_context = defaultdict(list)
    for row in applicable_resources(structure):
        if _kind(row) in _MATH_KINDS and row.get("resource_id") and row.get("status", "ok") == "ok":
            by_context[(row.get("block_id"), row.get("surface", "body"),
                        row.get("heading") if not row.get("block_id") else None)].append(row)
    groups = []
    for rows in by_context.values():
        # Identity sharing is limited to one block's mathematical setting.
        literals = defaultdict(list)
        for row in rows:
            literals[(_kind(row), _str(row.get("raw_content")))].append(row)
        representatives = []
        for same in literals.values():
            representatives.append({**_resource_row(same[0]),
                                    "resource_ids": [r["resource_id"] for r in same]})
        for chunk in _chunks(representatives, 22, 7200):
            ids = [rid for rep in chunk for rid in rep["resource_ids"]]
            groups.append({"group_id": "math-" + _hash(ids)[:20], "resource_ids": ids,
                           "resources": chunk, **_context_bits(rows[0], blocks, direct)})
    return groups


def _coverage(resources: list[dict], known: dict, failed: dict | None = None) -> list[dict]:
    failed = failed or {}
    rows = []
    for row in resources:
        rid = row["resource_id"]
        result = known.get(rid)
        state = result["status"] if result else failed.get(rid, "pending")
        rows.append({"resource_id": rid, "block_id": row.get("block_id"),
                     "surface": row.get("surface", "body"), "kind": _kind(row), "status": state,
                     "group_id": result["group_id"] if result else None})
    return rows


def _math_combination(group: dict, declared: set[str], expected: list[dict]) -> dict | None:
    members = [base for base in expected if declared.intersection(base["resource_ids"])]
    if (len(members) < 2 or group.get("group_id") not in {base["group_id"] for base in members}
            or not declared <= {rid for base in members for rid in base["resource_ids"]}
            or len({base.get("surface") for base in members}) != 1):
        return None
    resources = [row for base in members for row in base["resources"]
                 if declared.intersection(row.get("resource_ids", [row["resource_id"]]))]
    return {
        "group_id": "math-" + _hash([base["group_id"] for base in members])[:20],
        "resource_ids": sorted(declared),
        "resources": resources,
        "block_id": members[0].get("block_id"),
        "block_ids": list(dict.fromkeys(base["block_id"] for base in members if base.get("block_id"))),
        "source_ids": list(dict.fromkeys(sid for base in members for sid in _strings(base.get("source_ids")))),
    }


def _p2_reply(raw: dict, expected: list[dict], *, allow_math_combinations: bool = False) -> tuple[list, dict]:
    expected_by_id = {group["group_id"]: group for group in expected}
    known = {}
    groups = []
    occupied = set()
    unresolved = set(_strings(raw.get("unresolved_resource_ids")))
    for group in _objects(raw.get("groups")):
        declared = _strings(group.get("resource_ids"))
        if not declared or len(set(declared)) != len(declared) or set(declared) & occupied:
            continue
        ids = set(declared)
        supplied = group.get("group_id")
        allowed = expected_by_id.get(supplied)
        if allowed is None:
            matches = [x for x in expected if ids <= set(x["resource_ids"])]
            allowed = matches[0] if len(matches) == 1 else None
        if allow_math_combinations and (allowed is None or not ids <= set(allowed["resource_ids"])):
            allowed = _math_combination(group, ids, expected)
        if allowed is None or not ids <= set(allowed["resource_ids"]):
            continue
        status = group.get("status")
        if status not in {"explained", "partial", "needs_review"}:
            status = "provided"
        overview = _str(group.get("overview"))
        summary = _str(group.get("summary"))
        steps = _strings(group.get("composition") or group.get("relations"))
        components = []
        gid = "p2-" + _hash(allowed["group_id"], sorted(ids))[:20]
        for k in _objects(group.get("components")):
            raw_content = _str(k.get("raw") or k.get("raw_content"))
            meaning = _str(k.get("meaning"))
            if not raw_content.strip() or not meaning.strip():
                continue
            certainty = _str(k.get("certainty"))
            components.append({"component_id": gid + "-e" + str(len(components) + 1),
                               "raw_content": raw_content, "meaning": meaning,
                               "certainty": "uncertain" if certainty in {"", "unknown", "uncertain",
                                                                       "unreadable", "low"} else "grounded",
                               "location_hint": _str(k.get("location_hint"))})
        if not (overview.strip() or components or summary.strip() or steps):
            continue
        aliases = []
        for member in allowed["resources"]:
            same = member.get("resource_ids", [member["resource_id"]])
            if ids.intersection(same):
                aliases.extend(rid for rid in same if rid not in ids and rid not in occupied and rid not in unresolved)
        final_ids = list(dict.fromkeys(declared + aliases))
        native = {"group_id": gid, "resource_ids": final_ids, "kind": group.get("kind") or "resource",
                  "overview": overview, "components": components, "status": status,
                  "summary": summary,
                  "composition_or_steps": steps,
                  "model_unresolved_resource_ids": [rid for rid in declared if rid in unresolved],
                  "assumptions_and_limits": _strings(group.get("conditions") or group.get("unreadable")),
                  "source_ids": _strings(allowed.get("source_ids")),
                  "question_context": {"resource_ids": final_ids, "block_id": allowed.get("block_id"),
                                       "block_ids": allowed.get("block_ids") or
                                           ([allowed["block_id"]] if allowed.get("block_id") else []),
                                       "source_ids": _strings(allowed.get("source_ids"))}}
        groups.append(native)
        occupied.update(final_ids)
        for rid in final_ids:
            known[rid] = {"group_id": gid, "status": "covered_by_group" if rid in aliases and status == "explained"
                          else status}
    return groups, known


def _pending_resource_groups(groups, known):
    pending=[]
    for group in groups:
        ids=[rid for rid in group["resource_ids"] if rid not in known]
        if not ids:
            continue
        wanted=set(ids)
        resources=[]
        for row in group["resources"]:
            members=[rid for rid in row.get("resource_ids",[row["resource_id"]]) if rid in wanted]
            if members:
                resources.append({**row,"resource_id":members[0],"resource_ids":members})
        pending.append({**group,"resource_ids":ids,"resources":resources})
    return pending


async def _read_explanation_batch(stage, groups, request, *, math=False):
    found=[]
    known={}
    labels=[]
    pending=groups
    reasons=[]
    for attempt in range(3):
        raw, issue, new_labels = await request(pending, attempt)
        parsed, covered = _p2_reply(raw, pending, allow_math_combinations=math)
        found.extend(parsed)
        known.update(covered)
        labels.extend(new_labels)
        pending = _pending_resource_groups(pending, known)
        if not pending:
            return found, known, labels, None
        reasons.append(issue or "response_missing_resource_ids")
        if attempt < 2:
            await asyncio.sleep(2 ** attempt)
    return found, known, labels, {"stage":stage,"reason":reasons[-1],"attempt_reasons":reasons,
                                 "resource_ids":[rid for group in pending for rid in group["resource_ids"]]}


def _explanation_source_context(groups, originals):
    ordered_ids=dict.fromkeys(sid for group in groups for sid in group.get("source_ids",[]))
    return [{"source_id":sid,"text":originals[sid][:800]} for sid in ordered_ids
            if originals.get(sid)][:12]


async def explain_math(root: Path, sample: str, structure: dict, ask, on_partial=None, *, source=None) -> dict:
    relevant = [r for r in applicable_resources(structure) if _kind(r) in _MATH_KINDS and r.get("resource_id")]
    originals={row["source_id"]:_str(row.get("text")) for row in _objects((source or {}).get("sources"))}

    async def inspect(batch: list[dict]):
        async def request(groups, attempt):
            ids=[rid for group in groups for rid in group["resource_ids"]]
            payload={"group_id":groups[0]["group_id"],"resource_ids":ids,"semantic_groups":groups}
            if originals:
                payload["source_excerpts"]=_explanation_source_context(groups,originals)
            if attempt:
                payload["response_retry"]={"attempt":attempt+1,"return_resource_ids":ids}
            raw,failure=await _request(ask,"A1",payload)
            return raw,failure,[]
        found,known,_,issue=await _read_explanation_batch("A1",batch,request,math=True)
        return found,known,issue

    groups = []
    known = {}
    issues = []
    by_part = defaultdict(list)
    for group in _math_groups(structure):
        by_part[group.get("part_id")].append(group)
    batches = [batch for part in by_part.values() for batch in _chunks(part, 6, 15300)]
    for task in asyncio.as_completed(inspect(batch) for batch in batches):
        found,covers,issue=await task
        groups.extend(found)
        known.update(covers)
        if issue:
            issues.append(issue)
        if on_partial and found:
            await on_partial({'groups':list(groups),'coverage_by_resource':_coverage(relevant,known),
                              'verified_labels':[],'issues':list(issues)})
    return {"groups": groups, "coverage_by_resource": _coverage(relevant, known),
            "verified_labels": [], "issues": issues}


def archived_math_responses(root: Path) -> dict[str, dict]:
    """Read successful A1 receipts and index them by their exact original request."""
    receipts = {}
    for original in sorted((root / "requests" / "A1").glob("*/input.json")):
        try:
            request = _read(original)
            receipt = _read(original.with_name("result.json"))
        except (OSError, ValueError, UnicodeError):
            continue
        if (request.get("stage") == "A1" and request.get("id") == original.parent.name
                and isinstance(request.get("input"), dict) and not receipt.get("error")
                and (receipt.get("response") or {}).get("status") == "completed"
                and isinstance(receipt.get("result"), dict)):
            receipts[_hash(request["input"])] = {"id": request["id"], "result": receipt["result"]}
    return receipts


def replay_math_explanations(structure: dict, previous: dict, archive: dict) -> tuple[dict, dict]:
    """Re-associate archived math explanations to their original resource IDs."""
    replacements = set()
    groups = []
    known = {}
    batch_receipts = []
    for batch in _chunks(_math_groups(structure), 6, 15300):
        ids = [rid for group in batch for rid in group["resource_ids"]]
        key = _hash({"group_id": batch[0]["group_id"], "resource_ids": ids, "semantic_groups": batch})
        receipt = archive.get(key)
        if not receipt:
            continue
        result = receipt["result"]
        found, covers = _p2_reply(result, batch, allow_math_combinations=True)
        before = {rid: {"status": row["status"], "group_id": row["group_id"]}
                  for row in _objects(previous.get("coverage_by_resource"))
                  if (rid := row.get("resource_id")) in ids and row.get("group_id")}
        if covers == before:
            continue
        replacements.update(covers)
        groups.extend(found)
        known.update(covers)
        batch_receipts.append(receipt["id"])
    original_coverage = _objects(previous.get("coverage_by_resource"))
    if not replacements:
        return previous, {"request_ids": [], "newly_explained_ids": [], "changed_ids": []}
    retained=[]
    for row in _objects(previous.get("groups")):
        remaining=[rid for rid in _strings(row.get("resource_ids")) if rid not in replacements]
        if remaining:
            retained.append({**row,"resource_ids":remaining,
                             "question_context":{**row.get("question_context",{}),"resource_ids":remaining}})
    coverage = [
        {**row, "status": known.get(row["resource_id"], {}).get("status", "pending"),
         "group_id": known.get(row["resource_id"], {}).get("group_id")}
        if row.get("resource_id") in replacements else row
        for row in original_coverage
    ]
    full = {"explained", "covered_by_group"}
    pairs = zip(original_coverage, coverage, strict=True)
    changed = [(before, after) for before, after in pairs if before != after]
    return {
        **previous, "groups": retained + groups, "coverage_by_resource": coverage,
    }, {
        "request_ids": batch_receipts,
        "newly_explained_ids": [after["resource_id"] for before, after in changed
                                if before["status"] not in full and after["status"] in full],
        "changed_ids": [after["resource_id"] for _, after in changed],
    }


def reproject_archived_math(root: Path) -> dict:
    """Refresh isolated P2 outputs from archived responses after all final artifacts exist."""
    from ..step05.inference import write
    from .formatting import layer, replace_leaves, scan_batch, sha, text_leaves, type_patches

    jobs = sorted((root / "jobs").iterdir())
    if any(not (job / "run.json").is_file() for job in jobs if job.is_dir()):
        raise ValueError("Wait for the isolated finish run to finish before replaying A1.")
    archive = archived_math_responses(root)
    summary = {}
    group_path = ["attachments", "resource_explanations", "groups"]
    for job in jobs:
        side = job / "generated"
        path = side / "math.json"
        if not path.is_file():
            continue
        previous = _read(path)
        revised, receipt = replay_math_explanations(_read(side / "structure.json"), previous, archive)
        if not receipt["changed_ids"]:
            continue

        p2 = _read(job / "resource-explanations.json")
        patches = _read(job / "format-patches.json")
        old_groups = p2["groups"]
        previous_ids = {row["group_id"] for row in previous["groups"]}
        revised_ids = {row["group_id"] for row in revised["groups"]}
        retained = [group for group in old_groups
                    if group["group_id"] not in previous_ids or group["group_id"] in revised_ids]
        index = {group["group_id"]: position for position, group in enumerate(retained)}
        kept_patches = []
        for patch in patches:
            loc = patch["path"]
            if loc[:3] == group_path:
                new_position = index.get(old_groups[loc[3]]["group_id"])
                if new_position is None:
                    continue
                patch = {**patch, "path": [*group_path, new_position, *loc[4:]]}
            kept_patches.append(patch)

        for group in revised["groups"]:
            if group["group_id"] in index:
                continue
            leaves = list(text_leaves(group))
            changes = {}
            for ((loc, value), scan) in zip(leaves, scan_batch([value for _, value in leaves]), strict=True):
                text, operations = layer(value, type_patches(value, scan, []))
                if operations:
                    changes[loc] = text
                    kept_patches.append({
                        "path": [*group_path, len(retained), *loc],
                        "from_sha256": sha(value), "to_sha256": sha(text),
                        "phases": [{"name": "typography", "operations": operations}],
                    })
            retained.append(replace_leaves(group, changes))

        projected = {row["resource_id"]: row for row in revised["coverage_by_resource"]}
        coverage = [{**row, **projected.get(row["resource_id"], {})} for row in p2["coverage_by_resource"]]
        p2 = {**p2, "groups": retained, "coverage_by_resource": coverage}
        attachments = {**_read(job / "attachments.final.json"), "resource_explanations": p2}
        formatted_file = side / "formatted.json"
        formatted = _read(formatted_file) if formatted_file.is_file() else None
        run = _read(job / "run.json")
        write(job / "resource-explanations.json", p2)
        write(job / "attachments.final.json", attachments)
        write(job / "format-patches.json", kept_patches)
        if formatted is not None:
            write(formatted_file, {**formatted, "attachments": attachments, "patches": kept_patches})
        write(job / "run.json", {**run, "p2": dict(Counter(row["status"] for row in coverage)),
                                 "a1_archive_replay": receipt})
        write(side / "math-archive-replay.json", receipt)
        write(path, revised)
        summary[job.name] = receipt
    return summary


def _verified_images(root: Path, resources: list[dict]) -> dict[str, bytes]:
    images = {}
    for row in resources:
        sha = row.get("media_sha256") or row.get("asset_sha256")
        if not isinstance(sha, str) or not _SHA.fullmatch(sha) or sha in images:
            continue
        path = root / "media" / (sha + ".png")
        if not path.resolve().is_relative_to((root / "media").resolve()):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or hashlib.sha256(data).hexdigest() != sha:
            continue
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except (OSError, SyntaxError, Image.DecompressionBombError):
            continue
        images[sha] = data
    return images


def _label(resource: dict, label_id: str, value: str, location: str) -> dict:
    sha = _str(resource.get("asset_sha256") or resource.get("media_sha256") or resource.get("sha256"))
    rid = resource["resource_id"]
    return {"resource_id": rid, "label_id": label_id, "original": value, "location_hint": location,
            "asset_sha256": sha, "asset_id": resource.get("asset_id"),
            "label_key": "label-" + _hash(sha, value.strip(), location.strip())[:22]}


def _visual_reply_labels(raw: dict, resources: dict, images: dict) -> list[dict]:
    labels = {}
    for row in _objects(raw.get("verified_labels")):
        rid, lid = _str(row.get("resource_id")), _str(row.get("label_id"))
        original, location = _str(row.get("original")), _str(row.get("location_hint"))
        resource = resources.get(rid, {})
        sha = resource.get("media_sha256") or resource.get("asset_sha256")
        status = _str(row.get("certainty")).lower()
        if (not rid or not lid or not original.strip() or not location.strip() or sha not in images
                or status not in {"high", "known", "confirmed", "visible", "certain", "verified", "grounded"}):
            continue
        label = _label(resource, lid, original, location)
        labels[(rid, lid)] = label
    return list(labels.values())


def source_labels(source: dict) -> list[dict]:
    assets = source.get("assets") or {}
    labels = []
    for key, asset in assets.items() if isinstance(assets, dict) else ():
        if not isinstance(asset, dict):
            continue
        sha = asset.get("sha256")
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            continue
        for row in _objects(asset.get("verified_labels") or asset.get("labels")):
            identifier, text = _str(row.get("label_id")), _str(row.get("original") or row.get("text"))
            loc = _str(row.get("location_hint") or row.get("location"))
            status = _str(row.get("certainty") or row.get("status"))
            if identifier and text.strip() and loc and status in {"verified", "known", "confirmed", "high", "grounded", "visible"}:
                labels.append(_label(asset | {"resource_id": key, "asset_id": key}, identifier, text, loc))
    return labels


def _visual_payload(batch: list[dict], images: dict, context: dict) -> dict:
    groups = []
    for row in batch:
        rid = row["resource_id"]
        sha = row.get("media_sha256") or row.get("asset_sha256")
        bits = _resource_row(row)
        if sha in images:
            bits.update({"image_sha256": sha, "image_index": list(images).index(sha) + 1})
        bits['related_images']=[
            {'resource_id':part['resource_id'],'caption':part.get('raw_content',''),
             'image_sha256':part['media_sha256'],'image_index':list(images).index(part['media_sha256'])+1}
            for part in row.get('related_images',[]) if part['media_sha256'] in images]
        groups.append({"group_id": "visual-" + _hash(rid)[:20], "resources": [bits],
                       "resource_ids": [rid], **context.get(rid, {})})
    return {"resource_ids": [r["resource_id"] for r in batch],
            "image_sha256_in_attachment_order": list(images), "semantic_groups": groups}


async def explain_visual(root: Path, sample: str, structure: dict, source: dict, ask, on_labels=None,
                         on_partial=None) -> dict:
    relevant = [r for r in applicable_resources(structure) if _kind(r) not in _MATH_KINDS and r.get("resource_id")]
    catalog=structure.get('resource_context_catalog',structure['resources'])
    anchors=defaultdict(list)
    for resource in catalog:
        if _kind(resource) in _VISUAL_KINDS and resource.get('media_sha256'):
            anchors[resource.get('block_id')].append(resource)
    for index,row in enumerate(relevant):
        related=list(structure.get('related_visual_inputs',{}).get(row['resource_id'],[]))
        if _kind(row)=='table':
            related.extend(r for r in anchors[row.get('block_id')]
                           if r.get('surface')==row.get('surface') and
                           row.get('start',-1)<=r.get('start',-2)<r.get('end',-1)<=row.get('end',-2))
        if related:
            relevant[index]={**row,'related_images':related}
    images = _verified_images(root, [*relevant, *(part for row in relevant for part in row.get('related_images',[]))])
    blocks, direct = _contexts(structure)
    context = {row["resource_id"]: _context_bits(row, blocks, direct) for row in relevant}
    originals={row["source_id"]:_str(row.get("text")) for row in _objects(source.get("sources"))}
    ready_visual = []
    ready_nonvisual = []
    unavailable = {}
    for row in relevant:
        rid, kind = row["resource_id"], _kind(row)
        if row.get("status", "ok") != "ok":
            unavailable[rid] = "unreadable" if kind in _VISUAL_KINDS else "pending"
        elif kind in _VISUAL_KINDS:
            if row.get("media_sha256") in images:
                ready_visual.append(row)
            else:
                unavailable[rid] = "unreadable"
        else:
            ready_nonvisual.append(row)

    async def inspect(batch: list[dict]):
        attachment_rows=[*batch,*(part for row in batch for part in row.get('related_images',[]))]
        attached = {row["media_sha256"]: images[row["media_sha256"]] for row in attachment_rows
                    if row.get("media_sha256") in images}
        payload = _visual_payload(batch, attached, context)
        async def request(groups, attempt):
            ids=[rid for group in groups for rid in group["resource_ids"]]
            retry_rows=[row for row in batch if row["resource_id"] in ids]
            hashes={row.get("media_sha256") for row in [*retry_rows,*(part for row in retry_rows
                    for part in row.get("related_images",[]))]}
            images_for_retry={sha:data for sha,data in attached.items() if sha in hashes}
            payload=_visual_payload(retry_rows,images_for_retry,context)
            if originals:
                payload["source_excerpts"]=_explanation_source_context(payload["semantic_groups"],originals)
            if attempt:
                payload["response_retry"]={"attempt":attempt+1,"return_resource_ids":ids}
            raw,failure=await _request(ask,"A2",payload,images_for_retry)
            labels=_visual_reply_labels(raw,{r["resource_id"]:r for r in retry_rows},images_for_retry)
            return raw,failure,labels

        groups, known, labels, issue = await _read_explanation_batch("A2", payload["semantic_groups"], request)
        labels=list({(row['resource_id'],row['label_id']):row for row in labels}.values())
        if labels and on_labels:
            await on_labels(labels)
        return groups, known, labels, issue

    groups = []
    known = {}
    labels = []
    issues = []
    for task in asyncio.as_completed(inspect(batch) for seq,n in ((ready_visual,3),(ready_nonvisual,8))
                                      for batch in _chunks(seq,n,12000)):
        found,covers,named,issue=await task
        groups.extend(found)
        known.update(covers)
        labels.extend(named)
        if issue:
            issues.append(issue)
        if on_partial and found:
            await on_partial({'groups':list(groups),'coverage_by_resource':_coverage(relevant,known,unavailable),
                              'verified_labels':list(labels),'issues':list(issues)})
    return {"groups": groups, "coverage_by_resource": _coverage(relevant, known, unavailable),
            "verified_labels": labels, "issues": issues}


def _original_assets(source: dict) -> list[dict]:
    assets = source.get("assets") or {}
    used = defaultdict(list)
    for row in _objects(source.get("sources")):
        for aid in _strings(row.get("asset_refs")):
            used[aid].append(row.get("source_id"))
    out = []
    for aid, sids in used.items():
        asset = assets.get(aid) if isinstance(assets, dict) else None
        if not isinstance(asset, dict) or asset.get("kind") not in _VISUAL_KINDS | {"page_visual"}:
            continue
        out.append({**asset, "resource_id": aid, "asset_id": aid,
                    "asset_sha256": asset.get("sha256"), "source_ids": sorted(set(x for x in sids if isinstance(x, str)))})
    return out


def build_visual_catalog(sources: Iterable[dict]) -> dict[tuple, list[dict]]:
    by_file = defaultdict(dict)
    for source in sources:
        for row in _original_assets(source):
            if row.get("kind") in _VISUAL_KINDS:
                identity = (row["asset_id"], row["asset_sha256"])
                # Keep the pixel request independent of the selected article/scope.
                canonical = {k: row.get(k) for k in ("resource_id", "asset_id", "asset_sha256",
                                                     "kind", "file_id", "locator")}
                canonical["raw_content"] = ""
                by_file[row.get("file_id") or ""][identity] = canonical
    catalog = {}
    for _, file in sorted(by_file.items()):
        for batch in _chunks((file[k] for k in sorted(file)), 3, 12000):
            for row in batch:
                catalog[(row["asset_id"], row["asset_sha256"])] = batch
    return catalog


async def original_labels(root: Path, sample: str, source: dict, ask, catalog: dict | None = None,
                          on_labels=None) -> dict:
    use = _original_assets(source)
    catalog = catalog if catalog is not None else build_visual_catalog([source])
    available = {row["asset_id"]: row for row in use if row["kind"] in _VISUAL_KINDS}
    batches = {}
    for rid, row in available.items():
        seq = catalog.get((rid, row["asset_sha256"]))
        if seq:
            batches.setdefault(_hash(seq), seq)

    async def inspect(batch: list[dict]):
        images = _verified_images(root, batch)
        supplied = [row for row in batch if row["asset_sha256"] in images]
        if not supplied:
            return [], {}, False
        payload = _visual_payload(supplied, images, {})
        payload["purpose"] = "Only identify verified foreign labels in these original figures. "
        payload["purpose"] += "These assets are not additional chapter explanation blocks."
        raw, failure = await _request(ask, "A2", payload, images)
        labels = _visual_reply_labels(raw, {r["resource_id"]: r for r in supplied}, images)
        labels = [label for label in labels if label["resource_id"] in available]
        if labels and on_labels:
            await on_labels(labels)
        states = dict.fromkeys((r["resource_id"] for r in supplied), "pending" if failure else "inspected")
        return labels, states, failure

    labels = source_labels(source)
    states = {}
    issues = []
    for found, updated, failure in await asyncio.gather(*(inspect(b) for b in batches.values())):
        labels.extend(found)
        states.update(updated)
        if failure:
            issues.append({"stage": "A2", "resource_ids": sorted(set(updated) & available.keys()), "reason": failure})
    seen = {x["asset_id"] for x in labels}
    return {"verified_labels": labels, "coverage_by_asset": [{
                "asset_id": row["asset_id"], "source_ids": row["source_ids"],
                "status": ("verified_labels" if row["asset_id"] in seen
                           else states.get(row["asset_id"], "fallback_page" if row["kind"] == "page_visual"
                                           else "unreadable")),
            } for row in use], "issues": issues}


async def translate_labels(root: Path, sample: str, labels: list[dict], ask, known_terms=()) -> dict:
    # One stable pixel-and-label identity can point to multiple resource occurrences.
    grouped = defaultdict(list)
    for label in _objects(labels):
        sha = label.get("asset_sha256")
        original = _str(label.get("original"))
        if (not isinstance(sha, str) or not _SHA.fullmatch(sha) or not original.strip()
                or not _OTHER_LANGUAGE.search(original)):
            continue
        key = "label-" + _hash(sha, original.strip())[:22]
        grouped[key].append(label)
    by_asset = defaultdict(list)
    for key, aliases in grouped.items():
        primary = aliases[0]
        sha = primary["asset_sha256"]
        by_asset[sha].append({"resource_id": "img-" + sha, "label_id": key, "original": primary["original"],
                              "location_hint": primary.get("location_hint"), "asset_sha256": sha})

    async def inspect(batch: list[dict]):
        supplied = {row["label_id"]: row for row in batch}
        raw, error = await _request(ask, "B2", {"labels": batch, "known_terms": list(known_terms)})
        values = {}
        for row in _objects(raw.get("translations")):
            lid = row.get("label_id")
            base = supplied.get(lid) if isinstance(lid, str) else None
            status = row.get("status")
            text = _str(row.get("translated"))
            if (base and row.get("resource_id") == base["resource_id"] and row.get("original") == base["original"]
                    and text.strip() and status in {"complete", "uncertain"}):
                values[lid] = {"translated": text, "status": status}
        return values, {"stage": "B2", "label_ids": sorted(supplied), "reason": error} if error else None

    values = {}
    issues = []
    for found, issue in await asyncio.gather(*(inspect(b) for _, seq in sorted(by_asset.items())
                                              for b in _chunks(sorted(seq, key=lambda x: x["label_id"]), 24, 9300))):
        values.update(found)
        if issue:
            issues.append(issue)
    entries = [label | values[key] for key, aliases in grouped.items() if key in values for label in aliases]
    missing = [row["label_id"] for key, aliases in grouped.items() if key not in values for row in aliases]
    return {"schema_version": "translation-visuals-v1", "sample_id": sample, "translations": entries,
            "unresolved_label_ids": missing,
            "counts": {"unique_labels": len(grouped),
                       "translated": sum(v["status"] == "complete" for v in values.values()),
                       "uncertain": sum(v["status"] == "uncertain" for v in values.values()),
                       "unresolved": len(grouped) - len(values)},
            "issues": issues}


def _section_state(root: Path, sample: str, structure: dict, card: dict) -> dict:
    unit = root / "inputs" / sample
    metadata = _read(unit / "source-index.json")
    base = _read(root / "upstream07" / sample / "run.json")
    stored_path = unit / "reader-context.json"
    stored = _read(stored_path) if stored_path.is_file() else {}
    chapter = card.get("chapter") or {}
    known = _objects(stored.get("existing_terms") or metadata.get("existing_terms")
                     or card.get("existing_terms"))
    terms = {str(t.get("id") or t.get("term_id")): t
             for t in known if t.get("id") or t.get("term_id")}
    part_ids = {x["part_id"] for x in _structure_items(structure, "parts") if x.get("part_id")}
    blocks = {x["block_id"]: x for x in _structure_items(structure, "blocks") if x.get("block_id")}
    sources = {x["source_id"]: x for x in _objects(metadata.get("sources")) if x.get("source_id")}
    return {"mode": base.get("mode") or card.get("mode"),
            "title": chapter.get("title") or (card.get("question") or {}).get("title") or sample,
            "previous": card.get("previous") or {},
            "next_chapter": stored.get("confirmed_next_chapter") or card.get("confirmed_next_chapter"),
            "terms": terms, "existing_quizzes": _objects(stored.get("existing_quizzes")),
            "body_fused_sha": base.get("fused_sha256"), "parts": part_ids,
            "blocks": blocks, "sources": sources, "direct": _source_links(structure)}


def _part_contents(structure: dict, state: dict) -> list[dict]:
    parts = []
    for p in _structure_items(structure, "parts"):
        pid = p.get("part_id")
        if pid not in state["parts"]:
            continue
        blocks = []
        for bid in _strings(p.get("block_ids")):
            b = state["blocks"].get(bid)
            if not b or b.get("surface", "body") != "body":
                continue
            blocks.append({"block_id": bid, "markdown": _str(b.get("markdown") or b.get("text")),
                           "source_ids": state["direct"].get(bid, [])})
        if blocks:
            parts.append({"part_id": pid, "title": _str(p.get("title")), "blocks": blocks})
    return parts


def _valid_ids(values, ids) -> list[str]:
    return list(dict.fromkeys(v for v in _strings(values) if v in ids))


def _guidance_result(raw: dict, state: dict) -> dict:
    result = raw.get("guidance") if isinstance(raw.get("guidance"), dict) else {}
    simple = state["mode"] == "ANSWER"
    order = {row["part_id"]: index for index, row in enumerate(state["shown_parts"])}
    route_by_part = {}
    for row in _objects(result.get("reading_flow")):
        pid = row.get("part_id")
        if not isinstance(pid, str) or pid not in order or pid in route_by_part:
            continue
        title = _str(row.get("title"))
        if not title.strip():
            continue
        route_by_part[pid] = {"part_id": pid, "title": title, "focus": _str(row.get("focus"))}
    recap_by_part = {}
    if not simple:
        for row in _objects(result.get("part_recaps")):
            pid = row.get("part_id")
            if not isinstance(pid, str) or pid not in order or pid in recap_by_part:
                continue
            fields = {key: _str(row.get(key)) for key in ("background", "question", "conclusion",
                                                           "causes", "evidence", "application", "synthesis", "next_transition")}
            if order[pid] == len(order)-1 and not state.get("next_chapter"):
                fields["next_transition"] = ""
            if any(text.strip() for text in fields.values()):
                status = row.get("status")
                recap_by_part[pid] = {"part_id": pid, **fields, "status": status if status in {
                    "complete", "partial", "needs_review", "uncertain", "candidate"} else "candidate"}
    return {
        "schema_version": "chapter-guidance-v1",
        "chapter_intro": "" if simple else _str(result.get("chapter_intro")),
        "chapter_outro": "" if simple else _str(result.get("chapter_outro")),
        "reading_flow": [route_by_part[pid] for pid in order if pid in route_by_part] if not simple or len(order) > 1 else [],
        "part_recaps": [recap_by_part[pid] for pid in order if pid in recap_by_part],
        "uncertainties": _items(result.get("uncertainties")),
    }


def _term_results(raw: dict, state: dict) -> list[dict]:
    results = []
    seen = set()
    for row in _objects(raw.get("terms")):
        name = _str(row.get("name")).strip()
        if not name or row.get("action") not in {"reuse_existing", "new"}:
            continue
        reuse = _str(row.get("reuse_id"))
        adopted = state["terms"].get(reuse) if row["action"] == "reuse_existing" else None
        identity = reuse if adopted else "term-" + _hash(state["body_fused_sha"], name, _str(row.get("definition")))[:20]
        if identity in seen:
            continue
        seen.add(identity)
        base = adopted or row
        definition = _str(base.get("definition") or row.get("definition"))
        intuition = _str(base.get("intuition") or row.get("intuition"))
        if not (definition.strip() or intuition.strip()):
            continue
        part_ids = _valid_ids(row.get("part_ids"), state["parts"])
        block_ids = _valid_ids(row.get("block_ids"), state["blocks"])
        if not block_ids and part_ids:
            for block in state["blocks"].values():
                words = [name, *_strings(row.get("aliases"))]
                body = _str(block.get("markdown") or block.get("text")).casefold()
                if block.get("part_id") in part_ids and any(word.casefold() in body for word in words if len(word) > 1):
                    block_ids.append(block["block_id"])
        part_ids = list(dict.fromkeys(part_ids + [state["blocks"][bid].get("part_id")
                                                 for bid in block_ids
                                                 if state["blocks"][bid].get("part_id") in state["parts"]]))
        source_ids = list(dict.fromkeys(_valid_ids(row.get("source_ids"), state["sources"]) +
                                       [sid for bid in block_ids for sid in state["direct"].get(bid, [])
                                        if sid in state["sources"]]))
        status = ("reused" if adopted else "needs_review"
                  if row.get("status") == "needs_review" or row["action"] == "reuse_existing" or not block_ids
                  else "candidate")
        results.append({
            "term_id": identity, "action": "reuse_existing" if adopted else "new",
            "reuse_id": reuse if adopted else None, "name": _str(base.get("name") or base.get("term")) or name,
            "aliases": list(dict.fromkeys(_strings(base.get("aliases")) + _strings(row.get("aliases")))),
            "definition": definition, "intuition": intuition,
            "chinese_name": _str(row.get("chinese_name")), "english_name": _str(row.get("english_name")),
            "abbreviation": _str(row.get("abbreviation")), "part_ids": part_ids,
            "block_ids": block_ids, "source_ids": source_ids, "status": status,
        })
    return results


async def guidance(root: Path, sample: str, structure: dict, card: dict, ask) -> dict:
    state = _section_state(root, sample, structure, card)
    parts = _part_contents(structure, state)
    state["shown_parts"] = parts
    failure = ""
    if parts:
        payload = {
            "mode": state["mode"], "chapter_title": state["title"], "parts": parts,
            "chapter_goals": card.get("goals") or {}, "previous_saved_chapter": state["previous"],
            "confirmed_next_chapter": state["next_chapter"], "existing_terms": list(state["terms"].values()),
        }
        raw, failure = await _request(ask, "F12", payload)
    else:
        raw = {}
    g = _guidance_result(raw, state)
    terms = _term_results(raw, state)
    completed = {r["part_id"] for r in g["part_recaps"]}
    return {"guidance": g, "terms": terms,
            "counts": {"fresh_terms": sum(r["status"] != "reused" for r in terms),
                       "reused_terms": sum(r["status"] == "reused" for r in terms),
                       "candidate_terms": sum(r["status"] == "candidate" for r in terms),
                       "recaps": len(g["part_recaps"]), "route_items": len(g["reading_flow"])},
            "unresolved_part_recaps": [row["part_id"] for row in parts if row["part_id"] not in completed]
                                      if state["mode"] == "NORMAL" else [],
            "issues": [{"stage": "F12", "reason": failure}] if failure else []}


def _choice_index(answer: Any, options: list[str]) -> int | None:
    if isinstance(answer, dict):
        answer = answer.get("correct_index", answer.get("option"))
    if type(answer) is int:
        return answer
    if isinstance(answer, str):
        if answer in options:
            return options.index(answer)
        if answer.isascii() and answer.isdigit():
            return int(answer)
        if len(answer) == 1 and "A" <= answer.upper() <= "F":
            return ord(answer.upper()) - ord("A")
    return None


def _native_quiz(row: dict, state: dict, part: dict, provenance: str) -> dict | None:
    kind = row.get("type")
    stem, explanation = _str(row.get("question") or row.get("prompt")), _str(row.get("explanation"))
    if not stem.strip() or not explanation.strip():
        return None
    if kind in {"choice", "true_false"}:
        options = _items(row.get("options"))
        if not 2 <= len(options) <= 6 or not all(isinstance(x, str) and x.strip() for x in options):
            return None
        if len(set(options)) != len(options):
            return None
        correct = _choice_index(row.get("correct_index", row.get("answer")), options)
        if correct is None or not 0 <= correct < len(options) or (kind == "true_false" and len(options) != 2):
            return None
        reference = {}
    elif kind == "recall":
        text = _str(row.get("reference_answer") or row.get("answer"))
        if not text.strip():
            return None
        options, correct = ["还没答对", "答对了"], 1
        reference = {"reference_answer": text}
    else:
        return None
    pid = part["part_id"]
    allowed_blocks = {b["block_id"] for b in part["blocks"]}
    blocks = _valid_ids(row.get("block_ids"), allowed_blocks)
    allowed_sources = {sid for b in part["blocks"] for sid in b["source_ids"]}
    sources = _valid_ids(row.get("source_ids"), allowed_sources)
    identity = _str(row.get("id")) if provenance == "reused" else "quiz-" + _hash(pid, stem, options, correct)[:22]
    if not identity:
        return None
    return {"id": identity, "type": kind, "part_id": pid, "block_ids": blocks,
            "source_ids": sources, "placement": {"kind": "part", "part_id": pid},
            "prompt": stem, "question": stem, "options": options, "correct_index": correct,
            "explanation": explanation, **reference, "status": "candidate" if provenance == "fresh" else "reused",
            "provenance": provenance}


async def practice(root: Path, sample: str, structure: dict, card: dict, ask) -> dict:
    state = _section_state(root, sample, structure, card)
    parts = _part_contents(structure, state)
    by_pid = {p["part_id"]: p for p in parts}
    contained = {}
    for row in state["existing_quizzes"]:
        part = by_pid.get(row.get("part_id"))
        if not part or row.get("body_fused_sha") != state["body_fused_sha"] or row.get("deleted_at"):
            continue
        ready = _native_quiz(row, state, part, "reused")
        if ready:
            contained.setdefault(part["part_id"], ready)
    if state["mode"] != "NORMAL":
        return {"questions": list(contained.values()), "unresolved_part_ids": [],
                "status": "not_applicable", "counts": {"fresh": 0, "reused": len(contained)},
                "issues": []}

    missing = [p for p in parts if p["part_id"] not in contained]

    async def inspect(batch: list[dict]):
        payload = {"mode": state["mode"], "chapter_title": state["title"], "parts": batch,
                   "known_terms": list(state["terms"].values()),
                   "native_question_contract": {
                       "choice": "2 to 6 distinct options and zero-based answer",
                   }}
        raw, failure = await _request(ask, "F3", payload)
        asked = {p["part_id"]: p for p in batch}
        accepted = {}
        for row in _objects(raw.get("questions")):
            pid = row.get("part_id")
            if not isinstance(pid, str) or pid not in asked or pid in accepted:
                continue
            q = _native_quiz(row, state, asked[pid], "fresh")
            if q:
                accepted[pid] = q
        return accepted, {"stage": "F3", "part_ids": list(asked), "reason": failure} if failure else None

    issues = []
    for accepted, issue in await asyncio.gather(*(inspect(b) for b in _chunks(missing, 12, 21000))):
        contained.update(accepted)
        if issue:
            issues.append(issue)
    rows = [contained[p["part_id"]] for p in parts if p["part_id"] in contained]
    unresolved = [p["part_id"] for p in parts if p["part_id"] not in contained]
    return {"questions": rows, "unresolved_part_ids": unresolved, "status": "partial" if unresolved else "complete",
            "counts": {"fresh": sum(r["provenance"] == "fresh" for r in rows),
                       "reused": sum(r["provenance"] == "reused" for r in rows)},
            "issues": issues}


# A fragment can be proved to be notation without inferring the surrounding language.
_PDF_GLYPH = r"[A-Za-z\u0370-\u03ff\u2100-\u214f]"
_GLYPH_FRAGMENT = re.compile(_PDF_GLYPH + r"(?:[_^]?\d+|['′″₀-₉⁰-⁹])*\Z")
_GLYPH_SEQUENCE = re.compile(r"(?:" + _PDF_GLYPH + r"(?:[_^]?\d+|['′″₀-₉⁰-⁹])*|"
                             r"\d+(?:[.,]\d+)?|[()\[\]{}+×÷/\-=‹›<>≈≠∞∑∏√∈∉≤≥→←∂∫∧∨·:;_,^])+"
                             r"\Z")
_MATH_OPERATOR = re.compile(r"[=≈≠∞∑∏√∈∉≤≥→←∂∫∧∨]")


def _notation_correction(source: dict, unit: dict) -> str | None:
    position = unit.get("position") or {}
    if position.get("type") != "pdf_page" or not unit.get("refs"):
        return None
    assets = source.get("assets") or {}
    linked = [assets.get(aid, {}) for aid in _strings(unit.get("asset_refs"))]
    direct = {r.get("kind") for r in linked if isinstance(r, dict)} & {"formula", "vector", "table"}
    page = any(r.get("kind") == "page_visual" for r in linked if isinstance(r, dict))
    text = _str(unit.get("text")).strip()
    if _GLYPH_FRAGMENT.fullmatch(text):
        if direct:
            return "pdf_verified_" + sorted(direct)[0] + "_notation"
        if page and text.casefold() not in {"a", "i"}:
            return "pdf_isolated_symbol_with_page_evidence"
    joined = re.sub(r"\s+", "", text)
    # Avoid treating regular English words and titles as multiplication of letters.
    if len(joined) > 1 and not re.search(r"[A-Za-z\u0370-\u03ff]{2,}", joined):
        if _GLYPH_SEQUENCE.fullmatch(joined) and (direct or page and _MATH_OPERATOR.search(joined)):
            return "pdf_notation_sequence_with_source_evidence"
    return None


def reproject_math_glyphs(source: dict, previous: dict) -> dict:
    """Keep the original receipts and reconcile only evidenced untranslated glyphs."""
    unresolved = _strings(previous.get("unresolved_source_ids"))
    by_id = {u["source_id"]: u for u in source_units(source)}
    corrected = {}
    for sid in unresolved:
        unit = by_id.get(sid)
        why = _notation_correction(source, unit) if unit else None
        if why:
            corrected[sid] = (unit, why)
    if not corrected:
        return previous
    links = list(_objects(previous.get("links")))
    links.extend({"source_id": sid, "translation_id": sid, "relation": "preserved_math",
                  "status": "complete", "source_version": unit["source_version"],
                  "original_sha256": unit["original_sha256"],
                  "provenance": "script_source_notation"} for sid, (unit, _) in corrected.items())
    order = {sid: n for n, sid in enumerate(by_id)}
    links.sort(key=lambda row: order.get(row.get("source_id"), len(order)))
    counts = dict(previous.get("counts", {}))
    count = len(corrected)
    counts.update(needed=max(0, counts.get("needed", len(unresolved)) - count),
                  unresolved=max(0, counts.get("unresolved", len(unresolved)) - count),
                  preserved_math=counts.get("preserved_math", 0) + count)
    ledger = _objects(previous.get("classification_corrections"))
    return {**previous, "links": links, "counts": counts,
            "unresolved_source_ids": [sid for sid in unresolved if sid not in corrected],
            "classification_corrections": ledger + [
                {"source_id": sid, "old_status": "unresolved", "new_relation": "preserved_math", "reason": why}
                for sid, (_, why) in corrected.items()
            ]}
