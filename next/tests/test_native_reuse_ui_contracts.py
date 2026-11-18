from manualreader.artifacts import write_json
from manualreader.stages.finish.preview import native_visual
from reader_term_quality import cover_planned_terms, mentioned_terms
from reader_translation import save_visuals
from reader_workspace import Workspace


def test_visual_reuse_accepts_named_snapshot_attachment_and_skips_ambiguous_assets(tmp_path):
    def image(identity, file_id="project-file"):
        return {"id": identity, "kind": "image", "file_id": file_id,
                "src": f"/asset/{identity}.png", "refs": [], "zh": "已有图注"}

    images = {identity: image(identity) for identity in ("old", "same-a", "same-b", "wrong-file")}
    source = {
        "assets": {"asset-a": {"sha256": "digest-a", "file_id": "project-file", "kind": "vector"},
                   "asset-shared": {"sha256": "digest-shared", "file_id": "project-file", "kind": "image"},
                   "asset-other": {"sha256": "digest-other", "file_id": "different-file", "kind": "image"}},
        "sources": [{"source_id": sid, "file_id": image_row["file_id"], "kind": "image", "asset_refs": [asset_id]}
                    for (sid, image_row), asset_id in zip(images.items(),
                        ("asset-a", "asset-shared", "asset-shared", "asset-other"))],
    }
    translations = [{"asset_sha256": digest, "original": "Voltage", "translated": "电压"}
                    for digest in ("digest-a", "digest-shared", "digest-other")]
    translations.append({"source_id": "same-a", "asset_sha256": "digest-a",
                         "original": "Current", "translated": "wrong-attachment"})
    write_json(tmp_path / "translation-visuals.json", {"translations": translations})
    result = native_visual(tmp_path, images, [], source)
    assert [row["image_id"] for row in result] == ["old"]
    assert result[0]["evidence"]["matched_asset_sha256s"] == ["digest-a"]
    material = {"passages": list(images.values())}
    receipt = save_visuals(tmp_path, material, result)
    Workspace(tmp_path).apply_visual_translations(material)
    assert receipt["saved"] == 1 and receipt["incomplete"] == 1
    assert images["old"]["zh"] == "已有图注\n\nVoltage：电压"
    assert images["same-a"]["zh"] == "已有图注"


def test_known_unit_symbols_match_context_and_preserve_case():
    terms = [{"id": "watts", "name": "W 瓦特（Watt）", "aliases": ["W"]},
             {"id": "amp", "name": "安培（A）"},
             {"id": "volts", "name": "伏特（V）"},
             {"id": "seconds", "name": "秒（s）"},
             {"id": "ohms", "name": "欧姆（Ω）"},
             {"id": "variable", "name": "算法变量", "aliases": ["X"]}]
    assert {row["id"] for row in mentioned_terms("W 是瓦，电压 4 V、电流 3 A、持续 2 s、阻值 2 Ω", terms)} == {
        "watts", "amp", "volts", "seconds", "ohms"}
    assert not mentioned_terms("A variable, w 是变量，选择 V，另一个选项 X", terms)
    result = {"answers": [{"question_id": "q", "parts": [{"markdown": "W 是瓦，是功率单位"}]}], "terms": []}
    assert cover_planned_terms(result, {"mode": "answer", "items": [{"task_id": "q", "essential_terms": ["W"]}]}, terms) == []
    assert result["terms"][0]["term_id"] == "watts"
