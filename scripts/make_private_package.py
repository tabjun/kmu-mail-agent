"""교수님께 직접 전달할 설치 ZIP을 만듭니다 (API 키가 든 .env 포함).

    .venv\\Scripts\\python scripts\\make_private_package.py

- 커밋된 파일(git HEAD)에 지금 PC의 .env를 그대로 넣어 outputs/ 아래에 ZIP을 만듭니다.
- outputs/는 .gitignore에 있어 GitHub에 올라가지 않습니다. GitHub 저장소는 공개라서
  .env를 저장소에 넣으면 누구나 키를 가져가 연구실 예산을 쓸 수 있습니다.
- 만든 ZIP은 메신저·메일로 교수님께만 보내세요. 받은 쪽은 압축을 풀고 '설치하기.bat'만 누르면
  키 입력 단계 없이 설치됩니다.
- 키 값은 화면에 출력하지 않습니다.
"""
from __future__ import annotations

import io
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "kmu-mail-agent/"
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

    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print("⚠ 커밋하지 않은 변경이 있습니다. ZIP에는 마지막 커밋 내용만 들어갑니다.")

    archive = subprocess.run(["git", "archive", "--format=zip", f"--prefix={PREFIX}", "HEAD"], cwd=ROOT,
                             capture_output=True, check=True).stdout
    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"kmu-mail-agent-교수님용-{date.today():%Y%m%d}.zip"

    with zipfile.ZipFile(io.BytesIO(archive)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item))
        dst.writestr(PREFIX + ".env", env_text)

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    print(f"✅ 만들었습니다: {out}")
    print(f"   커밋 {head} + .env ({', '.join(n for n, v in names.items() if v)})")
    print("   이 파일에는 API 키가 들어 있습니다. GitHub·공개 게시판에 올리지 말고 교수님께만 직접 보내세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
