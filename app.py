from flask import Flask, render_template, request, redirect, url_for
from database import DB, STATUS_CHOICES

app = Flask(__name__)


@app.route("/")
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
def set_status(job_id):
    new_status = request.form.get("new_status", "").strip()

    if new_status not in STATUS_CHOICES:
        return "잘못된 상태 값입니다", 400

    db = DB()
    db.update_status(job_id, new_status)
    db.close()

    return _back_to_index()


@app.route("/jobs/<int:job_id>/memo", methods=["POST"])
def set_memo(job_id):
    memo = request.form.get("memo", "")

    db = DB()
    db.update_memo(job_id, memo)
    db.close()

    return _back_to_index()


if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False
    )
