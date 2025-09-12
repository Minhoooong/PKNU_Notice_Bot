# script.py
import os
import subprocess
import sys

# 환경변수 'RUN_NONSBJT'가 1일 경우에만 비교과 수집 실행
RUN_NONSBJT = int(os.getenv("RUN_NONSBJT", "1"))

def run_command(module_name: str, description: str):
    """주어진 모듈을 서브프로세스로 실행하고 결과를 처리하는 함수."""
    command = [sys.executable, "-m", module_name]
    print(f"--- {description} 작업을 시작합니다 ---", flush=True)
    try:
        # stdout, stderr를 실시간으로 스트리밍하지 않고 완료 후 한 번에 출력
        result = subprocess.run(
            command, 
            check=True,         # 실패 시 CalledProcessError 발생
            capture_output=True,  # stdout, stderr 캡처
            text=True,            # 출력을 텍스트로 디코딩
            encoding='utf-8'
        )
        print(f"STDOUT:\n{result.stdout}", flush=True)
        if result.stderr:
            print(f"STDERR:\n{result.stderr}", file=sys.stderr, flush=True)
        print(f"--- {description} 작업 성공 ---", flush=True)
        return result.returncode
    except FileNotFoundError:
        print(f"[에러] 모듈 '{module_name}'을(를) 찾을 수 없습니다.", file=sys.stderr, flush=True)
        return 127
    except subprocess.CalledProcessError as e:
        # 실행 실패 시 캡처된 출력과 함께 에러 로그 표시
        print(f"[에러] {description} 작업 중 오류 발생 (종료 코드: {e.returncode})", file=sys.stderr, flush=True)
        print(f"STDOUT:\n{e.stdout}", file=sys.stderr, flush=True)
        print(f"STDERR:\n{e.stderr}", file=sys.stderr, flush=True)
        return e.returncode

def collect_announcements():
    """학교 공지사항 수집 에이전트를 실행합니다."""
    return run_command("app.run_announcement_agent", "학교 공지사항 수집")

def run_nonSbjt_auto():
    """비교과 프로그램 수집 에이전트를 실행합니다."""
    return run_command("app.run_auto_agent", "비교과 프로그램 수집")

def main():
    """전체 자동화 파이프라인을 실행합니다."""
    print("=== 전체 자동화 작업 시작 ===", flush=True)
    
    # 1. 공지사항 수집 실행
    collect_announcements()
    
    # 2. 비교과 프로그램 수집 실행 (환경변수에 따라 결정)
    if RUN_NONSBJT == 1:
        run_nonSbjt_auto()
    else:
        print("--- RUN_NONSBJT=0이므로 비교과 프로그램 수집을 건너뜁니다. ---", flush=True)
        
    print("=== 모든 자동화 작업 완료 ===", flush=True)

if __name__ == "__main__":
    main()
