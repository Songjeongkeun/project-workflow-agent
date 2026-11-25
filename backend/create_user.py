"""계정 만들기. 회원가입 화면과 API는 없다. 운영 담당자가 서버에서 만든다 (04 2장).

    python create_user.py kim --name 김기획 [--slack U012AB3CD]

비밀번호는 화면에 보이지 않게 입력받는다.
"""

import argparse
import getpass
import sys

from db import get_connection, init_db
from services import sessions


def main() -> int:
    parser = argparse.ArgumentParser(description="Orbit 계정 만들기")
    parser.add_argument("login_id")
    parser.add_argument("--name", required=True, help="화면에 보일 이름")
    parser.add_argument("--slack", default=None, help="Slack 사용자 ID")
    args = parser.parse_args()

    password = getpass.getpass("비밀번호: ")
    if not password or password != getpass.getpass("비밀번호 확인: "):
        print("비밀번호가 비었거나 서로 다릅니다.", file=sys.stderr)
        return 1

    init_db()
    with get_connection() as conn:
        if sessions.find_user_by_login_id(conn, args.login_id):
            print(f"이미 있는 아이디입니다: {args.login_id}", file=sys.stderr)
            return 1
        sessions.create_user(conn, args.login_id, password, args.name, args.slack)
    print(f"계정을 만들었습니다: {args.login_id} ({args.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
