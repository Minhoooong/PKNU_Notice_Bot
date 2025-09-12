import os
import subprocess
import sys

RUN_NONSBJT = int(os.getenv("RUN_NONSBJT", "1"))

def run_nonSbjt_auto():
    """
    세부 메서드(app.run_auto_agent)을 서비스프로세스로 실행.
    환경변수는 그대로 실행, 종료코드는 전달.
    """
    cmd = [sys.executable, "-m", "app.run_auto_agent"]
    try:
        proc = subprocess.run(cmd, check=False)
        return proc.returncode
    except FileNotFoundError:
        print("[run_nonSbjt_auto] app.run_auto_agent 모듈을 찾을 수 없습니다.", file=sys.stderr)
        return 127

def collect_announcements():
    """
    기존 공지 수집 로직의 진입점.
    주의: 이전 'whalebe' 비교간 파신 간단은 제거/ube44활성화.
    """
    # TODO: 실제 수집 로직 연결 (repo 내부 수집기 호출)
    print("[collect_announcements] 공지 수집 로직 실행(구현 연결 필요)")

def main():
    collect_announcements()
    if RUN_NONSBJT == 1:
        print("[main] RUN_NONSBJT=1 → run_nonSbjt_auto 실행")
        rc = run_nonSbjt_auto()
        if rc != 0:
            print(f"[main] run_nonSbjt_auto 종료코드 {rc}", file=sys.stderr)
            sys.exit(rc)

if __name__ == "__main__":
    main()
