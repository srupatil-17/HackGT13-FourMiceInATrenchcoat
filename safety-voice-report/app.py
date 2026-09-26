import os
import json
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
client = OpenAI(
    api_key=os.getenv("XAI_API_KEY"),
    base_url="https://api.x.ai/v1"
)

DB = "incidents.db"

SYSTEM_PROMPT = """You are a workplace hazard intake assistant. A worker has
anonymously spoken a hazard or near-miss they noticed. Extract STRICT JSON
with these exact fields:

{
  "summary": "one sentence, neutral, no names",
  "hazard_category": "one of: slip_trip_fall, equipment_malfunction, electrical, chemical_exposure, fire_hazard, ergonomic_strain, blocked_exit, ppe_missing, vehicle_forklift, structural, other",
  "location": "short normalized location phrase, e.g. 'loading dock', 'east stairwell', 'break room' - infer a consistent short label even if worker describes it loosely",
  "severity": "low | medium | high | critical",
  "description": "1-2 sentence plain description of what was reported"
}

Return ONLY valid JSON, nothing else, no markdown fences."""

def init_db():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS hazards (
        id INTEGER PRIMARY KEY,
        transcript TEXT,
        summary TEXT,
        hazard_category TEXT,
        location TEXT,
        severity TEXT,
        description TEXT,
        created_at TEXT
    )""")
    conn.commit()
    conn.close()

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/process", methods=["POST"])
def process():
    transcript = request.json.get("transcript", "")
    if not transcript.strip():
        return jsonify({"error": "empty transcript"}), 400

    response = client.chat.completions.create(
        model="grok-4-fast",
        max_tokens=400,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": transcript}
        ]
    )
    raw = response.choices[0].message.content.strip()
    hazard = json.loads(raw)

    conn = sqlite3.connect(DB)
    conn.execute("""INSERT INTO hazards
        (transcript, summary, hazard_category, location, severity, description, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (transcript, hazard["summary"], hazard["hazard_category"],
         hazard["location"], hazard["severity"], hazard["description"],
         datetime.now().isoformat()))
    conn.commit()

    cutoff = (datetime.now() - timedelta(days=14)).isoformat()
    cur = conn.execute("""SELECT COUNT(*) FROM hazards
        WHERE hazard_category = ? AND location = ? AND created_at >= ?""",
        (hazard["hazard_category"], hazard["location"], cutoff))
    pattern_count = cur.fetchone()[0]
    conn.close()

    hazard["pattern_count"] = pattern_count
    hazard["is_emerging_pattern"] = pattern_count >= 2

    return jsonify(hazard)

@app.route("/api/hazards")
def api_hazards():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM hazards ORDER BY created_at DESC").fetchall()
    conn.close()

    hazards = [dict(row) for row in rows]

    cutoff = (datetime.now() - timedelta(days=14)).isoformat()
    conn = sqlite3.connect(DB)
    pattern_rows = conn.execute("""
        SELECT hazard_category, location, COUNT(*) as cnt
        FROM hazards WHERE created_at >= ?
        GROUP BY hazard_category, location
        HAVING cnt >= 2
        ORDER BY cnt DESC
    """, (cutoff,)).fetchall()
    conn.close()

    patterns = [{"hazard_category": r[0], "location": r[1], "count": r[2]} for r in pattern_rows]

    return jsonify({"hazards": hazards, "patterns": patterns})

if __name__ == "__main__":
    init_db()
    app.run(debug=True)