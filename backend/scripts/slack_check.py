"""Slack 연결 확인. .env의 토큰으로 Slack MCP 서버에 접속해 본다.

backend 폴더에서 (가상환경을 켠 상태):
    python scripts/slack_check.py              도구 목록만 본다 (메시지는 보내지 않는다)
    python scripts/slack_check.py U0123ABCD    그 사용자(또는 C로 시작하는 채널)에게 시험 메시지를 보낸다

처음 실행할 때 npx가 @modelcontextprotocol/server-slack 패키지를 내려받는다.
토큰 값은 화면에 출력하지 않는다.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 폴더를 import 경로에

import config  # noqa: E402
from integrations import slack  # noqa: E402
from integrations.mcp_client import list_tools  # noqa: E402


def main() -> None:
    print("SLACK_BOT_TOKEN:", "있음" if config.SLACK_BOT_TOKEN.startswith("xoxb-") else "없음 또는 xoxb-로 시작하지 않음")
    print("SLACK_TEAM_ID  :", config.SLACK_TEAM_ID or "없음")
    print("SLACK_CHANNEL_ID:", config.SLACK_CHANNEL_ID or "없음 (개인 메시지만 보냄)")

    print("\nSlack MCP 서버에 접속하는 중... (처음에는 패키지를 내려받느라 오래 걸릴 수 있습니다)")
    tools = list_tools(slack.PACKAGE, slack.env())
    print(f"도구 {len(tools)}개:", ", ".join(t["name"] for t in tools))
    send = next((t for t in tools if t["name"] == slack.SEND_TOOL), None)
    if send is None:
        print(f"\n[문제] 보내기 도구 {slack.SEND_TOOL}이 없습니다. integrations/slack.py의 SEND_TOOL을 바꿔 주세요.")
        return
    print(f"보내기 도구 {slack.SEND_TOOL}의 인자:", ", ".join(send["input"].get("properties", {})))

    if len(sys.argv) > 1:
        recipient = sys.argv[1]
        slack.send(recipient, "Orbit 연결 확인 메시지입니다. 이 메시지가 보이면 Slack 알림 설정이 끝난 것입니다.")
        print(f"\n{recipient}에게 시험 메시지를 보냈습니다. Slack에서 확인하세요.")


if __name__ == "__main__":
    main()
