#!/usr/bin/env python3
"""Cursor afterAgentResponse hook：一轮循环还没结束就盯上下文，过档立刻弹 macOS 通知。

为什么不塞进 ctx_handoff_hook.py：stop 事件只在**整轮** agent 循环结束时触发一次，
而一轮能连续跑一百多条消息。实测会话 58cf82d0——用户 10:03 提问时上下文 36,769
（全是固定开销），之后 147 条消息一口气跑到 158,089，10:19 才第一次触发 stop。
10 万那道阈值从头到尾没有被检查的机会，所以降阈值治不了超调，只能加密检查点。

afterAgentResponse 每写完一条 assistant 消息就触发，正好补这个空档。
Cursor 没有任何 hook 能中止正在跑的循环，所以这里只弹通知，按不按 ESC 由主理人定。
不注入任何内容到对话里，零 token 成本。

和另外两个脚本的分工：
  本脚本            循环进行中，10 万弹通知（人看）
  ctx_handoff_hook  循环结束时，10 万发口令让会话自己写交接摘要（AI 看）
  ctx_watch         launchd 兜底，22 万还没人管就推 Discord
三边共用 ctx_alerts 表，档位名前缀各不相同（live- / auto- / 无），不会互相顶掉。
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ctx_handoff_hook import CTX_WINDOW, claim, ctx_tokens  # noqa: E402

# 只设一档。循环中途通知的全部价值就是「趁还没烧完赶紧知道」，
# 22 万那种兜底档轮不到这儿管（ctx_watch 在盯）。
LIVE_LIMIT = 100_000
TIER = f"live-{LIVE_LIMIT // 1000}k"


def notify(tokens: int, title: str) -> None:
    """弹一条 macOS 通知。标题可能带引号，交给 osascript 前先转义。"""
    safe = title.replace("\\", "").replace('"', "'")[:40]
    body = (
        f"{safe} 已 {tokens/10000:.1f} 万 token（{tokens/CTX_WINDOW*100:.0f}%），"
        "这一轮还在跑。要省钱现在按 ESC 打断，带摘要换窗口。"
    )
    subprocess.run(
        [
            "osascript",
            "-e",
            f'display notification "{body}" with title "上下文过档" sound name "Ping"',
        ],
        capture_output=True,
        timeout=4,
    )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    cid = payload.get("conversation_id")
    if not cid:
        return

    hit = ctx_tokens(cid)
    if not hit:
        return
    tokens, title = hit

    # claim 兼当节流阀：一轮一百多条消息都会走到这儿，同一会话只让第一条过
    if tokens < LIVE_LIMIT or not claim(cid, TIER):
        return

    notify(tokens, title)


if __name__ == "__main__":
    main()
