"""
Mock Directory API Server.
Runs on port 8080 in docker-compose for local dev parity.
Responds to GET /api/faculty/{faculty_id} with realistic data.
"""
import random
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Directory API", version="1.0.0")

# Realistic mock faculty data
_FACULTY_DB = {
    "dr.smith": {
        "faculty_name": "Dr. John Smith",
        "faculty_email": "dr.smith@srmap.edu.in",
        "department_code": "CSE",
        "faculty_status": "active",
    },
    "prof.rao": {
        "faculty_name": "Prof. Venkata Rao",
        "faculty_email": "prof.rao@srmap.edu.in",
        "department_code": "ECE",
        "faculty_status": "active",
    },
    "dr.mehta": {
        "faculty_name": "Dr. Priya Mehta",
        "faculty_email": "dr.mehta@srmap.edu.in",
        "department_code": "MECH",
        "faculty_status": "active",
    },
    "dr.inactive": {
        "faculty_name": "Dr. Retired Person",
        "faculty_email": "dr.inactive@srmap.edu.in",
        "department_code": "CIVIL",
        "faculty_status": "inactive",
    },
    "papers": {
        "faculty_name": "Papers Submission Bot",
        "faculty_email": "papers@srmap.edu.in",
        "department_code": "ADMIN",
        "faculty_status": "active",
    },
}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-directory"}


@app.get("/api/faculty/{faculty_id}")
async def get_faculty(faculty_id: str):
    """
    Returns faculty data for the given faculty_id.
    - Known faculty_ids → realistic data
    - 'inactive_*' prefix → inactive status
    - Unknown → 404
    """
    faculty_id_lower = faculty_id.lower()

    if faculty_id_lower in _FACULTY_DB:
        data = _FACULTY_DB[faculty_id_lower]
        return JSONResponse(content=data, status_code=200)

    # Simulate inactive if id contains 'inactive'
    if "inactive" in faculty_id_lower:
        return JSONResponse(content={
            "faculty_name": f"Inactive User ({faculty_id})",
            "faculty_email": f"{faculty_id}@srmap.edu.in",
            "department_code": "UNKNOWN",
            "faculty_status": "inactive",
        }, status_code=200)

    # Unknown faculty
    return JSONResponse(
        content={"detail": f"Faculty '{faculty_id}' not found"},
        status_code=404,
    )
