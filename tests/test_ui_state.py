from rich.cells import cell_len

from room_tui.ui_state import (
    PipelineState,
    map_event_to_pipeline,
    mode_label,
    normalize_mode_display,
    render_chapter_lines,
    section_glyph,
)


def test_pipeline_render_order() -> None:
    p = PipelineState()
    p.mark_done_up_to("generate")
    lines = p.render_lines()
    assert any("生成章节" in x for x in lines)
    # Lines may include Rich markup around glyphs.
    assert "✓" in lines[0] or "●" in lines[0]


def test_map_events() -> None:
    p = PipelineState()
    map_event_to_pipeline({"type": "session_init"}, p)
    assert "template" in p.done_keys
    map_event_to_pipeline({"type": "run_complete"}, p)
    assert p.current_key == "complete"


def test_section_glyph() -> None:
    assert section_glyph("done") == "✓"
    assert section_glyph("generating") == "●"
    assert section_glyph("ready") == "○"


def test_mode_label() -> None:
    assert "生成中" in mode_label(True, "generating", "3/10")
    assert mode_label(False, "idle", "") == "空闲"
    assert mode_label(False, "init", "") == "初始化"
    assert mode_label(False, "paused", "") == "已暂停"
    assert normalize_mode_display("init") == "初始化"
    assert normalize_mode_display("已完成  3/10") == "已完成  3/10"


def test_chapter_lines_cjk_truncate_and_focus() -> None:
    secs = [
        {"section_id": "1", "title": "范围", "status": "pending", "level": 1},
        {
            "section_id": "3.1.1",
            "title": "(CSCI能力) 总线维护 SRFN1 很长很长的标题",
            "status": "pending",
            "level": 3,
        },
        {
            "section_id": "3.1",
            "title": "CSCI能力需求",
            "status": "active",
            "level": 2,
        },
    ]
    lines = render_chapter_lines(secs, focus="3.1", width_cells=28)
    assert "范围" in lines[0]
    assert "●" not in lines[0]  # not focused / not active
    assert "●" in lines[2] and "CSCI能力需求" in lines[2]
    # plain width must not exceed budget (no wrap)
    import re

    for ln in lines:
        plain = re.sub(r"\[/?[^\]]+\]", "", ln)
        assert cell_len(plain) <= 28
