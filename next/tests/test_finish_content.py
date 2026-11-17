import asyncio
from hashlib import sha256
import io
import json

from PIL import Image
from manualreader.stages.finish import content

from manualreader.stages.finish.content import (
    archived_math_responses,
    build_translation_catalog,
    build_visual_catalog,
    explain_math,
    explain_visual,
    guidance,
    original_labels,
    practice,
    replay_math_explanations,
    reproject_math_glyphs,
    source_identity,
    translate,
    translate_labels,
)


def _unit(sid, text, kind="paragraph", number=1, assets=None):
    return {
        "source_id": sid, "file_id": "source", "kind": kind, "text": text,
        "position": {"type": "pdf_page", "start": number, "end": number},
        "refs": [{"bbox": [1, 2, 3, 4]}], "asset_refs": assets or [],
        "registered_unit_ids": ["fragment-" + sid],
    }


def _source(*units, version="one", assets=None):
    return {"sources": list(units), "source_files": {"source": version},
            "snapshot_sha256": "chapter-snapshot", "assets": assets or {}}


def _structure(*resources):
    return {
        "parts": [{"part_id": "p1", "title": "第一部分", "block_ids": ["b1", "b2"]}],
        "blocks": [
            {"block_id": "b1", "part_id": "p1", "markdown": "原文此处使用 IPC", "surface": "body"},
            {"block_id": "b2", "part_id": "p1", "markdown": "这里的变量含义改变", "surface": "body"},
        ],
        "source_edges": [
            {"source_id": "source-1", "block_id": "b1", "relation_type": "direct_semantic"},
            {"source_id": "source-2", "block_id": "b1", "relation_type": "navigation_only"},
        ],
        "resources": list(resources),
    }


def _run_state(root, source, mode="NORMAL", existing=None):
    for relative, name, data in [
        ("inputs", "source-index.json", source),
        ("upstream07", "run.json", {"mode": mode, "fused_sha256": "digest"}),
        ("inputs", "reader-context.json", existing or {}),
    ]:
        stage = root / relative / "sample"
        stage.mkdir(parents=True, exist_ok=True)
        (stage / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_translation_catalog_shares_batches_and_keeps_version_boundaries(tmp_path):
    a = _unit("a", "A first sentence", number=1)
    b = _unit("b", "A second sentence", number=2)
    current, overlapping = _source(a, b), _source(b)
    other_version = _source(a, version="two")
    catalog = build_translation_catalog([overlapping, other_version, current])
    one = catalog.by_source[source_identity(current, a)]
    both = catalog.by_source[source_identity(overlapping, b)]
    assert one == both
    assert [s["source_id"] for s in one["units"]] == ["a", "b"]
    assert catalog.by_source[source_identity(other_version, a)]["source_version"] == "two"

    async def ask(stage, payload, images=None):
        assert stage == "B1"
        return {"groups": [{"translation_group_id": "g1", "segments": [
            {"source_id": "b", "translation_id": "t", "text": "另一个句子",
             "relation": "translated", "status": "complete"},
            {"source_id": "a", "translation_id": "x", "text": "第一个句子",
             "relation": "translated", "status": "uncertain"},
            {"source_id": "wrong", "translation_id": "x", "text": "不属于本轮",
             "relation": "translated", "status": "complete"},
        ]}]}

    out = asyncio.run(translate(tmp_path, "other-sample", overlapping, ask, catalog))
    assert out["counts"]["fresh"] == 1
    assert out["groups"][0]["segments"][0]["source_id"] == "b"
    assert out["links"][0]["translation_id"].startswith("tr-")
    assert len(out["links"]) == 1


def test_pdf_notation_projection_keeps_real_english_unresolved():
    symbol = _unit("symbol", "H", assets=["asset-figure"])
    prose = _unit("text", "Introduce n variables", assets=["asset-figure"])
    original = _source(symbol, prose, assets={"asset-figure": {"kind": "vector"}})
    old = {"unresolved_source_ids": ["symbol", "text"], "links": [],
           "counts": {"needed": 2, "unresolved": 2, "preserved_math": 0}, "issues": []}
    new = reproject_math_glyphs(original, old)
    assert new["counts"] == {"needed": 1, "unresolved": 1, "preserved_math": 1}
    assert new["unresolved_source_ids"] == ["text"]
    assert new["links"][0]["relation"] == "preserved_math"
    assert old["unresolved_source_ids"] == ["symbol", "text"]
    assert reproject_math_glyphs(original, new) == new


def test_explanation_shares_identical_formula_within_one_context(tmp_path):
    base = {"kind": "formula_display", "raw_content": r"\(x\)", "status": "ok"}
    records = [
        {**base, "resource_id": "a", "block_id": "b1"},
        {**base, "resource_id": "b", "block_id": "b1"},
        {**base, "resource_id": "c", "block_id": "b2"},
    ]

    async def ask(stage, payload, images=None):
        assert len(payload["semantic_groups"]) == 2
        groups = []
        for group in payload["semantic_groups"]:
            assert "source-2" not in group["source_ids"]
            groups.append({"group_id": group["group_id"], "kind": "math",
                           "resource_ids": [group["resource_ids"][0]], "overview": "变量", "summary": "解释变量的用途",
                           "components": [{"raw": "x", "meaning": "当前块使用的变量", "certainty": "known"}],
                           "composition": [], "conditions": [], "status": "explained"})
        return {"groups": groups}

    result = asyncio.run(explain_math(tmp_path, "chapter", _structure(*records), ask))
    by_id = {r["resource_id"]: r for r in result["coverage_by_resource"]}
    assert by_id["a"]["group_id"] == by_id["b"]["group_id"]
    assert by_id["b"]["status"] == "covered_by_group"
    assert by_id["a"]["group_id"] != by_id["c"]["group_id"]


def test_archived_math_batch_validates_cross_block_group_and_unresolved(tmp_path):
    structure = _structure(
        {"kind": "formula_display", "raw_content": r"\((x,y)\)", "resource_id": "a", "block_id": "b1"},
        {"kind": "formula_display", "raw_content": r"\((0,0)\)", "resource_id": "b", "block_id": "b2"},
        {"kind": "formula_display", "raw_content": r"\(k\)", "resource_id": "missing", "block_id": "b2"},
    )
    captured = {}

    async def capture(stage, payload, images=None):
        if not captured:
            captured.update(payload)
        return {"groups": []}

    old = asyncio.run(explain_math(tmp_path, "coords", structure, capture))
    folder = tmp_path / "requests" / "A1" / "request"
    folder.mkdir(parents=True)
    (folder / "input.json").write_text(json.dumps(
        {"stage": "A1", "id": "request", "input": captured}), encoding="utf-8")
    response = {
        "group_id": captured["group_id"], "resource_ids": ["a", "b"], "overview": "坐标系", "summary": "建立坐标基准",
        "components": [{"component_id": "a", "raw": r"\((x,y)\)", "meaning": "坐标", "certainty": "known"}],
        "status": "explained",
    }
    (folder / "result.json").write_text(json.dumps({
        "response": {"status": "completed"},
        "result": {"groups": [response],
                   "unresolved_resource_ids": ["missing"]},
    }), encoding="utf-8")
    archive = archived_math_responses(tmp_path)
    fixed, receipt = replay_math_explanations(structure, old, archive)
    assert receipt["newly_explained_ids"] == ["a", "b"]
    assert fixed["groups"][0]["question_context"]["block_ids"] == ["b1", "b2"]
    assert fixed["coverage_by_resource"][-1]["status"] == "pending"
    assert replay_math_explanations(structure, fixed, archive)[1]["changed_ids"] == []
    changed_text = {**structure, "blocks": [{**structure["blocks"][0], "markdown": "修改了送入模型的上下文"},
                                            structure["blocks"][1]]}
    assert replay_math_explanations(changed_text, old, archive)[1]["changed_ids"] == []


def test_math_combination_preserves_distinct_model_definitions_for_repeated_symbol(tmp_path):
    same = {"kind": "formula_display", "raw_content": r"\(a\)", "status": "ok"}
    structure = _structure({**same, "resource_id": "left", "block_id": "b1"},
                           {**same, "resource_id": "right", "block_id": "b2"})

    def response(payload, ambiguous):
        definitions = [{"component_id": "module-a", "raw": r"\(a\)",
                        "meaning": "同一模块", "certainty": "known"}]
        if ambiguous:
            definitions.append({**definitions[0], "meaning": "另一个独立变量"})
        return {"groups": [{"group_id": payload["group_id"], "resource_ids": payload["resource_ids"],
                            "status": "explained", "overview": "模块与输入", "summary": "符号在两处指同一输入",
                            "components": definitions}]}

    async def clear(stage, payload, images=None):
        return response(payload, ambiguous=False)

    async def ambiguous(stage, payload, images=None):
        return response(payload, ambiguous=True)

    accepted = asyncio.run(explain_math(tmp_path, "clear", structure, clear))
    distinct = asyncio.run(explain_math(tmp_path, "ambiguous", structure, ambiguous))
    assert [r["status"] for r in accepted["coverage_by_resource"]] == ["explained", "explained"]
    assert [r["status"] for r in distinct["coverage_by_resource"]] == ["explained", "explained"]
    assert [r["meaning"] for r in distinct["groups"][0]["components"]] == ["同一模块", "另一个独立变量"]


def test_resource_retry_keeps_existing_answers_and_only_requests_missing_ids(monkeypatch):
    async def no_delay(_):
        return None
    monkeypatch.setattr(content.asyncio,"sleep",no_delay)

    async def exercise(kind,index):
        stage="A1" if kind=="formula_display" else "A2"
        first,second=f"{index}-1",f"{index}-2"
        expected=[{"group_id":f"group-{rid}","resource_ids":[rid],
                   "resources":[{"resource_id":rid,"kind":kind}]} for rid in (first,second)]
        seen=[]

        async def request(groups,attempt):
            ids=[rid for group in groups for rid in group["resource_ids"]]
            seen.append(ids)
            if attempt==0:
                return {},"connection_lost",[]
            rid=first if attempt==1 else second
            row={"group_id":f"group-{rid}","resource_ids":[rid],"status":"explained",
                 "overview":"说明可确认的组成关系", "components":[
                      {"raw":"记号","meaning":"可以说明本处的作用，来源未注明外部定义","certainty":"unknown"}]}
            invalid={**row,"resource_ids":["not-registered"]}
            raw={"groups":[invalid,row]} if attempt==1 else {"groups":[row]}
            return raw,"",[]

        groups,known,_,issue=await content._read_explanation_batch(stage,expected,request,math=stage=="A1")
        assert seen==[[first,second],[first,second],[second]]
        assert sorted(known)==sorted([first,second])
        assert all(v["status"]=="explained" for v in known.values())
        assert groups[0]["components"][0]["certainty"]=="uncertain"
        assert not issue

    async def batch():
        await asyncio.gather(*(exercise(kind,index) for kind in
            ("formula_display","image","table","mermaid","code") for index in range(20)))
    asyncio.run(batch())


def test_visual_and_ocr_require_supplied_source_pixels(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    pixel = io.BytesIO()
    Image.new("RGB", (1, 1)).save(pixel, format="PNG")
    image = pixel.getvalue()
    digest = sha256(image).hexdigest()
    (media / (digest + ".png")).write_bytes(image)
    bitmap = {"resource_id": "real", "kind": "image", "media_sha256": digest, "status": "ok"}
    broken = b"\x89PNG\r\n\x1a\nbroken-image"
    broken_sha = sha256(broken).hexdigest()
    (media / (broken_sha + ".png")).write_bytes(broken)
    missing = {"resource_id": "missing", "kind": "image", "media_sha256": broken_sha, "status": "ok"}
    source = _source(_unit("image-source", "", assets=["real"]), assets={"real": {
        "asset_id": "real", "sha256": digest, "kind": "image", "file_id": "source",
    }})
    callbacks = []

    async def on_labels(rows):
        callbacks.extend(rows)

    async def ask(stage, payload, images=None):
        assert stage == "A2"
        assert payload["resource_ids"] == ["real"]
        assert images == {digest: image}
        return {"groups": [], "verified_labels": [
            {"resource_id": "real", "label_id": "v1", "original": "Memory",
             "location_hint": "upper left", "certainty": "known"},
            {"resource_id": "missing", "label_id": "fake", "original": "Impossible",
             "location_hint": "unknown", "certainty": "known"},
        ]}

    first="![1](one.png)"
    second="![2](two.png)"
    raw=first+"\n"+second
    span1={**bitmap,"block_id":"b1","start":0,"end":len(first),"raw_content":first}
    span2={**missing,"block_id":"b1","start":len(first)+1,"end":len(raw),"raw_content":second}
    shape=_structure(span1,span2)
    shape['blocks'][0].update(start=0,markdown=raw)
    result = asyncio.run(explain_visual(tmp_path,"sample",shape,source,ask,on_labels))

    assert result["coverage_by_resource"][1]["status"] == "unreadable"
    assert len(callbacks) == 1
    catalog = build_visual_catalog([source])
    extracted = asyncio.run(original_labels(tmp_path, "sample", source, ask, catalog))
    assert extracted["coverage_by_asset"][0]["status"] == "verified_labels"
    assert extracted["verified_labels"][0]["asset_sha256"] == digest


def test_visual_translation_reuses_a_verified_label_across_resources(tmp_path):
    common = {"asset_sha256": "1" * 64, "original": "Read operations", "location_hint": "y axis"}
    labels = [common | {"resource_id": "res-a", "label_id": "a"},
              common | {"resource_id": "res-b", "label_id": "b"}]
    received = []

    async def ask(stage, payload, images=None):
        received.extend(payload["labels"])
        return {"translations": [row | {"translated": "读取操作", "status": "complete"}
                                 for row in payload["labels"]]}

    answer = asyncio.run(translate_labels(tmp_path, "sample", labels, ask))
    assert len(received) == 1
    assert {x["label_id"] for x in answer["translations"]} == {"a", "b"}


def test_answer_mode_uses_no_fabricated_recap_or_quiz(tmp_path):
    _run_state(tmp_path, _source(_unit("source-1", "内容")), "ANSWER")
    calls = []

    async def ask(stage, payload, images=None):
        calls.append(stage)
        return {"guidance": {"chapter_intro": "此处不应有章首", "chapter_outro": "此处不应有章末",
                             "part_recaps": [{"part_id": "p1", "background": "该模式不需要回顾"}]},
                "terms": []}

    answer = asyncio.run(guidance(tmp_path, "sample", _structure(), {}, ask))
    assert answer["guidance"]["chapter_intro"] == answer["guidance"]["chapter_outro"] == ""
    assert answer["guidance"]["part_recaps"] == []
    assert asyncio.run(practice(tmp_path, "sample", _structure(), {}, ask))["status"] == "not_applicable"
    assert calls == ["F12"]


def test_existing_term_and_question_require_real_lineage(tmp_path):
    existing = {
        "existing_quizzes": [{"id": "old", "body_fused_sha": "other", "part_id": "p1"}],
        "existing_terms": [{"id": "known", "name": "IPC", "definition": "旧术语定义"}],
    }
    _run_state(tmp_path, _source(_unit("source-1", "内容")), existing=existing)

    async def ask(stage, payload, images=None):
        if stage == "F12":
            return {"terms": [{"action": "reuse_existing", "reuse_id": "known", "name": "IPC",
                               "definition": "未经授权的新定义", "block_ids": ["b1"], "status": "reused"}]}
        return {"questions": [
            {"part_id": "p1", "type": "choice", "question": "哪一项正确", "options": ["甲", "乙"],
             "answer": 0, "explanation": "选择甲", "block_ids": ["b1", "unmapped"],
             "source_ids": ["source-1", "source-2"]},
        ]}

    answer = asyncio.run(guidance(tmp_path, "sample", _structure(), {}, ask))
    assert answer["terms"][0]["definition"] == "旧术语定义"
    assert answer["terms"][0]["source_ids"] == ["source-1"]
    tests = asyncio.run(practice(tmp_path, "sample", _structure(), {}, ask))
    assert tests["questions"][0]["id"] != "old"
    assert tests["questions"][0]["block_ids"] == ["b1"]
    assert tests["questions"][0]["source_ids"] == ["source-1"]


def test_p2_excludes_inline_math_inline_images_and_invisible_resources():
    from manualreader.stages.finish.content import p2_inventory
    inline=r"看 \(x\) 的变化"
    pic="![图](figure.png)"
    raw=inline+"\n"+pic
    shape={"blocks":[{"block_id":"b","start":25,"markdown":raw}],
           "resources":[{"block_id":"b","resource_id":"inline","kind":"formula_inline"},
                        {"block_id":"b","resource_id":"standalone","kind":"image",
                         "start":25+len(inline)+1,"end":25+len(raw)},
                        {"block_id":"b","resource_id":"mixed","kind":"image","start":26,"end":35},
                        {"block_id":"b","resource_id":"off","kind":"formula_display","surface":"deferred"}]}
    result=p2_inventory(shape)
    assert result['standalone']['ordinal']==1
    assert result['inline']['exclude_reason']=='inline_math'
    assert result['mixed']['exclude_reason']=='inline_media'
    assert result['off']['exclude_reason']=='outside_visible_body'
