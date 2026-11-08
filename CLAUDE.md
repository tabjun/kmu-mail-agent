# CLAUDE.md — KMU Mail Agent

이 저장소를 Claude Code / Claude Desktop(Code·Cowork)에서 열었을 때 따를 안내입니다.
사용자는 대개 **개발자가 아닌 교수님**입니다. 명령어를 외우게 하지 말고, 대신 실행하면서 한 단계씩 확인받으세요.

## 이 프로젝트가 하는 일
Office 365(Outlook) 메일을 주기적으로 읽어 Claude로 분류하고, 할 일을 Outlook 캘린더에 알림과 함께 넣습니다
(매일 '✅ 오늘 할 일' 체크리스트 일정 포함). 불필요한 메일은 사용자가 승인한 것만 지운 편지함으로 옮깁니다.
웹 화면: `http://localhost:3000/` (대시보드), `/setup` (설정 마법사), `/guide` (사용법).

## 처음 설치 (Windows) — 사용자가 "설치해줘"라고 하면 이 순서로
가장 간단한 방법은 `scripts\setup_windows.ps1`(= `설치하기.bat`) 하나로 1~6단계를 모두 하는 것입니다.
키 입력 단계는 사용자가 직접 붙여 넣게 두거나, 사용자가 대화로 준 값을 `.env`에만 쓰고 `-NoPrompt`로 실행하세요.
아래는 그 스크립트가 하는 일을 단계별로 풀어 쓴 것입니다.
1. Python 3.10+ 확인: `python --version`
2. 가상환경과 패키지:
   - `python -m venv .venv`
   - `.venv\Scripts\pip install -r requirements.txt`
3. `.env` 만들기: `.env.example`을 복사합니다. **`ROOT_API_KEY`와 `ANTHROPIC_WORKSPACE_ID`는 사용자에게 받아서** 채웁니다.
   - 키 값은 대화에 다시 출력하지 마세요.
   - `MS_CLIENT_ID`는 예시 값을 그대로 둡니다.
4. 동작 확인: `.venv\Scripts\python -m mail_agent check-key` → "✅ API 키 유효"
5. 항상 켜지는 서비스로 등록: `powershell -ExecutionPolicy Bypass -File scripts\install_service.ps1`
   - Windows 로그인 시 자동 시작, 죽으면 재시작, 바탕화면 '메일 비서' 바로가기 생성
   - 지금만 백그라운드로 켜려면 `scripts\start_background.ps1`
6. 브라우저에서 `http://localhost:3000/setup`을 열게 하고, 설정 마법사 3단계를 안내합니다.
   1. 학교 메일 주소 입력 → 연결하기 → 표시된 코드로 Microsoft 로그인 → 권한 **수락**. 이 단계는 사용자가 직접 해야 합니다.
   2. 기본 설정(알림 시각, 불필요한 메일 처리 방식, 점검 알림)
   3. 저장하고 실행
7. 대시보드의 **연결 상태**가 모두 ✅인지 확인합니다(서비스 / Claude / Outlook 메일 / Outlook 캘린더).

## 문제 해결
- 연결 상태 ❌ "다시 로그인이 필요합니다" → 대시보드 '+ 계정 추가'에서 같은 계정을 다시 연결합니다(권한이 추가된 경우).
- "관리자 승인 필요" 화면 → 학교 테넌트 정책입니다. `docs/admin-consent-request.md`를 참고해 정보통신처에 요청합니다.
- 서버 로그: `data/logs/server.log`, `data/logs/mail_agent.log`
- **활동 로그**: `data/logs/activity.jsonl`에 화면 클릭, API 호출(상태·시간), 계정 연결, 설정 저장, 실행 결과, 오류가 한 줄씩 남습니다(비밀번호·키·메일 본문은 기록하지 않음). 문제를 진단할 때 먼저 이 파일의 마지막 부분을 읽으세요.
- Gmail·네이버는 설정 마법사 1단계의 'Gmail · 네이버 · 다음' 탭에서 앱 비밀번호로 연결합니다(비밀번호는 Windows 자격 증명 관리자에 저장).
- 테스트: `.venv\Scripts\python -m pytest` (오프라인, Claude·Microsoft 호출 없음)

## 지켜야 할 것
- `.env`, `data/`, `outputs/`는 절대 커밋하지 않습니다(메일 내용·토큰·키).
- 메일을 지우는 동작은 항상 사용자 승인(정리 제안 → 승인) 뒤에만, 그리고 지운 편지함 이동으로만 합니다. 영구 삭제 코드를 넣지 마세요.
- 설정 도우미 에이전트(cleanup / priority / calendar)는 각자 범위의 설정만 바꿉니다(`mail_agent/agents.py`의 가드레일). 범위를 넓히지 마세요.
- 구조와 의도: `docs/ARCHITECTURE.md`, 개발 과정과 결정 이유: `docs/DEVELOPMENT_LOG.md`
