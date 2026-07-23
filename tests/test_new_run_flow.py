"""Inline /new flow dropdown helpers."""

from pathlib import Path

from room_tui.new_run_flow import (
    format_new_confirm_dropdown,
    format_new_inputs_dropdown,
    format_new_template_dropdown,
    inputs_path_at,
    inputs_row_count,
    inputs_row_is_next,
    safe_filename_stem,
)


def test_safe_filename_stem() -> None:
    assert safe_filename_stem("软件需求规格") == "软件需求规格"
    assert " " not in safe_filename_stem("a b/c")


def test_template_dropdown_empty() -> None:
    text, rows = format_new_template_dropdown([], selected=0)
    assert "①/3" in text
    assert "无已注册" in text
    assert rows >= 2


def test_template_dropdown_select() -> None:
    tpls = [
        {"id": "t1", "name": "A", "section_count": 2},
        {"id": "t2", "name": "B", "section_count": 9},
    ]
    text, _ = format_new_template_dropdown(tpls, selected=1)
    assert "B" in text and "①/3" in text


def test_inputs_rows_and_toggle_map() -> None:
    a = Path("/tmp/a.md")
    b = Path("/tmp/b.md")
    cands = [a, b]
    assert inputs_row_count([], cands) == 3
    assert inputs_path_at(0, [], cands) == a
    assert inputs_path_at(1, [], cands) == b
    assert inputs_row_is_next(2, [], cands)
    assert inputs_path_at(0, [a], cands) == a
    assert inputs_row_is_next(2, [a], cands)  # ✓a, ○b, next


def test_inputs_dropdown_marks() -> None:
    a = Path("/ws/x.doc")
    text, _ = format_new_inputs_dropdown(
        selected_paths=[a],
        candidates=[a, Path("/ws/y.doc")],
        cursor=0,
        ws=Path("/ws"),
    )
    assert "②/3" in text
    assert "✓" in text
    assert "下一步" in text


def test_confirm_dropdown() -> None:
    text, rows = format_new_confirm_dropdown(
        template_name="规格",
        template_id="tpl-1",
        inputs=[Path("/ws/a.doc")],
        output="out.md",
        model="deepseek",
    )
    assert "③/3" in text
    assert "开始生成" in text
    assert rows >= 5
