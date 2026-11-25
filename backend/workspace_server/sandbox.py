"""생성된 코드를 격리해서 실행한다.

npm install, make test는 명령 이름만 정해져 있고, 실제로 무엇을 할지는 
에이전트가 쓴 파일(package.json의 postinstall, Makefile의 test:)이 정한다.
> 서버에서 바로 돌리지 않고, 명령마다 새 컨테이너를 띄워 그 안에서 실행한 뒤 지운다.

컨테이너 안에서 보이는 것: 이 프로젝트의 작업 공간(/workspace)뿐
보이지 않는 것: .env(토큰), orbit.db, 다른 프로젝트, docker 소켓
"""