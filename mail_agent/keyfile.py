"""Finds the key file (.env) the lab sent and puts it in the program folder.

The .env usually arrives by KakaoTalk or e-mail and lands in Downloads or
"카카오톡 받은 파일", often renamed ('env', '.env.txt', '.env (1)'). Instead of making the
user move it by hand, look in those places and recognise it by its content
(a non-empty ROOT_API_KEY=), not by its name. Values are never printed or logged.
"""
from __future__ import annotations

import re
from pathlib import Path

KEY_LINE = re.compile(r"^\s*ROOT_API_KEY\s*=\s*\S", re.M)
MAX_SIZE = 8192


def _is_key_file(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size <= MAX_SIZE and \
            bool(KEY_LINE.search(p.read_text(encoding="utf-8-sig", errors="ignore")))
    except OSError:
        return False


def search_dirs(project_root: Path, home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    dirs = [project_root, home / "Downloads", home / "Desktop", home / "Documents" / "카카오톡 받은 파일"]
    for od in home.glob("OneDrive*"):  # Documents / Desktop redirected to OneDrive (e.g. 'OneDrive - 계명대학교')
        dirs += [od / "문서" / "카카오톡 받은 파일", od / "Documents" / "카카오톡 받은 파일", od / "바탕 화면", od / "Desktop"]
    return [d for d in dirs if d.is_dir()]


def find_key_file(project_root: Path, home: Path | None = None) -> Path | None:
    """Newest file named like .env (env, .env.txt, .env (1) …) that holds a ROOT_API_KEY value."""
    found = []
    for d in search_dirs(project_root, home):
        for p in d.glob("*env*"):
            if p.resolve() == (project_root / ".env").resolve() or p.suffix.lower() in (".zip", ".py", ".ps1", ".example"):
                continue
            if _is_key_file(p):
                found.append(p)
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def adopt_key_file(project_root: Path, home: Path | None = None) -> str | None:
    """Copy a found key file to <project>/.env when .env is missing. Returns a message, or None."""
    target = project_root / ".env"
    if target.exists():
        return None
    src = find_key_file(project_root, home)
    if src is None:
        return None
    # Notepad / PowerShell 5.1 often add a BOM, which would hide the first key from dotenv → write it without one
    target.write_text(src.read_text(encoding="utf-8-sig", errors="ignore"), encoding="utf-8")
    where = "이 폴더" if src.parent == project_root else str(src.parent)
    return f"{where}에 있던 '{src.name}' 파일을 찾아 .env로 적용했습니다."
