import json
from pathlib import Path
import hashlib

# 프로젝트 루트에 'data' 디렉터리를 생성하고 파일을 관리합니다.
# GitHub Actions에서 실행될 것을 고려하여 절대 경로 대신 상대 경로를 사용합니다.
DATA_DIR = Path("data")
SAVE_JSON_PATH = DATA_DIR / "nonSbjt_all.json"
SEEN_DB_PATH = DATA_DIR / "pknu_nonSbjt_seen.txt"

def ensure_data_dir():
    """'data' 디렉터리가 없으면 생성합니다."""
    DATA_DIR.mkdir(exist_ok=True)

def get_seen_ids() -> set:
    """
    이미 알림을 보낸 프로그램 ID 목록을 불러옵니다.

    Returns:
        set: 프로그램 ID들이 담긴 집합.
    """
    ensure_data_dir()
    if not SEEN_DB_PATH.exists():
        return set()
    return set(SEEN_DB_PATH.read_text(encoding="utf-8").splitlines())

def save_all_programs(programs: list):
    """
    크롤링한 모든 프로그램 목록을 JSON 파일로 저장합니다.

    Args:
        programs (list): 프로그램 딕셔너리 리스트.
    """
    ensure_data_dir()
    SAVE_JSON_PATH.write_text(
        json.dumps(programs, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def add_seen_ids(seen_ids: set):
    """
    새로운 프로그램 ID가 포함된 전체 ID 목록을 파일에 덮어씁니다.

    Args:
        seen_ids (set): 업데이트된 전체 프로그램 ID 집합.
    """
    ensure_data_dir()
    # set을 list로 변환 후 정렬하여 저장
    SEEN_DB_PATH.write_text("\n".join(sorted(list(seen_ids))), encoding="utf-8")

def generate_id(program: dict) -> str:
    """
    프로그램의 URL을 기반으로 고유한 해시 ID를 생성합니다.

    Args:
        program (dict): 'url' 키를 포함하는 프로그램 딕셔너리.

    Returns:
        str: 고유 식별자 ID.
    """
    return hashlib.sha1(program['url'].encode()).hexdigest()[:16]

