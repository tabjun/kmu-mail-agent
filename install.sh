#!/usr/bin/env bash
# 메일 비서 설치 (macOS / Linux)
#   터미널에서:  bash install.sh
# 하는 일: 파이썬 확인 → 패키지 설치 → API 키 입력(.env) → 연결 확인
#          → 항상 켜지는 서비스 등록(macOS: LaunchAgent, Linux: systemd --user) → 브라우저 열기
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
PORT="${PORT:-3000}"

say() { printf '\n\033[36m[%s] %s\033[0m\n' "$1" "$2"; }
ok() { printf '  \033[32m✓ %s\033[0m\n' "$1"; }
warn() { printf '  \033[33m! %s\033[0m\n' "$1"; }

say 1/6 "파이썬 확인"
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  warn "파이썬 3.10 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행하세요."
  exit 1
fi
ok "$($PY --version)"

say 2/6 "필요한 프로그램 설치 (처음 한 번, 2~5분)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip --quiet --disable-pip-version-check
.venv/bin/python -m pip install -r requirements.txt --quiet --disable-pip-version-check
ok "설치 완료"

say 3/6 "Claude API 키 설정 (.env 파일)"
[ -f .env ] || { cp .env.example .env; ok ".env 파일을 만들었습니다: $DIR/.env"; }
getv() { grep -E "^\s*$1\s*=" .env | head -1 | cut -d= -f2- | sed 's/#.*//' | tr -d '[:space:]' || true; }
setv() { .venv/bin/python - "$1" "$2" <<'PY'
import sys, pathlib
name, value = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env"); lines = p.read_text(encoding="utf-8").splitlines()
out, found = [], False
for l in lines:
    if l.strip().startswith(name + "="):
        out.append(f"{name}={value}"); found = True
    else:
        out.append(l)
if not found: out.append(f"{name}={value}")
p.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}
if [ -z "$(getv ROOT_API_KEY)" ]; then
  printf '  연구실에서 받은 Claude API 키를 붙여 넣고 Enter (화면에 표시되지 않음): '
  read -rs KEY; echo
  [ -n "$KEY" ] && setv ROOT_API_KEY "$KEY" && ok "키를 .env에 저장했습니다."
fi
if [ -z "$(getv ANTHROPIC_WORKSPACE_ID)" ]; then
  printf '  워크스페이스 ID (wrkspc_로 시작, 없으면 Enter): '
  read -r WS
  [ -n "$WS" ] && setv ANTHROPIC_WORKSPACE_ID "$WS" && ok "워크스페이스 ID를 저장했습니다."
fi

say 4/6 "Claude 연결 확인"
.venv/bin/python -m mail_agent check-key || warn "연결을 확인하지 못했습니다. .env를 확인한 뒤 다시 실행하세요. (설치는 계속합니다)"

say 5/6 "항상 켜지는 서비스로 등록"
CMD=("$DIR/.venv/bin/python" -m mail_agent serve --http --port "$PORT")
case "$(uname -s)" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/kr.kmu.mailagent.plist"
    mkdir -p "$HOME/Library/LaunchAgents" "$DIR/data/logs"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>kr.kmu.mailagent</string>
  <key>ProgramArguments</key><array>
    <string>${CMD[0]}</string><string>-m</string><string>mail_agent</string><string>serve</string>
    <string>--http</string><string>--port</string><string>$PORT</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/data/logs/server.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/logs/server.log</string>
</dict></plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load -w "$PLIST"
    ok "로그인할 때마다 자동으로 켜집니다 (LaunchAgent)"
    OPEN=open ;;
  Linux)
    if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
      mkdir -p "$HOME/.config/systemd/user"
      cat > "$HOME/.config/systemd/user/kmu-mail-agent.service" <<EOF
[Unit]
Description=KMU Mail Agent
[Service]
WorkingDirectory=$DIR
ExecStart=${CMD[*]}
Restart=always
RestartSec=60
[Install]
WantedBy=default.target
EOF
      systemctl --user daemon-reload
      systemctl --user enable --now kmu-mail-agent.service
      ok "로그인할 때마다 자동으로 켜집니다 (systemd --user)"
    else
      mkdir -p data/logs
      nohup "${CMD[@]}" >> data/logs/server.log 2>&1 &
      warn "systemd를 쓸 수 없어 지금만 백그라운드로 실행했습니다(nohup). 재부팅 후에는 다시 실행하세요."
    fi
    OPEN=xdg-open ;;
  *) warn "지원하지 않는 OS입니다."; exit 1 ;;
esac

say 6/6 "설정 화면 열기"
echo "  waiting for the server (first start can take about a minute)"
for i in $(seq 1 90); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/health"; then
    ok "메일 비서가 실행 중입니다: http://localhost:$PORT/"
    "$OPEN" "http://localhost:$PORT/setup" >/dev/null 2>&1 || true
    printf '\n설치가 끝났습니다. 브라우저의 "처음 설정" 3단계를 진행하세요.\n'
    exit 0
  fi
  sleep 1
done
warn "서버 응답을 확인하지 못했습니다. 로그: $DIR/data/logs/server.log"
exit 1
