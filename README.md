# KMU Mail Agent · 메일 비서

> 메일이 너무 많아 놓치는 교수님을 위해, **Outlook 메일을 Claude가 대신 읽고**
> 할 일은 **Outlook 캘린더 알림**으로, 불필요한 메일은 **"지워도 될까요?"**로 정리해 주는 서비스입니다.

![대시보드](web/guide/05-dashboard.jpg)

## 무엇을 해 주나요
- **매일 아침 '✅ 오늘 할 일'**: Outlook 캘린더에 우선순위순 체크리스트 일정이 울립니다. 휴대폰 Outlook 앱에서도 같은 알림이 옵니다.
- **마감·회의 자동 등록**: 메일 속 날짜를 찾아 일정으로 넣습니다. 보고서·심사처럼 준비가 필요한 마감은 **D-3, D-1 점검 알림**도 넣습니다.
- **불필요한 메일 정리**: 광고·홍보 메일을 모아 승인받은 뒤 지운 편지함으로 옮깁니다(복구 가능).
- **우선순위**: 긴급 → 오늘 → 이번 주 → 이번 달. 마감 날짜가 있으면 날짜가 기준이라 설명할 수 있습니다.
- **항목별 전용 도우미**: 정리 규칙 / 우선순위·중요 발신자 / 캘린더·알림을 말로 조정합니다. 각 도우미는 자기 항목만 바꿉니다.
- **연결 상태 확인**: 서비스 · Claude · Outlook 메일 · Outlook 캘린더가 연결됐는지 ✅/❌로 보여줍니다.
- **항상 켜짐**: Windows 로그인 시 자동 시작하고, 죽으면 다시 시작합니다.

## 설치
**교수님(비개발자):** Claude Desktop에 프롬프트 하나만 붙여 넣으면 됩니다 → [docs/SETUP_WITH_CLAUDE.md](docs/SETUP_WITH_CLAUDE.md)

**직접 설치할 때:**

```bash
python -m venv .venv
```

```bash
.venv\Scripts\pip install -r requirements.txt
```

`.env.example`을 `.env`로 복사한 뒤 `ROOT_API_KEY`와 `ANTHROPIC_WORKSPACE_ID`를 채웁니다. 그다음 서비스를 설치합니다.

```bash
powershell -ExecutionPolicy Bypass -File scripts\install_service.ps1
```

`http://localhost:3000/setup` 에서 설정 마법사 3단계를 진행하면 끝입니다. 화면별 사용법은 [`/guide`](web/guide.html)에 실제 화면 캡처와 함께 정리했습니다.

| 1. 계정 연결 | 2. 기본 설정 | 3. 저장하고 실행 |
|---|---|---|
| ![](web/guide/01-setup-account.jpg) | ![](web/guide/02-setup-basic.jpg) | ![](web/guide/04-setup-run.jpg) |

| 불필요한 메일 정리 | 전용 도우미 | 범위 밖 요청은 거절 |
|---|---|---|
| ![](web/guide/06-dashboard-cleanup.jpg) | ![](web/guide/08-cleanup-agent.jpg) | ![](web/guide/09-agent-guardrail.jpg) |

## 어떻게 만들었나
- **LangGraph 파이프라인**: 메일 병렬 분류(`Send`), 사람 승인(`interrupt`). 판단에만 Claude를 쓰고, 나머지(날짜 계산, 중복 방지, 캘린더 등록)는 결정적 코드로 처리합니다.
- **Claude API**: 구조화 출력(Pydantic)으로 분류합니다. 기본 모델은 Sonnet 5.5이고, 대시보드에서 Opus 5.5로 바꿀 수 있습니다.
- **Microsoft Graph**: 메일 읽기, 지운 편지함 이동, 캘린더 쓰기. MSAL device code 로그인으로 여러 계정을 지원합니다.
- **가드레일**: 에이전트별 도구 화이트리스트, 설정 범위 검사, 저장소에 없는 메일 id 차단, 템플릿 첫 인사, 삭제 전 사람 승인, 프롬프트 인젝션 분리.
- **채널**: 웹 콘솔(FastAPI), Claude Desktop(MCP 서버), Outlook 애드인(작업창).

자세히: [아키텍처](docs/ARCHITECTURE.md) · **[개발 기록: 무엇을, 왜, 어떻게](docs/DEVELOPMENT_LOG.md)**

## 비용
기본 모델 Sonnet 5.5 기준으로 메일 한 통 분류에 1~2센트 정도입니다. 하루 100통이면 월 $30~60 수준으로, 연구실 Claude 팀 플랜의 월 API 크레딧($380) 안에서 운영됩니다. 더 정확한 Opus 5.5는 대시보드에서 고를 수 있습니다(비용 약 2배).

## 개인정보
- 메일 내용은 분류를 위해 Claude API로 전송되고, 결과는 이 PC(`data/`)에만 저장됩니다.
- 토큰, 키, 메일 데이터는 저장소에 포함되지 않습니다(`.gitignore`).
- 메일을 영구 삭제하지 않습니다.

## 개발
```bash
.venv\Scripts\python -m pytest
```

오프라인 테스트 25개가 있습니다. 데모 모드(샘플 메일, 캘린더는 .ics 파일)는 `MAIL_SOURCE=sample CALENDAR_PROVIDER=ics`로 켭니다.
