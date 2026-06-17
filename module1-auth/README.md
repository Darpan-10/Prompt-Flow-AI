# 🔐 Prompt Flow AI — Module 1: Authentication & Access Control

Production-ready FastAPI auth service with OAuth 2.0, JWT, RBAC, and PostgreSQL RLS.

**Status:** Production-Ready | **Version:** 1.0.0 | **Compliance:** NAAC, FERPA, GDPR

## Quick Start

```bash
# 1. Generate JWT keys
python scripts/generate_keys.py

# 2. Setup environment
cp .env.example .env

# 3. Initialize database
psql -U promptflow -d promptflow < db/schema.sql

# 4. Install & run
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**Or use Docker:**
```bash
python scripts/generate_keys.py
docker-compose up
```

See `SETUP.md` for detailed Arch Linux setup guide.
