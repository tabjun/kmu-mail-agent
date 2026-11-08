# Claude Desktop으로 설치하기 (교수님용)

명령어를 몰라도 됩니다. Claude Desktop 앱에 아래 프롬프트를 붙여 넣으면 Claude가 다음을 대신해 줍니다.
- 프로그램 받기, 설치
- 항상 켜지는 서비스로 등록
- 설정 화면 열기

## 준비물
- Windows PC: 항상 켜 두는 연구실 PC를 권합니다.
- Claude Desktop 앱
- **`kmu-mail-agent-key.zip`**: Claude API 키가 든 파일입니다. 담당 학생이 메신저·메일로 따로 보내 드립니다. 다운로드 폴더에 저장해 두세요.
- 학교 Office 365 계정(@ms.kmu.ac.kr). Gmail·네이버 메일도 추가로 연결할 수 있습니다(앱 비밀번호 필요).

## 1. 작업 폴더 고르기
Claude Desktop에서 **Code** 탭을 엽니다. Cowork를 써도 됩니다.
작업 폴더를 고르라고 하면 **`C:\Users\(사용자 이름)`** 폴더(내 사용자 폴더)를 고르세요.
이 안에 **`C:\Users\(사용자 이름)\kmu-mail-agent`** 폴더가 만들어집니다.
문서·바탕 화면은 OneDrive로 동기화될 수 있어서, 메일 데이터와 로그인 정보가 클라우드에 올라가지 않도록 이 위치를 씁니다.

## 2. 아래 프롬프트를 그대로 붙여 넣기

```text
GitHub 저장소 https://github.com/tabjun/kmu-mail-agent 의 '메일 비서'를 이 PC에 설치해 줘.

1. 내 사용자 폴더 안에 kmu-mail-agent 폴더(C:\Users\<내 이름>\kmu-mail-agent)로 저장소를 받아 줘. git이 없으면
   https://github.com/tabjun/kmu-mail-agent/archive/refs/heads/main.zip 을 내려받아 압축을 풀고 폴더 이름을 kmu-mail-agent로 해 줘.
2. API 키는 따로 받은 kmu-mail-agent-key.zip 안의 .env 파일에 있어. 다운로드 폴더에 있으니 kmu-mail-agent 폴더 안에 풀어서
   .env가 설치하기.bat 옆에 오게 해 줘. 직접 못 하면 나에게 어디에 풀면 되는지 알려 줘.
   .env 안의 값은 화면에 보여주지 말고, ROOT_API_KEY와 ANTHROPIC_WORKSPACE_ID 값이 들어 있는지만 확인해 줘.
3. 받은 폴더의 CLAUDE.md를 먼저 읽고, 거기 적힌 '처음 설치' 순서를 따라 줘.
   (.env가 이미 있으니 키 입력 없이 scripts\setup_windows.ps1 -NoPrompt 로 1~6단계를 한 번에)
4. 설치가 끝나면 항상 켜지는 서비스로 등록하고, 브라우저에서 http://localhost:3000/setup 을 열어 줘.
5. 설정 마법사(계정 연결 → 기본 설정 → 저장하고 실행)를 내가 진행하는 동안 단계마다 무엇을 누르면 되는지 짧게 알려 줘.
6. 끝나면 대시보드의 '연결 상태'가 모두 ✅인지 확인하고, ❌가 있으면 원인을 찾아 고쳐 줘.
   문제가 생기면 data\logs\activity.jsonl 과 data\logs\server.log 를 읽어서 판단해 줘.
```

## 3. 진행 중에 교수님이 직접 하실 일
- Claude가 키 파일 위치를 물으면 `kmu-mail-agent-key.zip`을 `C:\Users\(사용자 이름)\kmu-mail-agent` 폴더에 풀어 주세요
  (ZIP 오른쪽 버튼 → 압축 풀기 → 위 폴더 선택). 폴더 안에 `.env` 파일이 생기면 됩니다.
- 설정 마법사 1단계에서 학교 메일 주소를 입력하고 **연결하기**를 누릅니다. Microsoft 로그인 창이 열리면 계정을 고르고 **수락**을 누릅니다.
  - "관리자 승인 필요"가 뜨면 학교 승인이 아직 안 된 것입니다. 정보통신처에 [요청서](admin-consent-request.md)를 보내세요.
- Gmail·네이버도 쓰신다면 1단계의 **Gmail · 네이버 · 다음** 탭에서 앱 비밀번호로 추가합니다.
- 2단계에서 알림 시각 등을 고르고, 3단계에서 **지금 실행하기**를 누릅니다.

## 설치가 끝난 뒤
- 바탕화면의 **메일 비서** 아이콘을 열면 대시보드가 나옵니다.
- 매일 알림 시각에 Outlook 캘린더의 **'✅ 오늘 할 일'** 일정이 울립니다. 휴대폰 Outlook 앱(같은 계정)에도 같은 알림이 옵니다.
- 불필요한 메일은 대시보드의 **불필요한 메일 정리** 카드에서 승인하거나 거절합니다. 승인하면 휴지통으로 옮겨지고, 복구할 수 있습니다.
- 기준을 바꾸고 싶으면 🧹 / 📌 / 📅 버튼(전용 도우미)을 누르고 말로 설명하면 됩니다.

## Claude 없이 설치하려면
README의 **설치 방법 1 · 클릭만으로**를 따라 하세요. ZIP을 받아 `설치하기.bat`을 더블클릭하면 됩니다.
