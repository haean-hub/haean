"""Playwright chromium 실행 공통 헬퍼.

Windows 작업 스케줄러 세션에서 사흘간 collect_competitors.py/collect_company_stat.py가
"Executable doesn't exist" 로 조용히 실패한 적이 있다(대화형 세션에서는 재현 안 됨 -
LOCALAPPDATA를 비워도 재현 안 됐음, 원인 미확정). 기본 실행이 실패하면 ms-playwright
디렉터리를 직접 뒤져서 실행파일 절대경로를 찾아 명시적으로 넘겨 재시도한다.
"""
import glob
import os


def launch_chromium(playwright):
    """p.chromium.launch()를 시도하고, 실패하면 실행파일을 직접 찾아 재시도한다."""
    try:
        return playwright.chromium.launch()
    except Exception as first_err:
        exe = _find_headless_shell_exe()
        if not exe:
            raise first_err
        return playwright.chromium.launch(executable_path=exe)


def _find_headless_shell_exe() -> str | None:
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "ms-playwright"
    )
    if not browsers_path:
        return None
    pattern = os.path.join(browsers_path, "chromium_headless_shell-*", "**", "chrome-headless-shell.exe")
    matches = glob.glob(pattern, recursive=True)
    if matches:
        return sorted(matches)[-1]
    pattern = os.path.join(browsers_path, "chromium-*", "**", "chrome.exe")
    matches = glob.glob(pattern, recursive=True)
    return sorted(matches)[-1] if matches else None
