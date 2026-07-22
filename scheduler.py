"""
매일 정해진 시각에 crawler.py의 자동 크롤링(run_scheduled)을 실행하는 상주 스크립트.

사용법:
    python scheduler.py

- 선택 사항입니다: config.py의 SCHEDULE_TIME을 "HH:MM" 형식으로 설정해야만 동작하며,
  설정하지 않으면(기본값 None) 이 스크립트를 실행해도 아무 것도 하지 않고 바로 끝납니다.
- SCHEDULE_TIME이 설정되어 있으면 매일 그 시각에 맞춰 한 번 실행됩니다.
- config.py의 SEARCH_CONDITIONS에 등록해둔 조건으로 크롤링하며, 기존 데이터는
  지우지 않고 새 공고만 추가로 저장합니다.
- 이 프로세스가 켜져 있는 동안만 동작하므로, 터미널을 계속 열어두어야 합니다.
- 실행 기록은 콘솔과 crawler.log 파일에 함께 남습니다.
- 종료하려면 Ctrl+C를 누르세요.
"""
import time

import schedule

from config import SCHEDULE_TIME
from crawler import logger, run_scheduled


def job():
    try:
        run_scheduled()
    except Exception:
        logger.exception("자동 크롤링 중 오류가 발생했습니다.")


def main():
    if not SCHEDULE_TIME:
        logger.info("config.py의 SCHEDULE_TIME이 설정되지 않아 스케줄러를 실행하지 않습니다.")
        return

    schedule.every().day.at(SCHEDULE_TIME).do(job)
    logger.info(f"스케줄러 시작. 매일 {SCHEDULE_TIME}에 자동 크롤링합니다. (Ctrl+C로 종료)")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
