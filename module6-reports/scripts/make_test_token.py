#!/usr/bin/env python3
"""
Module 6 – Local Test Token Generator

Generates an UNSIGNED JWT with the claims Module 6 expects from Module 1,
for local testing without running the real Auth service.

Usage:
    python3 scripts/make_test_token.py
    python3 scripts/make_test_token.py --role hod --dept CSE
    python3 scripts/make_test_token.py --role faculty --dept ECE --faculty-id <uuid>

Requires SKIP_JWT_VALIDATION=true in your .env.

Role/report-type authorization reminder (see app/auth.py):
  - NAAC_CRITERIA_III requires role in {coordinator, hod, admin}
  - FACULTY_PROFILE requires role in {coordinator, hod, admin}, OR
    role=faculty generating their OWN profile (faculty_id must match)
"""

from __future__ import annotations

import argparse
import uuid

import jwt


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a test JWT for Module 6 local testing")
    parser.add_argument("--role", default="hod", choices=["faculty", "coordinator", "hod", "admin", "system_worker"])
    parser.add_argument("--dept", default="CSE", help="Department code, e.g. CSE, ECE, MECH")
    parser.add_argument("--user-id", default=None, help="UUID for sub claim (random if omitted)")
    parser.add_argument("--faculty-id", default=None, help="UUID for faculty_id claim (only relevant if role=faculty)")
    args = parser.parse_args()

    claims = {
        "sub": args.user_id or str(uuid.uuid4()),
        "department_code": args.dept,
        "role": args.role,
    }
    if args.role == "faculty":
        claims["faculty_id"] = args.faculty_id or str(uuid.uuid4())

    token = jwt.encode(claims, key="local-dev-only-not-secure", algorithm="HS256")

    print("\nGenerated test JWT (claims below):")
    for k, v in claims.items():
        print(f"  {k}: {v}")
    print("\nToken:\n")
    print(token)
    print("\nUse it like this:\n")
    print(f'curl -H "Authorization: Bearer {token}" http://localhost:8006/api/v1/reports/<report_id>')
    print()


if __name__ == "__main__":
    main()
