import os
import json
import sqlite3
import csv
import io
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file

PROJECT_NAME = "LLM-Assisted Quality Evaluation of User Stories"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "story_quality.db")

app = Flask(__name__)

INVEST = {
    "Independent": "The story can be developed with minimal dependency on another story.",
    "Negotiable": "The story describes a goal and avoids unnecessary implementation details.",
    "Valuable": "The story provides clear value to a user or the business.",
    "Estimable": "The story contains enough information for the team to estimate the work.",
    "Small": "The story is focused enough to fit a reasonable iteration or sprint.",
    "Testable": "The story has observable conditions that can verify completion."
}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story TEXT NOT NULL,
            scores TEXT NOT NULL,
            total INTEGER NOT NULL,
            percentage REAL NOT NULL,
            feedback TEXT NOT NULL,
            improved TEXT NOT NULL,
            acceptance TEXT NOT NULL,
            intelligence TEXT NOT NULL DEFAULT '{}',
            model TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
        cols = [r['name'] for r in conn.execute('PRAGMA table_info(evaluations)').fetchall()]
        if 'intelligence' not in cols:
            conn.execute("ALTER TABLE evaluations ADD COLUMN intelligence TEXT NOT NULL DEFAULT '{}'")
        conn.commit()

def improve_story(story):
    s = story.strip()
    low = s.lower()
    if not s:
        return ""
    if " as a " not in low:
        s = "As a user, " + s[:1].lower() + s[1:]
    if " so that " not in s.lower():
        s = s.rstrip(".") + " so that I can achieve the intended outcome."
    return s

def make_acceptance(story):
    return [
        "Given the user is eligible, when the requested action is performed, then the expected result is produced.",
        "Given invalid or incomplete input, when the action is submitted, then a clear validation message is displayed.",
        "The system should provide a clear success state and preserve the intended user outcome."
    ]

def heuristic_evaluate(story):
    s = story.strip()
    low = s.lower()

    scores = {
        "Independent": 1 if any(x in low for x in ["depends on", "after story", "must first"]) else 2,
        "Negotiable": 1 if any(x in low for x in ["must use", "using react", "using mongodb", "exactly"]) else 2,
        "Valuable": 2 if (" so that " in low or " so i can " in low or "benefit" in low) else 1,
        "Estimable": 2 if 8 <= len(s.split()) <= 60 else 1,
        "Small": 1 if any(x in low for x in ["everything", "complete system", "all features", "and also"]) else 2,
        "Testable": 2 if any(x in low for x in ["acceptance", "when", "then", "should", "able to"]) else 1
    }

    feedback = []
    if " as a " not in low:
        feedback.append("Add a clear user role using: As a <role>...")
    if " i want " not in low:
        feedback.append("State the desired capability using: I want to...")
    if " so that " not in low:
        feedback.append("Explain the user or business value using: so that...")
    if scores["Testable"] < 2:
        feedback.append("Add measurable acceptance criteria or Given-When-Then scenarios.")
    if scores["Small"] < 2:
        feedback.append("Split the story into smaller, independently deliverable stories.")
    if scores["Negotiable"] < 2:
        feedback.append("Avoid unnecessary technology or implementation prescriptions.")

    total = sum(scores.values())
    percentage = round(total / 12 * 100, 1)

    if percentage >= 85:
        level = "Excellent"
    elif percentage >= 70:
        level = "Good"
    elif percentage >= 55:
        level = "Needs Improvement"
    else:
        level = "Poor"

    return {
        "scores": scores,
        "total": total,
        "max": 12,
        "percentage": percentage,
        "level": level,
        "feedback": feedback,
        "improved": improve_story(s),
        "acceptance": make_acceptance(s),
        "model": "Local Quality Engine"
    }

def llm_evaluate(story):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        prompt = f"""
You are an expert Agile requirements analyst.
Evaluate the following user story using INVEST.

Criteria:
{json.dumps(INVEST, indent=2)}

Return ONLY valid JSON:
{{
  "scores": {{
    "Independent": 0,
    "Negotiable": 0,
    "Valuable": 0,
    "Estimable": 0,
    "Small": 0,
    "Testable": 0
  }},
  "feedback": ["actionable feedback"],
  "improved": "improved user story preserving original intent",
  "acceptance": ["three concise, testable acceptance criteria"]
}}

Each score must be an integer from 0 to 2.
Do not invent business requirements that change the original intent.

User story:
{story}
"""
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-6-luna"),
            input=prompt
        )
        data = json.loads(response.output_text)
        data["total"] = sum(int(v) for v in data["scores"].values())
        data["max"] = 12
        data["percentage"] = round(data["total"] / 12 * 100, 1)
        data["level"] = (
            "Excellent" if data["percentage"] >= 85 else
            "Good" if data["percentage"] >= 70 else
            "Needs Improvement" if data["percentage"] >= 55 else "Poor"
        )
        data["model"] = os.getenv("OPENAI_MODEL", "gpt-6-luna")
        return data
    except Exception as exc:
        # When an API key is present, do not silently hide an API/billing/model error.
        # The frontend will show the exact safe error message so the user can fix it.
        msg = str(exc)
        if "insufficient_quota" in msg.lower() or "quota" in msg.lower() or "billing" in msg.lower():
            raise RuntimeError("OpenAI API billing/credits are unavailable for this project. Add API credits, then try again.")
        if "model" in msg.lower() and ("not found" in msg.lower() or "does not exist" in msg.lower() or "permission" in msg.lower()):
            raise RuntimeError("The configured OpenAI model is not available for this API project. Set OPENAI_MODEL to an available model and try again.")
        raise RuntimeError("OpenAI evaluation failed. Check your API key, API credits, and model access, then try again.")


VAGUE_WORDS = ["easy", "fast", "quick", "simple", "user-friendly", "etc", "and so on", "proper", "good", "better", "soon", "somehow", "everything"]


def build_intelligence(story, result):
    """Create an explainable second layer beyond the INVEST score."""
    s = story.strip()
    low = s.lower()
    words = s.split()
    smells = []
    strengths = []

    if not any(x in low for x in [" as a ", " as an "]):
        smells.append({"name": "Missing persona", "severity": "medium", "detail": "The story does not clearly identify who needs the capability."})
    else:
        strengths.append("A user persona is explicitly stated.")
    if " so that " not in low and " so i can " not in low:
        smells.append({"name": "Missing value", "severity": "high", "detail": "The intended user or business outcome is not explicit."})
    else:
        strengths.append("The story communicates an intended outcome/value.")
    vague = [w for w in VAGUE_WORDS if w in low]
    if vague:
        smells.append({"name": "Ambiguous wording", "severity": "high", "detail": "Vague terms detected: " + ", ".join(vague[:5]) + ". Replace them with observable outcomes."})
    implementation = [w for w in ["using react", "using mongodb", "using python", "using java", "using node", "must use", "technology"] if w in low]
    if implementation:
        smells.append({"name": "Implementation leakage", "severity": "medium", "detail": "The story contains implementation constraints that may belong in technical design."})
    if any(x in low for x in ["everything", "complete system", "all features", "entire platform", "and also", "plus payment and"]):
        smells.append({"name": "Scope explosion", "severity": "high", "detail": "Multiple outcomes or a very broad scope may make the story difficult to deliver in one iteration."})
    if any(x in low for x in ["depends on", "after story", "must first", "blocked by"]):
        smells.append({"name": "Dependency risk", "severity": "medium", "detail": "The wording suggests dependency on another story or workflow."})
    if len(words) > 60:
        smells.append({"name": "Overloaded story", "severity": "medium", "detail": "The story is long enough to deserve decomposition or clarification."})
    if not any(x in low for x in ["when", "then", "should", "able to", "can "]):
        smells.append({"name": "Weak test signal", "severity": "medium", "detail": "The story has few observable cues for verifying completion."})
    if " i want " in low:
        strengths.append("The requested capability is explicitly stated.")
    if result.get("scores", {}).get("Testable", 0) == 2:
        strengths.append("The story contains a relatively strong testability signal.")

    high = sum(1 for x in smells if x["severity"] == "high")
    medium = sum(1 for x in smells if x["severity"] == "medium")
    risk = "High" if high >= 2 else ("Medium" if high == 1 or medium >= 2 else "Low")
    ambiguity = min(100, 10 + len(vague) * 15 + (25 if " so that " not in low else 0) + (15 if len(words) > 60 else 0))
    scope = min(100, 15 + (35 if any(x in low for x in ["everything", "complete system", "all features", "entire platform"]) else 0) + max(0, len(words)-45))
    testability = result.get("scores", {}).get("Testable", 0) / 2 * 100
    value = result.get("scores", {}).get("Valuable", 0) / 2 * 100
    readiness = max(0, round(result.get("percentage", 0) - high * 8 - medium * 3, 1))
    readiness_label = "Ready for review" if readiness >= 80 else ("Needs refinement" if readiness >= 55 else "High refinement needed")
    evidence = (2 if (" as a " in low or " as an " in low) else 0) + (2 if (" so that " in low or " so i can " in low) else 0) + min(2, len(words)//8)
    confidence = min(95, 55 + evidence*6 + (10 if result.get("model") != "Local Quality Engine" else 0))

    # Stable, privacy-friendly fingerprint for comparing revisions without storing raw text elsewhere.
    import hashlib
    fingerprint = hashlib.sha256(" ".join(words).lower().encode("utf-8")).hexdigest()[:10].upper()
    return {
        "risk_level": risk,
        "readiness": readiness,
        "readiness_label": readiness_label,
        "fingerprint": fingerprint,
        "smells": smells,
        "strengths": strengths[:6],
        "confidence": confidence,
        "signals": {
            "ambiguity": round(ambiguity, 1),
            "scope_risk": round(scope, 1),
            "testability": round(testability, 1),
            "value_clarity": round(value, 1)
        },
        "human_review": "Use this analysis as decision support; validate business context, dependencies and acceptance criteria with the product team."
    }

def save_evaluation(story, result):
    with get_db() as conn:
        cur = conn.execute("""
        INSERT INTO evaluations
        (story, scores, total, percentage, feedback, improved, acceptance, intelligence, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            story,
            json.dumps(result["scores"]),
            result["total"],
            result["percentage"],
            json.dumps(result["feedback"]),
            result["improved"],
            json.dumps(result["acceptance"]),
            json.dumps(result.get("intelligence", {})),
            result["model"],
            datetime.now().isoformat(timespec="seconds")
        ))
        conn.commit()
        return cur.lastrowid

@app.get("/")
def index():
    return render_template("index.html", project_name=PROJECT_NAME)

@app.post("/api/evaluate")
def evaluate():
    payload = request.get_json(silent=True) or {}
    story = str(payload.get("story", "")).strip()

    if not story:
        return jsonify({"error": "Please enter a user story first."}), 400
    if len(story) > 5000:
        return jsonify({"error": "Maximum story length is 5000 characters."}), 400

    try:
        result = llm_evaluate(story)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502

    # Local engine remains available when no API key is configured.
    result = result or heuristic_evaluate(story)
    result["intelligence"] = build_intelligence(story, result)
    result["id"] = save_evaluation(story, result)
    return jsonify(result)

@app.get("/api/history")
def history():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM evaluations ORDER BY id DESC LIMIT 100"
        ).fetchall()

    return jsonify([
        {
            "id": r["id"],
            "story": r["story"],
            "scores": json.loads(r["scores"]),
            "total": r["total"],
            "percentage": r["percentage"],
            "level": (
                "Excellent" if r["percentage"] >= 85 else
                "Good" if r["percentage"] >= 70 else
                "Needs Improvement" if r["percentage"] >= 55 else "Poor"
            ),
            "feedback": json.loads(r["feedback"]),
            "improved": r["improved"],
            "acceptance": json.loads(r["acceptance"]),
            "intelligence": json.loads(r["intelligence"]) if "intelligence" in r.keys() else {},
            "model": r["model"],
            "created_at": r["created_at"]
        }
        for r in rows
    ])

@app.delete("/api/history/<int:evaluation_id>")
def delete_history(evaluation_id):
    with get_db() as conn:
        conn.execute("DELETE FROM evaluations WHERE id = ?", (evaluation_id,))
        conn.commit()
    return jsonify({"ok": True})

@app.get("/api/stats")
def stats():
    with get_db() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS count,
                   COALESCE(AVG(percentage), 0) AS average,
                   COALESCE(MAX(percentage), 0) AS best
            FROM evaluations
        """).fetchone()
        strong = conn.execute(
            "SELECT COUNT(*) AS count FROM evaluations WHERE percentage >= 80"
        ).fetchone()["count"]

    return jsonify({
        "count": row["count"],
        "average": round(row["average"], 1),
        "best": row["best"],
        "strong": strong
    })

@app.get("/api/export/<int:evaluation_id>")
def export_one(evaluation_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM evaluations WHERE id = ?", (evaluation_id,)
        ).fetchone()

    if not row:
        return "Evaluation not found", 404

    data = {
        "project": PROJECT_NAME,
        "id": row["id"],
        "story": row["story"],
        "scores": json.loads(row["scores"]),
        "total": row["total"],
        "percentage": row["percentage"],
        "feedback": json.loads(row["feedback"]),
        "improved_story": row["improved"],
        "acceptance_criteria": json.loads(row["acceptance"]),
        "quality_intelligence": json.loads(row["intelligence"]) if "intelligence" in row.keys() else {},
        "model": row["model"],
        "created_at": row["created_at"]
    }
    stream = io.BytesIO(json.dumps(data, indent=2).encode())
    return send_file(
        stream,
        as_attachment=True,
        download_name=f"user_story_evaluation_{evaluation_id}.json",
        mimetype="application/json"
    )

@app.get("/api/analytics")
def analytics():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT scores, percentage, created_at FROM evaluations ORDER BY id ASC"
        ).fetchall()
    totals = {k: 0 for k in INVEST}
    count = len(rows)
    trend = []
    for r in rows:
        scores = json.loads(r["scores"])
        for k, v in scores.items():
            totals[k] += int(v)
        trend.append({
            "date": r["created_at"],
            "percentage": r["percentage"]
        })
    averages = {
        k: round((totals[k] / (count * 2) * 100), 1) if count else 0
        for k in totals
    }
    return jsonify({"count": count, "criteria": averages, "trend": trend[-20:]})

@app.post("/api/compare")
def compare_stories():
    payload = request.get_json(silent=True) or {}
    left = str(payload.get("left", "")).strip()
    right = str(payload.get("right", "")).strip()
    if not left or not right:
        return jsonify({"error": "Provide both stories to compare."}), 400
    if len(left) > 5000 or len(right) > 5000:
        return jsonify({"error": "Each story must be 5000 characters or fewer."}), 400
    a = heuristic_evaluate(left); b = heuristic_evaluate(right)
    ia = build_intelligence(left, a); ib = build_intelligence(right, b)
    changes = []
    for k in INVEST:
        delta = int(b["scores"][k]) - int(a["scores"][k])
        if delta:
            changes.append({"criterion": k, "delta": delta, "before": a["scores"][k], "after": b["scores"][k]})
    return jsonify({"before": {"scores": a["scores"], "percentage": a["percentage"], "risk": ia["risk_level"], "readiness": ia["readiness"]}, "after": {"scores": b["scores"], "percentage": b["percentage"], "risk": ib["risk_level"], "readiness": ib["readiness"]}, "delta": round(b["percentage"]-a["percentage"],1), "changes": changes, "note": "Comparison is a structured decision-support view; validate business intent with the product team."})

@app.post("/api/batch")
def batch_evaluate():
    payload = request.get_json(silent=True) or {}
    stories = payload.get("stories") or []
    if not isinstance(stories, list) or not stories:
        return jsonify({"error": "Provide a list of user stories."}), 400
    if len(stories) > 25:
        return jsonify({"error": "Batch limit is 25 stories."}), 400
    results = []
    for raw in stories:
        story = str(raw).strip()
        if not story or len(story) > 5000: continue
        result = heuristic_evaluate(story); result["intelligence"] = build_intelligence(story, result)
        results.append({"story": story, "percentage": result["percentage"], "level": result["level"], "risk": result["intelligence"]["risk_level"], "readiness": result["intelligence"]["readiness"]})
    avg = round(sum(x["percentage"] for x in results)/len(results),1) if results else 0
    return jsonify({"count": len(results), "average": avg, "results": results})


def _extract_capability(story):
    low=story.lower()
    capability=story
    for marker in [" i want ", " i need ", " i can "]:
        if marker in low:
            capability=story[low.find(marker)+len(marker):]
            break
    capability=capability.split(" so that ")[0].strip(" .")
    return capability[:180]

def requirement_studio(story):
    """Deterministic requirements-engineering layer for decomposition, traceability and risk."""
    words=story.split(); low=story.lower()
    signals=[]
    if any(x in low for x in ["everything","complete system","entire platform","all features"]): signals.append("Broad scope")
    if len(words)>35: signals.append("Long narrative")
    if any(x in low for x in ["and", "plus", "also", ","]): signals.append("Multiple clauses")
    capability=_extract_capability(story)
    base=capability if capability else "Deliver the requested capability"
    # Produce small candidate slices without inventing business goals.
    parts=[
        {"title":"Primary capability","story":f"As the intended user, I want {base} so that the original user outcome is preserved.","reason":"Core slice extracted from the original intent."},
        {"title":"Validation slice","story":f"As the intended user, I want the {base.lower()} outcome to be verifiable so that completion can be confirmed.","reason":"Adds an observable verification focus."},
        {"title":"Exception slice","story":f"As the intended user, I want the {base.lower()} flow to handle relevant failure cases so that the intended outcome remains reliable.","reason":"Separates exception handling from the happy path."}
    ]
    if not signals:
        parts=parts[:2]
    trace=[
      {"artifact":"User / persona","evidence":"Explicit persona" if (" as a " in low or " as an " in low) else "Needs confirmation","status":"covered" if (" as a " in low or " as an " in low) else "review"},
      {"artifact":"Capability","evidence":capability or "Not clearly extracted","status":"covered" if capability else "review"},
      {"artifact":"Business value","evidence":"Outcome phrase detected" if " so that " in low else "No explicit outcome phrase","status":"covered" if " so that " in low else "gap"},
      {"artifact":"Verification","evidence":"Acceptance criteria required","status":"review"},
      {"artifact":"Dependencies","evidence":"Potential dependency language detected" if any(x in low for x in ["depends","blocked","after "]) else "No explicit dependency stated","status":"review"}
    ]
    return {"signals":signals,"decomposition":parts,"traceability":trace,"recommendation":"Split broad stories by user outcome, workflow slice or exception path while preserving the original business intent."}

@app.post("/api/studio")
def studio():
    payload=request.get_json(silent=True) or {}
    story=str(payload.get("story","")).strip()
    if not story: return jsonify({"error":"Provide a user story."}),400
    if len(story)>5000: return jsonify({"error":"Story must be 5000 characters or fewer."}),400
    return jsonify(requirement_studio(story))

@app.get("/api/export-all")
def export_all():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM evaluations ORDER BY id DESC"
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "story", "percentage", "total", "quality_level",
        "model", "created_at", "improved_story"
    ])

    for row in rows:
        level = (
            "Excellent" if row["percentage"] >= 85 else
            "Good" if row["percentage"] >= 70 else
            "Needs Improvement" if row["percentage"] >= 55 else "Poor"
        )
        writer.writerow([
            row["id"], row["story"], row["percentage"], row["total"],
            level, row["model"], row["created_at"], row["improved"]
        ])

    stream = io.BytesIO(output.getvalue().encode())
    return send_file(
        stream,
        as_attachment=True,
        download_name="user_story_evaluation_history.csv",
        mimetype="text/csv"
    )

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
