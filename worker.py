"""
GitHub Actions에서 주기적으로(예: 10분마다) 실행되는 크롤링 워커.
Selenium/Chrome이 필요하므로 Vercel(app.py)이 아니라 여기서만 실행한다.

처리 순서:
1. 웹에서 "크롤링 요청" 버튼으로 쌓인 대기열(crawl_request)을 먼저 처리.
2. 계정별 자동 크롤링 스케줄(schedule_setting) 중 오늘 아직 실행 안 된 것을 처리.
"""
from datetime import datetime

from database import DB
from conditions import selection_to_conditions
from crawler import run_web_conditions, logger


def _names(csv_value):
    return [n for n in (csv_value or "").split(",") if n]


def process_pending_requests():
    db = DB()
    try:
        pending = db.get_pending_crawl_requests()
    finally:
        db.close()

    for req in pending:
        try:
            conditions = selection_to_conditions(
                _names(req["job_categories"]),
                _names(req["locations"]),
                _names(req["experiences"]),
                _names(req["educations"]),
                _names(req["job_types"]),
            )
            saved, found = run_web_conditions(conditions, req["user_id"])

            db = DB()
            try:
                db.mark_crawl_request_done(req["id"], saved, found)
            finally:
                db.close()
        except Exception:
            logger.exception(f"[대기열 처리 실패] request_id={req['id']}")


def cleanup_expired_jobs():
    db = DB()
    try:
        deleted = db.delete_expired_jobs()
        if deleted:
            logger.info(f"[마감일 정리] 마감일 지난 공고 {deleted}건 삭제")
    finally:
        db.close()


def process_due_schedules():
    now = datetime.now()
    current_time = now.strftime("%H:%M")
    today = now.date()

    db = DB()
    try:
        due = db.get_due_schedules(current_time, today)
    finally:
        db.close()

    for sched in due:
        try:
            cat_names = _names(sched["job_categories"])
            if cat_names:
                conditions = selection_to_conditions(
                    cat_names,
                    _names(sched["locations"]),
                    _names(sched["experiences"]),
                    _names(sched["educations"]),
                    _names(sched["job_types"]),
                )
                run_web_conditions(conditions, sched["user_id"])

            db = DB()
            try:
                db.mark_schedule_run(sched["user_id"], today)
            finally:
                db.close()
        except Exception:
            logger.exception(f"[예약 스케줄 처리 실패] user_id={sched['user_id']}")


if __name__ == "__main__":
    process_pending_requests()
    process_due_schedules()
    cleanup_expired_jobs()
