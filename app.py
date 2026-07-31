from datetime import date
from functools import wraps

import mysql.connector
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, get_flashed_messages,
)
from werkzeug.security import generate_password_hash, check_password_hash

from database import DB, STATUS_CHOICES
from settings import SECRET_KEY
from config import JOB_CATEGORY, LOCATION, EXPERIENCE, EDUCATION, JOB_TYPE
from job_matching import analyze_resume_match, JobMatchError
from cover_letter import (
    analyze_company, generate_cover_letter, revise_cover_letter,
    review_cover_letter, CoverLetterError,
)
from company_research import analyze_company_research, parse_sections, SECTION_ORDER, CompanyResearchError
from crypto_utils import encrypt_secret, decrypt_secret

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 이력서 업로드 용량 제한 (8MB)
# debug=False로 실행 중이라 기본값이면 템플릿을 최초 1회만 읽고 메모리에 캐싱한다.
# 그러면 templates/*.html을 고쳐도 서버를 재시작하기 전까지는 화면에 반영되지 않는다.
app.config["TEMPLATES_AUTO_RELOAD"] = True


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


PAGE_SIZE = 50


@app.route("/")
@login_required
def index():

    region = request.args.get("region", "").strip() or None
    keyword = request.args.get("keyword", "").strip() or None
    condition_name = request.args.get("condition_name", "").strip() or None
    status = request.args.get("status", "").strip() or None
    view = request.args.get("view", "list").strip() or "list"
    sort = request.args.get("sort", "").strip() or None

    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    page = max(1, page)

    try:
        db = DB()

        all_jobs = db.get_jobs(
            session["user_id"],
            region=region,
            keyword=keyword,
            condition_name=condition_name,
            status=status,
            group_by_company=(view == "company"),
            sort_by_deadline=(sort == "deadline"),
        )
        condition_names = db.get_condition_names(session["user_id"])

        db.close()

        total_count = len(all_jobs)
        total_pages = max(1, -(-total_count // PAGE_SIZE))  # 올림 나눗셈
        page = min(page, total_pages)
        start = (page - 1) * PAGE_SIZE
        jobs = all_jobs[start:start + PAGE_SIZE]

        return render_template(
            "index.html",
            jobs=jobs,
            total_count=total_count,
            page=page,
            total_pages=total_pages,
            condition_names=condition_names,
            status_choices=STATUS_CHOICES,
            selected_region=region or "",
            selected_keyword=keyword or "",
            selected_condition=condition_name or "",
            selected_status=status or "",
            selected_sort=sort or "",
            view=view,
            today=date.today(),
            username=session.get("username"),
        )

    except Exception as e:
        return f"DB ERROR : {e}"


def _back_to_index(page_override=None):
    """상태/메모/삭제 처리 후 원래 보고 있던 필터·화면·페이지로 되돌아가기.
    page_override: 목록 순서가 바뀌어 원래 보던 페이지 번호가 더 이상 의미 없어졌을 때
    (예: '관심있음'으로 바꿔서 최상단으로 이동) 강제로 이동시킬 페이지."""
    params = {
        key: request.form.get(key, "")
        for key in ("region", "keyword", "condition_name", "filter_status", "view", "page", "sort")
    }
    return redirect(url_for(
        "index",
        region=params["region"] or None,
        keyword=params["keyword"] or None,
        condition_name=params["condition_name"] or None,
        status=params["filter_status"] or None,
        view=params["view"] or "list",
        page=page_override or params["page"] or None,
        sort=params["sort"] or None,
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

    # '관심있음'으로 바꾸면 목록 보기 최상단으로 정렬되므로(get_jobs 참고),
    # 원래 페이지 그대로 두면 눈에 안 띄니 1페이지로 보내서 실제로 이동한 걸 보여줌
    is_list_view = request.form.get("view", "list") == "list"
    page_override = "1" if (new_status == "관심있음" and is_list_view) else None

    return _back_to_index(page_override=page_override)


@app.route("/jobs/<int:job_id>/memo", methods=["POST"])
@login_required
def set_memo(job_id):
    memo = request.form.get("memo", "")

    db = DB()
    db.update_memo(session["user_id"], job_id, memo)
    db.close()

    return _back_to_index()


@app.route("/jobs/delete", methods=["POST"])
@login_required
def delete_jobs_route():
    job_ids = [int(v) for v in request.form.getlist("job_ids") if v.isdigit()]

    if not job_ids:
        flash("삭제할 공고를 선택해주세요.")
    else:
        db = DB()
        try:
            deleted = db.delete_jobs(session["user_id"], job_ids)
        finally:
            db.close()
        flash(f"{deleted}건의 공고를 삭제했습니다.")

    return _back_to_index()


@app.route("/crawl", methods=["GET", "POST"])
@login_required
def crawl():
    db = DB()
    try:
        user = db.get_user_by_id(session["user_id"])

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
                    recent_requests=db.get_recent_crawl_requests(session["user_id"]),
                )

            # Selenium 크롤링은 이 서버(Vercel 서버리스)가 아니라 GitHub Actions 워커가
            # 대기열을 폴링하며 처리한다 (worker.py). 여기서는 요청만 접수한다.
            db.create_crawl_request(
                session["user_id"],
                ",".join(cat_names),
                ",".join(loc_names),
                ",".join(exp_names),
                ",".join(edu_names),
                ",".join(type_names),
            )

            flash("크롤링 요청이 접수되었습니다. 잠시 후 아래 목록에서 결과를 확인해주세요.")
            return redirect(url_for("crawl"))

        return render_template(
            "crawl.html",
            **form_context,
            error=None,
            selected_job_categories=default_job_categories,
            recent_requests=db.get_recent_crawl_requests(session["user_id"]),
        )
    finally:
        db.close()


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


@app.route("/match", methods=["GET", "POST"])
@login_required
def job_match():
    # request.values는 GET 쿼리스트링/POST 폼 양쪽 다 읽어주므로,
    # 필터 조건 재조회(GET)와 분석 요청 제출(POST) 양쪽에서 같은 필터가 유지된다.
    region = request.values.get("region", "").strip()
    keyword = request.values.get("keyword", "").strip()
    condition_name = request.values.get("condition_name", "").strip()
    status = request.values.get("status", "").strip()

    result = None
    error = None

    db = DB()
    try:
        jobs = db.get_jobs(
            session["user_id"],
            region=region or None,
            keyword=keyword or None,
            condition_name=condition_name or None,
            status=status or None,
        )
        condition_names = db.get_condition_names(session["user_id"])

        if request.method == "POST":
            selected_ids = {int(v) for v in request.form.getlist("job_ids") if v.isdigit()}
            selected_jobs = [j for j in jobs if j["id"] in selected_ids]

            resume_file = request.files.get("resume")

            if not selected_jobs:
                error = "비교할 채용공고를 1개 이상 선택해주세요."
            elif not resume_file or not resume_file.filename:
                error = "이력서 PDF 파일을 선택해주세요."
            elif not resume_file.filename.lower().endswith(".pdf"):
                error = "PDF 파일만 업로드할 수 있습니다."
            else:
                pdf_bytes = resume_file.read()
                api_key = decrypt_secret(db.get_api_key_encrypted(session["user_id"]))
                try:
                    analysis = analyze_resume_match(pdf_bytes, selected_jobs, api_key)
                except JobMatchError as e:
                    error = str(e)
                else:
                    jobs_by_id = {j["id"]: j for j in selected_jobs}
                    matches = []
                    for m in analysis.get("matches", []):
                        job = jobs_by_id.get(m.get("job_id"))
                        if not job:
                            continue
                        matches.append({
                            "job": job,
                            "match_percent": m.get("match_percent", 0),
                            "missing_skills": m.get("missing_skills") or [],
                            "reason": m.get("reason", ""),
                        })
                    matches.sort(key=lambda m: m["match_percent"], reverse=True)

                    result = {
                        "extracted_skills": analysis.get("extracted_skills", []),
                        "overall_summary": analysis.get("overall_summary", ""),
                        "matches": matches,
                    }
        else:
            # 처음 들어왔을 때는 필터링된 공고 전체가 기본으로 체크되어 있도록
            selected_ids = {j["id"] for j in jobs}
    finally:
        db.close()

    return render_template(
        "match.html",
        username=session.get("username"),
        jobs=jobs,
        condition_names=condition_names,
        status_choices=STATUS_CHOICES,
        selected_region=region,
        selected_keyword=keyword,
        selected_condition=condition_name,
        selected_status=status,
        selected_ids=selected_ids,
        result=result,
        error=error,
    )


@app.route("/cover-letter/<int:job_id>", methods=["GET", "POST"])
@login_required
def cover_letter_page(job_id):
    db = DB()
    try:
        jobs = db.get_jobs(session["user_id"])
        job = next((j for j in jobs if j["id"] == job_id), None)
        if not job:
            flash("존재하지 않는 공고입니다.")
            return redirect(url_for("index"))

        api_key = decrypt_secret(db.get_api_key_encrypted(session["user_id"]))

        # DB에 저장해둔 이전 결과를 기본값으로 사용 - 페이지를 나갔다 다시 들어와도
        # 회사 분석/자소서를 계속 볼 수 있도록 함
        saved = db.get_cover_letter(session["user_id"], job_id)
        company_analysis = saved["company_analysis"] if saved else None
        draft = saved["cover_letter"] if saved else None
        interaction_id = saved["cover_letter_interaction_id"] if saved else None

        error = None
        warning = None

        if request.method == "POST":
            # 같은 페이지 내에서 넘어온 hidden 필드 값이 있으면 그걸 우선 사용
            # (DB 저장 전에 이미 여러 단계를 거쳤을 수 있으므로)
            company_analysis = request.form.get("company_analysis", company_analysis)
            draft = request.form.get("draft", draft)
            interaction_id = request.form.get("interaction_id") or interaction_id

            action = request.form.get("action")

            if action == "analyze":
                try:
                    company_analysis, interaction_id, used_search = analyze_company(job, api_key)
                except CoverLetterError as e:
                    error = str(e)
                else:
                    if not used_search:
                        warning = "웹 검색 무료 한도 초과로 검색 없이 생성된 분석입니다. 최신 뉴스/실적 등은 부정확할 수 있어요."
                    # 새로 분석하면 대화 맥락이 새로 시작되므로, 이전 체인에 이어져 있던
                    # 자소서 초안은 더 이상 유효하지 않아 함께 비운다
                    draft = None
                    db.save_cover_letter(session["user_id"], job_id, company_analysis, draft, interaction_id)

            elif action == "generate":
                resume_file = request.files.get("resume")
                if not resume_file or not resume_file.filename:
                    error = "이력서 PDF 파일을 선택해주세요."
                elif not resume_file.filename.lower().endswith(".pdf"):
                    error = "PDF 파일만 업로드할 수 있습니다."
                else:
                    pdf_bytes = resume_file.read()
                    try:
                        draft, interaction_id = generate_cover_letter(job, pdf_bytes, interaction_id, api_key)
                    except CoverLetterError as e:
                        error = str(e)
                    else:
                        db.save_cover_letter(session["user_id"], job_id, company_analysis, draft, interaction_id)

            elif action == "revise":
                feedback = (request.form.get("feedback") or "").strip()
                try:
                    draft, interaction_id = revise_cover_letter(feedback, interaction_id, api_key)
                except CoverLetterError as e:
                    error = str(e)
                else:
                    db.save_cover_letter(session["user_id"], job_id, company_analysis, draft, interaction_id)
    finally:
        db.close()

    return render_template(
        "cover_letter.html",
        username=session.get("username"),
        job=job,
        company_analysis=company_analysis,
        draft=draft,
        interaction_id=interaction_id,
        error=error,
        warning=warning,
    )


@app.route("/cover-letter-review", methods=["GET", "POST"])
@login_required
def cover_letter_review_page():
    # 회사/공고와 무관하게 자기소개서 원문 자체를 첨삭하는 단발성 기능이라
    # job_match처럼 DB에 저장하지 않고 요청마다 새로 처리한다.
    review = None
    error = None
    draft_text = ""

    if request.method == "POST":
        draft_text = (request.form.get("draft_text") or "").strip()
        draft_file = request.files.get("draft_pdf")
        pdf_bytes = None
        if draft_file and draft_file.filename:
            if not draft_file.filename.lower().endswith(".pdf"):
                error = "PDF 파일만 업로드할 수 있습니다."
            else:
                pdf_bytes = draft_file.read()

        if not error:
            db = DB()
            try:
                api_key = decrypt_secret(db.get_api_key_encrypted(session["user_id"]))
            finally:
                db.close()

            try:
                review = review_cover_letter(api_key, text=draft_text or None, pdf_bytes=pdf_bytes)
            except CoverLetterError as e:
                error = str(e)

    return render_template(
        "cover_letter_review.html",
        username=session.get("username"),
        review=review,
        draft_text=draft_text,
        error=error,
    )


@app.route("/company-analysis", methods=["GET", "POST"])
@login_required
def company_analysis_page():
    company = request.values.get("company", "").strip()

    error = None
    warning = None
    analysis_text = None
    sections = {}
    updated_at = None

    db = DB()
    try:
        companies = db.get_companies(session["user_id"])

        if request.method == "POST":
            if not company:
                error = "조사할 회사명을 입력하거나 목록에서 선택해주세요."
            else:
                api_key = decrypt_secret(db.get_api_key_encrypted(session["user_id"]))
                try:
                    analysis_text, sections, interaction_id, used_search = analyze_company_research(company, api_key)
                except CompanyResearchError as e:
                    error = str(e)
                else:
                    if not used_search:
                        warning = "웹 검색 무료 한도 초과로 검색 없이 생성된 분석입니다. 최신 뉴스/매출 등은 부정확할 수 있어요."
                    db.save_company_analysis(session["user_id"], company, analysis_text, interaction_id)

        if not error and company and analysis_text is None:
            saved = db.get_company_analysis(session["user_id"], company)
            if saved:
                analysis_text = saved["analysis"]
                sections = parse_sections(analysis_text or "")
                updated_at = saved["updated_at"]
    finally:
        db.close()

    return render_template(
        "company_analysis.html",
        username=session.get("username"),
        companies=companies,
        selected_company=company,
        analysis_text=analysis_text,
        sections=sections,
        section_order=SECTION_ORDER,
        updated_at=updated_at,
        error=error,
        warning=warning,
    )


@app.route("/settings/api-key", methods=["GET", "POST"])
@login_required
def api_key_settings():
    db = DB()
    try:
        if request.method == "POST":
            action = request.form.get("action")
            if action == "clear":
                db.set_api_key_encrypted(session["user_id"], None)
                flash("등록된 API 키를 삭제했습니다.")
            else:
                new_key = (request.form.get("api_key") or "").strip()
                if not new_key:
                    flash("API 키를 입력해주세요.")
                else:
                    db.set_api_key_encrypted(session["user_id"], encrypt_secret(new_key))
                    flash("API 키를 저장했습니다.")
            return redirect(url_for("api_key_settings"))

        api_key_enc = db.get_api_key_encrypted(session["user_id"])
    finally:
        db.close()

    return render_template(
        "api_key_settings.html",
        username=session.get("username"),
        has_api_key=bool(api_key_enc),
    )


if __name__ == "__main__":
    # 크롤링 예약 실행은 이제 worker.py(GitHub Actions)가 담당하므로
    # 여기서는 로컬 개발용으로 Flask 서버만 띄운다.
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        threaded=True,
    )
