"""Chat history persistence + task summary helpers."""

from __future__ import annotations

import json
from pathlib import Path

from room_tui.workspace import (
    RunManifest,
    Workspace,
    _pi_user_text,
)


def test_append_and_read_chat_history(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    ws.ensure()
    ws.append_chat_message("user", "你好")
    ws.append_chat_message("assistant", "收到")
    ws.append_chat_message("system", "提示")
    rows = ws.read_chat_history()
    assert len(rows) == 3
    assert rows[0]["role"] == "user"
    assert rows[0]["text"] == "你好"
    assert rows[1]["role"] == "assistant"
    assert rows[2]["role"] == "system"


def test_clear_chat_history(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    ws.append_chat_message("user", "x")
    assert ws.read_chat_history()
    ws.clear_chat_history()
    assert ws.read_chat_history() == []


def test_pi_user_text_unwraps_file() -> None:
    raw = (
        '<file name="/tmp/chat-1.user.txt">\n'
        "对比 output 与模板\n"
        "</file>\n"
    )
    assert _pi_user_text([{"type": "text", "text": raw}]) == "对比 output 与模板"
    assert _pi_user_text("plain hi") == "plain hi"


def test_seed_from_pi_agent(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    ws.ensure()
    agent = ws.tui / "pi-agent"
    agent.mkdir(parents=True)
    session = agent / "2026-01-01T00-00-00_room-agent.jsonl"
    rows = [
        {"type": "session", "id": "room-agent"},
        {
            "type": "message",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": '<file name="x">\nhello world\n</file>',
                    }
                ],
            },
        },
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "short"},
                    {"type": "toolCall", "name": "read", "arguments": {}},
                ],
            },
        },
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "这是最终长回答，包含分析内容。"}],
            },
        },
    ]
    session.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    n = ws.seed_chat_history_from_pi_agent()
    assert n == 2
    hist = ws.read_chat_history()
    assert hist[0]["role"] == "user"
    assert hist[0]["text"] == "hello world"
    assert hist[1]["role"] == "assistant"
    assert "最终长回答" in hist[1]["text"]
    # second call is a no-op when history exists
    assert ws.seed_chat_history_from_pi_agent() == 0


def test_task_block_lines_compact() -> None:
    from room_tui.screens.shell import ShellScreen

    m = RunManifest(
        client_version="0.1.0",
        template_id="tpl-abc",
        session_id="sess_1",
        status="cancelled",
        phase="generating",
        progress="42/46",
        output="/proj/output.md",
        inputs=["/proj/软件任务书.doc"],
    )
    line1, line2 = ShellScreen._task_block_lines(m)
    assert line1.count("\n") == 0
    assert line2.count("\n") == 0
    assert "软件任务书" in line1
    assert "已取消" in line1
    assert "42/46" in line1
    assert "/continue" in line2
    assert "output.md" in line2
    assert "sess_1" not in line1 and "sess_1" not in line2
    assert ShellScreen._is_incomplete_task(m)
    m2 = RunManifest(
        client_version="0.1.0",
        template_id="tpl-abc",
        session_id="sess_1",
        status="complete",
        phase="complete",
        progress="46/46",
    )
    assert not ShellScreen._is_incomplete_task(m2)


def test_chrome_history_noise_filter() -> None:
    """Only *auto* cold-start chrome is noise — never user-triggered results."""
    from room_tui.screens.shell import ShellScreen

    # Auto launch chrome (must stay filtered)
    assert ShellScreen._is_chrome_history_noise("未完成  软件任务书  ·  已取消 42/46")
    assert ShellScreen._is_chrome_history_noise("── 以上为历史消息 ──")
    assert ShellScreen._is_chrome_history_noise(
        "你好，这里是 Room 工程间。\n· /new 新建"
    )
    assert ShellScreen._is_chrome_history_noise(
        "⚠ 未配置模型\n按 Ctrl+M 打开选择器"
    )

    # User-triggered slash results MUST survive quit/re-entry
    assert not ShellScreen._is_chrome_history_noise(
        "✓ 模板已注册（完整）  测试模板-1  [tpl-x]  ·  5 节  ·  100ms\n"
        "下一步: /new  → ①选模板 ②勾资料 ③Enter 开始"
    )
    assert not ShellScreen._is_chrome_history_noise(
        "已注册模板（1）:\n  测试模板-1  [tpl-x]  ·  5 节\n"
        "注册: /template register <样例> [名]  ·  快速: --fast  ·  生成: /new"
    )
    assert not ShellScreen._is_chrome_history_noise(
        "新建文档  ·  ①选模板  ②勾资料  ③确认开始\n已加载 1 个模板  ·  Esc 取消"
    )
    assert not ShellScreen._is_chrome_history_noise(
        "项目    /tmp/demo\n运行    否\n阶段    —\n进度    —\n本项目尚无任务清单"
    )
    assert not ShellScreen._is_chrome_history_noise("没有可继续的任务")
    assert not ShellScreen._is_chrome_history_noise("完成  分析样例")
    assert not ShellScreen._is_chrome_history_noise("失败  注册模板  ·  timeout")
    assert not ShellScreen._is_chrome_history_noise("对比 output 与模板")
    assert not ShellScreen._is_chrome_history_noise("生成完成。输出：output.md")
    # /status-style lines are user-triggered, not auto chrome
    assert not ShellScreen._is_chrome_history_noise("项目  demo")
    assert not ShellScreen._is_chrome_history_noise(
        "当前任务  软件任务书\n状态    已取消"
    )


def test_slash_should_pin_pipeline_commands() -> None:
    """ /continue and template register pin; short slash does not. """
    from room_tui.screens.shell import ShellScreen

    assert ShellScreen._slash_should_pin("continue", "/continue")
    assert ShellScreen._slash_should_pin("c", "/c")
    assert ShellScreen._slash_should_pin(
        "template",
        "/template register sample.md 名",
    )
    assert ShellScreen._slash_should_pin(
        "template",
        "/template register --fast sample.md",
    )
    assert not ShellScreen._slash_should_pin("status", "/status")
    assert not ShellScreen._slash_should_pin("template", "/template list")
    assert not ShellScreen._slash_should_pin("new", "/new")
    assert not ShellScreen._slash_should_pin("help", "/help")


def test_slash_results_roundtrip_not_filtered(tmp_path: Path) -> None:
    """Regression: consecutive slash turns keep their system responses on disk.

    Matches the reported bug: /template register|list, /new, /status, /continue
    each had a live response that vanished after quit/re-entry.
    """
    from room_tui.screens.shell import ShellScreen

    samples: list[tuple[str, str]] = [
        ("user", "/template register sample.md 测试模板-1"),
        (
            "system",
            "✓ 模板已注册（完整）  测试模板-1  [tpl-x]  ·  5 节  ·  100ms\n"
            "下一步: /new  → ①选模板 ②勾资料 ③Enter 开始",
        ),
        ("user", "/template list"),
        (
            "system",
            "已注册模板（1）:\n  测试模板-1  [tpl-x]  ·  5 节\n"
            "注册: /template register <样例> [名]  ·  快速: --fast  ·  生成: /new",
        ),
        ("user", "/new"),
        (
            "system",
            "新建文档  ·  ①选模板  ②勾资料  ③确认开始\n"
            "已加载 1 个模板  ·  Esc 取消",
        ),
        ("user", "/status"),
        (
            "system",
            "项目    /tmp/demo\n运行    否\n阶段    —\n进度    —\n本项目尚无任务清单",
        ),
        ("user", "/continue"),
        (
            "system",
            "没有可继续的任务\n请先 /new 开始文档生成  ·  ①选模板 ②勾资料 ③Enter",
        ),
    ]
    ws = Workspace(tmp_path)
    for role, text in samples:
        ws.append_chat_message(role, text)
    rows = ws.read_chat_history()
    assert len(rows) == len(samples)
    # Soft filter must not drop any of these on restore
    visible = [
        r
        for r in rows
        if not (
            ShellScreen._is_chrome_history_noise(r["text"])
            and not r.get("blocks")
        )
    ]
    assert len(visible) == len(samples)
    # User and following system stay paired
    for i in range(0, len(samples), 2):
        assert visible[i]["role"] == "user"
        assert visible[i + 1]["role"] == "system"
        assert visible[i + 1]["text"].strip()
