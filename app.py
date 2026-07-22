import threading
import time
from datetime import datetime
from functools import wraps

import mysql.connector
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, get_flashed_messages,
)
from werkzeug.security import generate_password_hash, check_password_hash

from database import DB, STATUS_CHOICES
from config import SECRET_KEY, JOB_CATEGORY, LOCATION, EXPERIENCE, EDUCATION, JOB_TYPE
from crawler import build_conditions, run_web_conditions
from guideline import build_guideline

app = Flask(__name__)
app.secret_key = SECRET_KEY


def _selection_to_conditions(cat_names, loc_names, exp_names, edu_names, type_names):
    """체크된 이름 리스트들(직무/지역/경력/학력/근무형태)을 condition dict 리스트로 변환.
    직무 외에는 비어있으면 각각 전국/전체로 기본값 처리."""
    loc_names = loc_names or [next(iter(LOCATION))]
    exp_names = exp_names or [next(iter(EXPERIENCE))]
    edu_names = edu_names or [next(iter(EDUCATION))]
    type_names = type_names or [next(iter(JOB_TYPE))]

    cat_sel = [(name, JOB_CATEGORY[name]) for name in cat_names if name in JOB_CATEGORY]
    loc_sel = [(name, LOCATION[name]) for name in loc_names if name in LOCATION]
    exp_sel = [(name, EXPERIENCE[name]) for name in exp_names if name in EXPERIENCE]
    edu_sel = [(name, EDUCATION[name]) for name in edu_names if name in EDUCATION]
    type_sel = [(name, JOB_TYPE[name]) for name in type_names if name in JOB_TYPE]

    return build_conditions(cat_sel, loc_sel, exp_sel, edu_sel, type_sel)


def _run_due_schedules():
    now = datetime.now()
    current_time = now.strftime("%H:%M")
    today = now.date()

    db = DB()
    try:
        due = db.get_due_schedules(current_time, today)
    finally:
        db.close()

    for sched in due:
        cat_names = [n for n in (sched["job_categories"] or "").split(",") if n]
        loc_names = [n for n in (sched["locations"] or "").split(",") if n]
        exp_names = [n for n in (sched["experiences"] or "").split(",") if n]
        edu_names = [n for n in (sched["educations"] or "").split(",") if n]
        type_names = [n for n in (sched["job_types"] or "").split(",") if n]

        if cat_names:
            conditions = _selection_to_conditions(
                cat_names, loc_names, exp_names, edu_names, type_names
            )
            run_web_conditions(conditions)

        db = DB()
        try:
            db.mark_schedule_run(sched["user_id"], today)
        finally:
            db.close()


def _scheduler_background_loop():
    while True:
        try:
            _run_due_schedules()
        except Exception:
            pass
        time.sleep(60)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        job_categories = [
            name for name in request.form.getlist("job_category")
            if name in JOB_CATEGORY
        ]

        error = None
        if not username:
            error = "아이디를 입력해주세요."
        elif not password:
            error = "비밀번호를 입력해주세요."
        elif password != password_confirm:
            error = "비밀번호가 일치하지 않습니다."
        elif not job_categories:
            error = "희망 직무를 하나 이상 선택해주세요."

        if not error:
            db = DB()
            try:
                if db.get_user_by_username(username):
                    error = "이미 사용 중인 아이디입니다."
                else:
                    user_id = db.create_user(
                        username,
                        generate_password_hash(password),
                        ",".join(job_categories),
                    )
            except mysql.connector.IntegrityError:
                error = "이미 사용 중인 아이디입니다."
            finally:
                db.close()

        if error:
            return render_template(
                "signup.html",
                job_categories=JOB_CATEGORY,
                error=error,
                username=username,
                selected_job_categories=job_categories,
            )

        session.clear()
        session["user_id"] = user_id
        session["username"] = username
        return redirect(url_for("index"))

    return render_template(
        "signup.html",
        job_categories=JOB_CATEGORY,
        error=None,
        username="",
        selected_job_categories=[],
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        db = DB()
        user = db.get_user_by_username(username)
        db.close()

        if not user or not check_password_hash(user["password_hash"], password):
            return render_template(
                "login.html", error="아이디 또는 비밀번호가 올바르지 않습니다.", username=username
            )

        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]

        next_url = request.args.get("next") or url_for("index")
        return redirect(next_url)

    return render_template("login.html", error=None, username="")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():

    region = request.args.get("region", "").strip() or None
    keyword = request.args.get("keyword", "").strip() or None
    condition_name = request.args.get("condition_name", "").strip() or None
    status = request.args.get("status", "").strip() or None
    view = request.args.get("view", "list").strip() or "list"

    try:
        db = DB()

        jobs = db.get_jobs(
            session["user_id"],
            region=region,
            keyword=keyword,
            condition_name=condition_name,
            status=status,
            group_by_company=(view == "company"),
        )
        condition_names = db.get_condition_names()

        db.close()

        return render_template(
            "index.html",
            jobs=jobs,
            condition_names=condition_names,
            status_choices=STATUS_CHOICES,
            selected_region=region or "",
            selected_keyword=keyword or "",
            selected_condition=condition_name or "",
            selected_status=status or "",
            view=view,
            username=session.get("username"),
        )

    except Exception as e:
        return f"DB ERROR : {e}"


def _back_to_index():
    """상태/메모 변경 후 원래 보고 있던 필터·화면으로 되돌아가기"""
    params = {
        key: request.form.get(key, "")
        for key in ("region", "keyword", "condition_name", "filter_status", "view")
    }
    return redirect(url_for(
        "index",
        region=params["region"] or None,
        keyword=params["keyword"] or None,
        condition_name=params["condition_name"] or None,
        status=params["filter_status"] or None,
        view=params["view"] or "list",
    ))


@app.route("/jobs/<int:job_id>/status", methods=["POST"])
@login_required
def set_status(job_id):
    new_status = request.form.get("new_status", "").strip()

    if new_status not in STATUS_CHOICES:
        return "잘못된 상태 값입니다", 400

    db = DB()
    db.update_status(session["user_id"], job_id, new_status)
    db.close()

    return _back_to_index()


@app.route("/jobs/<int:job_id>/memo", methods=["POST"])
@login_required
def set_memo(job_id):
    memo = request.form.get("memo", "")

    db = DB()
    db.update_memo(session["user_id"], job_id, memo)
    db.close()

    return _back_to_index()


@app.route("/crawl", methods=["GET", "POST"])
@login_required
def crawl():
    db = DB()
    user = db.get_user_by_id(session["user_id"])
    db.close()

    default_job_categories = (user["job_categories"].split(",") if user and user["job_categories"] else [])

    form_context = dict(
        job_categories=JOB_CATEGORY,
        locations=LOCATION,
        experiences=EXPERIENCE,
        educations=EDUCATION,
        job_types=JOB_TYPE,
        username=session.get("username"),
    )

    if request.method == "POST":
        cat_names = request.form.getlist("job_category")
        loc_names = request.form.getlist("location")
        exp_names = request.form.getlist("experience")
        edu_names = request.form.getlist("education")
        type_names = request.form.getlist("job_type")

        if not cat_names:
            return render_template(
                "crawl.html",
                **form_context,
                error="직무를 하나 이상 선택해주세요.",
                selected_job_categories=default_job_categories,
            )

        conditions = _selection_to_conditions(
            cat_names, loc_names, exp_names, edu_names, type_names
        )
        saved, found = run_web_conditions(conditions)

        flash(f"크롤링 완료: 조건 {len(conditions)}개 / 발견 {found}건 / 신규 저장 {saved}건")
        return redirect(url_for("index"))

    return render_template(
        "crawl.html",
        **form_context,
        error=None,
        selected_job_categories=default_job_categories,
    )


@app.route("/schedule", methods=["GET", "POST"])
@login_required
def schedule_settings():
    user_id = session["user_id"]

    db = DB()
    try:
        existing = db.get_schedule(user_id)
        user = db.get_user_by_id(user_id)
    finally:
        db.close()

    default_job_categories = (
        user["job_categories"].split(",") if user and user["job_categories"] else []
    )

    form_context = dict(
        job_categories=JOB_CATEGORY,
        locations=LOCATION,
        experiences=EXPERIENCE,
        educations=EDUCATION,
        job_types=JOB_TYPE,
        username=session.get("username"),
    )

    if request.method == "POST":
        enabled = request.form.get("enabled") == "on"
        schedule_time = request.form.get("schedule_time", "").strip()
        cat_names = request.form.getlist("job_category")
        loc_names = request.form.getlist("location")
        exp_names = request.form.getlist("experience")
        edu_names = request.form.getlist("education")
        type_names = request.form.getlist("job_type")

        error = None
        if enabled and not schedule_time:
            error = "자동 크롤링을 사용하려면 시각을 지정해주세요."
        elif enabled and not cat_names:
            error = "직무를 하나 이상 선택해주세요."

        if error:
            return render_template(
                "schedule.html",
                **form_context,
                error=error,
                schedule_enabled=enabled,
                schedule_time=schedule_time,
                selected_job_categories=cat_names or default_job_categories,
                selected_locations=loc_names,
                selected_experiences=exp_names,
                selected_educations=edu_names,
                selected_job_types=type_names,
            )

        db = DB()
        try:
            db.upsert_schedule(
                user_id,
                enabled,
                schedule_time or "09:00",
                ",".join(cat_names),
                ",".join(loc_names),
                ",".join(exp_names),
                ",".join(edu_names),
                ",".join(type_names),
            )
        finally:
            db.close()

        flash("자동 크롤링 설정을 저장했습니다.")
        return redirect(url_for("schedule_settings"))

    if existing:
        return render_template(
            "schedule.html",
            **form_context,
            error=None,
            schedule_enabled=bool(existing["enabled"]),
            schedule_time=existing["schedule_time"],
            selected_job_categories=(
                existing["job_categories"].split(",") if existing["job_categories"] else default_job_categories
            ),
            selected_locations=(existing["locations"].split(",") if existing["locations"] else []),
            selected_experiences=(existing["experiences"].split(",") if existing["experiences"] else []),
            selected_educations=(existing["educations"].split(",") if existing["educations"] else []),
            selected_job_types=(existing["job_types"].split(",") if existing["job_types"] else []),
        )

    return render_template(
        "schedule.html",
        **form_context,
        error=None,
        schedule_enabled=False,
        schedule_time="09:00",
        selected_job_categories=default_job_categories,
        selected_locations=[],
        selected_experiences=[],
        selected_educations=[],
        selected_job_types=[],
    )


@app.route("/guide")
@login_required
def guide():
    db = DB()
    try:
        jobs = db.get_interested_jobs(session["user_id"])
    finally:
        db.close()

    data = build_guideline(jobs)

    return render_template(
        "guide.html",
        username=session.get("username"),
        **data,
    )


if __name__ == "__main__":

    threading.Thread(target=_scheduler_background_loop, daemon=True).start()

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        threaded=True,
    )
