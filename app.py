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

app = Flask(__name__)
app.secret_key = SECRET_KEY


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
    db.update_status(job_id, new_status)
    db.close()

    return _back_to_index()


@app.route("/jobs/<int:job_id>/memo", methods=["POST"])
@login_required
def set_memo(job_id):
    memo = request.form.get("memo", "")

    db = DB()
    db.update_memo(job_id, memo)
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
        loc_names = request.form.getlist("location") or [next(iter(LOCATION))]
        exp_names = request.form.getlist("experience") or [next(iter(EXPERIENCE))]
        edu_names = request.form.getlist("education") or [next(iter(EDUCATION))]
        type_names = request.form.getlist("job_type") or [next(iter(JOB_TYPE))]

        if not cat_names:
            return render_template(
                "crawl.html",
                **form_context,
                error="직무를 하나 이상 선택해주세요.",
                selected_job_categories=default_job_categories,
            )

        cat_sel = [(name, JOB_CATEGORY[name]) for name in cat_names if name in JOB_CATEGORY]
        loc_sel = [(name, LOCATION[name]) for name in loc_names if name in LOCATION]
        exp_sel = [(name, EXPERIENCE[name]) for name in exp_names if name in EXPERIENCE]
        edu_sel = [(name, EDUCATION[name]) for name in edu_names if name in EDUCATION]
        type_sel = [(name, JOB_TYPE[name]) for name in type_names if name in JOB_TYPE]

        conditions = build_conditions(cat_sel, loc_sel, exp_sel, edu_sel, type_sel)
        saved, found = run_web_conditions(conditions)

        flash(f"크롤링 완료: 조건 {len(conditions)}개 / 발견 {found}건 / 신규 저장 {saved}건")
        return redirect(url_for("index"))

    return render_template(
        "crawl.html",
        **form_context,
        error=None,
        selected_job_categories=default_job_categories,
    )


if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        threaded=True,
    )
