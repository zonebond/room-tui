"""humanize_engine_error keeps root causes (not generic 引擎调用失败)."""

from __future__ import annotations

import sys

from room_tui.engine.errors import humanize_engine_error, register_error_hints


def test_extracts_runtimeerror_from_traceback() -> None:
    dump = """engine failed (1): Traceback (most recent call last):
  File "x.py", line 1, in <module>
    raise RuntimeError("无法读取 .doc 文件。macOS: 系统自带 textutil 应可用。")
RuntimeError: 无法读取 .doc 文件。macOS: 系统自带 textutil 应可用。
"""
    out = humanize_engine_error(dump, sample_suffix=".doc")
    assert "引擎调用失败" not in out or "doc" in out.lower()
    assert ".doc" in out or "docx" in out.lower()


def test_windows_doc_hint(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    out = humanize_engine_error(
        "RuntimeError: 无法读取旧版 .doc。请安装 LibreOffice（推荐，免费）或 Microsoft Word",
        sample_suffix=".doc",
    )
    assert "LibreOffice" in out or "docx" in out.lower() or "Word" in out
    assert "引擎调用失败" not in out


def test_windows_doc_keeps_word_com_detail(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    out = humanize_engine_error(
        "RuntimeError: 无法读取旧版 .doc。已尝试 LibreOffice 与 Word/WPS 自动化。"
        " Word: COM create failed · 需桌面版 Word 或 WPS"
        " LibreOffice: https://www.libreoffice.org/",
        sample_suffix=".doc",
    )
    assert "无法读取旧版 .doc" in out
    assert "COM" in out or "桌面版" in out
    hints = register_error_hints(out, sample_path="x.doc")
    assert any("虚拟机" in h or "docx" in h.lower() for h in hints)


def test_template_id_exists_json() -> None:
    dump = (
        'engine failed (1): {"error":"template_id_exists",'
        '"message":"模板 id 已存在","existing_id":"tpl-abc"}'
    )
    out = humanize_engine_error(dump)
    assert "已存在" in out
    assert "tpl-abc" in out or "delete" in out.lower() or "删除" in out


def test_not_json_keeps_slice() -> None:
    out = humanize_engine_error("engine stdout is not JSON: (暂无已注册模板)")
    assert "暂无" in out or "JSON" in out or "异常" in out


def test_register_hints_for_doc() -> None:
    hints = register_error_hints("无法读取 .doc", sample_path="软件需求规格说明.doc")
    assert hints
    assert any("docx" in h.lower() or ".md" in h for h in hints)


def test_plain_message_not_swallowed() -> None:
    out = humanize_engine_error("模型未配置 — Ctrl+M 连接")
    assert "模型未配置" in out


def test_no_such_option_out_not_doc_noise() -> None:
    dump = (
        "Error: No such option '--out'. "
        "Usage: paper-derived.exe template register [OPTIONS] SAMPLE"
    )
    out = humanize_engine_error(dump, sample_suffix=".doc")
    assert "引擎调用失败" not in out
    assert "out" in out.lower() or "prompt-file" in out.lower() or "参数" in out
    hints = register_error_hints(out, sample_path="x.doc")
    assert any("套件" in h or "paper-derived" in h for h in hints)
    assert not any("LibreOffice" in h for h in hints)
