"""개발용 팀원 계정 다섯 개를 만든다. 실제 운영 서버에서는 쓰지 않는다.

    python seed_demo.py

계정: jeonggeun, juyeong, hyeongjun, nayeong, suyeon. 비밀번호는 모두 DEMO_PASSWORD.
jeonggeun이 만든 예시 프로젝트 하나에 다섯 사람을 넣고, 누구나 모든 승인을 시험할 수 있도록 네 역할을 모두 준다.
역할은 멤버 화면에서 프로젝트를 만든 사람(jeonggeun)이 바꿀 수 있다.
"""

import config
from db import get_connection, init_db
from services import members, sessions

DEMO_PASSWORD = "1111"
DEMO_USERS = [
    ("jeongkeun", "송정근"),
    ("juyeong", "조주영"),
    ("hyeongjun", "심형준"),
    ("nayeong", "양나영"),
    ("suyeon", "양수연"),
]


def main() -> None:
    init_db()
    with get_connection() as conn:
        users = {}
        for login_id, name in DEMO_USERS:
            user = sessions.find_user_by_login_id(conn, login_id)
            if user is None:
                sessions.create_user(conn, login_id, DEMO_PASSWORD, name)
                user = sessions.find_user_by_login_id(conn, login_id)
            users[login_id] = user

        owner = users[DEMO_USERS[0][0]]
        if not members.my_projects(conn, owner["id"]):
            project_id = members.create_project(conn, "회의실 예약", owner)
            project = members.get_project(conn, project_id)
            members.update_member(conn, project, owner, owner["id"], members.ROLES, None)
            for login_id, _ in DEMO_USERS[1:]:
                members.invite(conn, project, owner, login_id, members.ROLES, None)

    print(f"팀원 계정: {', '.join(login_id for login_id, _ in DEMO_USERS)}")
    print(f"DB: {config.DB_PATH}")


if __name__ == "__main__":
    main()
