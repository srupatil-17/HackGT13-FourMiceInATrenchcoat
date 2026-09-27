import os
import json
import sqlite3
from datetime import datetime, timedelta

from flask import Flask, render_template, request, jsonify
from openai import OpenAI
from dotenv import load_dotenv


# ---------------------------------------------------------
# App setup
# ---------------------------------------------------------

load_dotenv()

app = Flask(__name__)

client = OpenAI(
    api_key=os.getenv("XAI_API_KEY"),
    base_url="https://api.x.ai/v1"
)

DB = "incidents.db"


# ---------------------------------------------------------
# AI prompt
# ---------------------------------------------------------

SYSTEM_PROMPT = """You are a workplace hazard intake assistant.

A worker has anonymously reported a workplace hazard, unsafe condition, or near-miss.

Your job is to convert the report into STRICT JSON using exactly these fields:

{
  "summary": "one short neutral sentence with no names or identifying information",
  "hazard_category": "one of: slip_trip_fall, equipment_malfunction, electrical, chemical_exposure, fire_hazard, ergonomic_strain, blocked_exit, ppe_missing, vehicle_forklift, structural, other",
  "location": "short normalized location phrase such as 'loading dock', 'east stairwell', 'warehouse exit', 'break room', or 'parking lot'",
  "severity": "low | medium | high | critical",
  "description": "1-2 sentences describing the reported hazard in plain language"
}

CLASSIFICATION RULES:

1. slip_trip_fall
Use for wet floors, spills, loose cords, uneven surfaces, cluttered walkways, or anything likely to cause slipping, tripping, or falling.

2. equipment_malfunction
Use for damaged, broken, malfunctioning, jammed, overheating, or unsafe machinery or equipment.

3. electrical
Use for exposed wires, electrical sparks, damaged outlets, damaged cords, electrical panels, shock risk, or unsafe electrical equipment.

4. chemical_exposure
Use for chemical spills, fumes, leaks, unknown chemical odors, hazardous substances, or possible chemical contact.

5. fire_hazard
Use for smoke, flames, overheating that may cause fire, combustible material risks, or other fire-related hazards.

6. ergonomic_strain
Use for repetitive motion, heavy lifting, poor workstation setup, awkward posture, or strain-related hazards.

7. blocked_exit
Use when an emergency exit, fire exit, evacuation route, doorway, or escape path is blocked or obstructed.

8. ppe_missing
Use when required safety equipment such as gloves, goggles, helmets, masks, hearing protection, or protective clothing is missing or not being used.

9. vehicle_forklift
Use for forklifts, trucks, carts, workplace vehicles, collisions, near-collisions, unsafe driving, or pedestrian-vehicle hazards.

10. structural
Use for damaged stairs, ceilings, walls, floors, railings, roofs, supports, cracks, collapse risk, or other building-structure hazards.

11. other
Use only when none of the categories above reasonably apply.

SEVERITY RULES:

low:
Minor hazard with limited immediate risk.

medium:
Hazard that could reasonably cause injury if not corrected.

high:
Hazard with a substantial risk of serious injury or requiring prompt attention.

critical:
Immediate danger involving possible death, severe injury, fire, explosion, major chemical exposure, electrocution, collapse, or another life-threatening situation.

LOCATION RULES:

- Normalize similar descriptions into short consistent labels.
- Example: "by the loading area behind the warehouse" may become "loading dock".
- Example: "stairs on the east side" may become "east stairwell".
- Do not invent a precise location that was not reasonably stated.
- If no useful location is provided, use "unknown location".

PRIVACY RULES:

- Do not include people's names in the summary or description.
- Do not infer identities.
- Keep wording neutral and factual.
- Do not exaggerate the report.

OUTPUT RULES:

- Return valid JSON only.
- Do not use markdown.
- Do not add explanations.
- Do not add fields.
- Do not omit fields.
"""


# ---------------------------------------------------------
# Database setup
# ---------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS hazards (
            id INTEGER PRIMARY KEY,
            transcript TEXT,
            summary TEXT,
            hazard_category TEXT,
            location TEXT,
            severity TEXT,
            description TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


# Initialize database even when using:
# flask --app app run
init_db()


# ---------------------------------------------------------
# Home / Voice Intake
# ---------------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


# ---------------------------------------------------------
# Dashboard
# ---------------------------------------------------------

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


# ---------------------------------------------------------
# Process hazard report
# ---------------------------------------------------------

@app.route("/process", methods=["POST"])
def process():

    conn = None

    try:
        # Safely read JSON request
        data = request.get_json(silent=True) or {}

        transcript = data.get("transcript", "").strip()

        if not transcript:
            return jsonify({
                "error": "Please provide a hazard report."
            }), 400

        # -------------------------------------------------
        # Send report to xAI / Grok
        # -------------------------------------------------

        response = client.chat.completions.create(
            model="grok-4-fast",
            max_tokens=400,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": transcript
                }
            ]
        )

        raw = response.choices[0].message.content.strip()

        # Convert AI response to Python dictionary
        hazard = json.loads(raw)

        # -------------------------------------------------
        # Validate required AI fields
        # -------------------------------------------------

        required_fields = [
            "summary",
            "hazard_category",
            "location",
            "severity",
            "description"
        ]

        for field in required_fields:
            if field not in hazard:
                raise ValueError(
                    f"AI response missing required field: {field}"
                )

        valid_categories = {
            "slip_trip_fall",
            "equipment_malfunction",
            "electrical",
            "chemical_exposure",
            "fire_hazard",
            "ergonomic_strain",
            "blocked_exit",
            "ppe_missing",
            "vehicle_forklift",
            "structural",
            "other"
        }

        valid_severities = {
            "low",
            "medium",
            "high",
            "critical"
        }

        # If AI returns an unexpected category,
        # safely classify it as "other"
        if hazard["hazard_category"] not in valid_categories:
            hazard["hazard_category"] = "other"

        # If severity is unexpected, use medium
        if hazard["severity"] not in valid_severities:
            hazard["severity"] = "medium"

        # -------------------------------------------------
        # Save hazard to SQLite
        # -------------------------------------------------

        conn = sqlite3.connect(DB)

        conn.execute(
            """
            INSERT INTO hazards
            (
                transcript,
                summary,
                hazard_category,
                location,
                severity,
                description,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transcript,
                hazard["summary"],
                hazard["hazard_category"],
                hazard["location"],
                hazard["severity"],
                hazard["description"],
                datetime.now().isoformat()
            )
        )

        conn.commit()

        # -------------------------------------------------
        # Emerging pattern detection
        #
        # Same category + same location
        # reported at least twice within 14 days
        # -------------------------------------------------

        cutoff = (
            datetime.now() - timedelta(days=14)
        ).isoformat()

        cur = conn.execute(
            """
            SELECT COUNT(*)
            FROM hazards
            WHERE hazard_category = ?
              AND location = ?
              AND created_at >= ?
            """,
            (
                hazard["hazard_category"],
                hazard["location"],
                cutoff
            )
        )

        pattern_count = cur.fetchone()[0]

        hazard["pattern_count"] = pattern_count
        hazard["is_emerging_pattern"] = pattern_count >= 2

        return jsonify(hazard), 200

    # -----------------------------------------------------
    # AI returned something that was not valid JSON
    # -----------------------------------------------------

    except json.JSONDecodeError:

        return jsonify({
            "error": "The AI returned an invalid response. Please try again."
        }), 502

    # -----------------------------------------------------
    # Any other backend/API/database error
    # -----------------------------------------------------

    except Exception as e:

        # Error appears only in developer terminal,
        # not on the worker-facing page.
        print(f"Error processing report: {e}")

        return jsonify({
            "error": "Unable to process the report right now. Please try again."
        }), 500

    finally:

        if conn is not None:
            conn.close()


# ---------------------------------------------------------
# Dashboard API
# ---------------------------------------------------------

@app.route("/api/hazards")
def api_hazards():

    conn = None

    try:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            """
            SELECT *
            FROM hazards
            ORDER BY created_at DESC
            """
        ).fetchall()

        hazards = [dict(row) for row in rows]

        cutoff = (
            datetime.now() - timedelta(days=14)
        ).isoformat()

        pattern_rows = conn.execute(
            """
            SELECT
                hazard_category,
                location,
                COUNT(*) AS cnt
            FROM hazards
            WHERE created_at >= ?
            GROUP BY hazard_category, location
            HAVING COUNT(*) >= 2
            ORDER BY cnt DESC
            """,
            (cutoff,)
        ).fetchall()

        patterns = [
            {
                "hazard_category": row[0],
                "location": row[1],
                "count": row[2]
            }
            for row in pattern_rows
        ]

        return jsonify({
            "hazards": hazards,
            "patterns": patterns
        })

    except Exception as e:

        print(f"Error loading dashboard data: {e}")

        return jsonify({
            "error": "Unable to load hazard data.",
            "hazards": [],
            "patterns": []
        }), 500

    finally:

        if conn is not None:
            conn.close()


# ---------------------------------------------------------
# Start development server
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)