r"""교수님께 따로 전달할 'API 키 ZIP'(.env 하나만 들어 있음)을 만듭니다.

    .venv\Scripts\python scripts\make_key_zip.py

- 결과: outputs\kmu-mail-agent-key.zip  (안에는 .env 파일 하나)
- outputs/와 .env는 .gitignore에 있어 GitHub에 올라가지 않습니다. 저장소는 공개라서 키를 넣으면 안 됩니다.
- 받는 쪽: 프로그램 폴더(C:\Users\<사용자>\schedule)에 이 ZIP을 풀어서
  .env가 '설치하기.bat' 옆에 오게 한 뒤 설치하면 키 입력 단계를 건너뜁니다.
- 키 값은 화면에 출력하지 않습니다.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("ROOT_API_KEY", "ANTHROPIC_WORKSPACE_ID", "MS_CLIENT_ID")


def env_names(text: str) -> dict[str, bool]:
    """{변수 이름: 값이 있는지} — 값 자체는 돌려주지 않습니다."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            out[name.strip()] = bool(value.split("#", 1)[0].strip())
    return out


def main() -> int:
    env_path = ROOT / ".env"
    if not env_path.exists():
        print("❌ .env 파일이 없습니다. 먼저 이 PC에서 설치를 마쳐 .env를 만드세요.")
        return 1
    env_text = env_path.read_text(encoding="utf-8")
    names = env_names(env_text)
    missing = [n for n in REQUIRED if not names.get(n)]
    if missing:
        print(f"❌ .env에 값이 비어 있습니다: {', '.join(missing)}")
        return 1
    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / "kmu-mail-agent-key.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(".env", env_text)
    print(f"✅ 만들었습니다: {out}")
    print(f"   들어 있는 것: .env ({', '.join(n for n, v in names.items() if v)})")
    print("   API 키가 들어 있습니다. GitHub·공개 게시판에 올리지 말고 교수님께만 직접 보내세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
