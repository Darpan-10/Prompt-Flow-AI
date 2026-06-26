"""
Module 5 – Locust Performance Test

Verifies the <600ms p95 latency target across all three search modes
under concurrent load.

USAGE (see SETUP.md for full walkthrough):

    # Against local Docker setup
    locust -f tests/performance/locustfile.py --host http://localhost:8005

    # Headless run with a fixed user count/duration (CI-friendly)
    locust -f tests/performance/locustfile.py --host http://localhost:8005 \
        --users 50 --spawn-rate 5 --run-time 2m --headless \
        --html tests/performance/report.html

    # Against a deployed AWS ALB endpoint
    locust -f tests/performance/locustfile.py --host https://search.promptflow.srmap.edu.in

Then open http://localhost:8089 (Locust's web UI) if running interactively,
or check the generated --html report if running --headless.

IMPORTANT: requires a valid test JWT (see scripts/make_test_token.py).
Set TEST_JWT_TOKEN as an environment variable before running, e.g.:

    export TEST_JWT_TOKEN=$(python3 scripts/make_test_token.py --role admin --dept CSE | tail -1)
"""

import os
import random

from locust import HttpUser, between, task

# Sample queries representative of real usage -- mix of common CS/research
# terms likely to exist in a populated PromptFlow database.
SAMPLE_QUERIES = [
    "attention mechanism",
    "deep learning",
    "neural network architecture",
    "transformer model",
    "natural language processing",
    "computer vision",
    "reinforcement learning",
    "graph neural networks",
    "machine learning healthcare",
    "convolutional neural network",
]

TEST_TOKEN = os.environ.get("TEST_JWT_TOKEN", "")

if not TEST_TOKEN:
    print(
        "\n⚠️  WARNING: TEST_JWT_TOKEN environment variable not set.\n"
        "   Requests will fail with 401. Generate one with:\n"
        "   export TEST_JWT_TOKEN=$(python3 scripts/make_test_token.py --role admin --dept CSE | tail -1)\n"
    )


class SearchUser(HttpUser):
    """Simulates a user issuing search queries across all three modes."""

    # Wait 1-3 seconds between requests per simulated user (realistic
    # human search-then-read pacing, not a tight hammering loop).
    wait_time = between(1, 3)

    def on_start(self):
        self.headers = {
            "Authorization": f"Bearer {TEST_TOKEN}",
            "Content-Type": "application/json",
        }

    @task(5)
    def search_hybrid(self):
        """Hybrid mode is the default and most common -- weighted highest."""
        query = random.choice(SAMPLE_QUERIES)
        self.client.post(
            "/api/v1/search",
            json={"query": query, "mode": "hybrid", "limit": 20},
            headers=self.headers,
            name="/api/v1/search [hybrid]",
        )

    @task(3)
    def search_keyword(self):
        query = random.choice(SAMPLE_QUERIES)
        self.client.post(
            "/api/v1/search",
            json={"query": query, "mode": "keyword", "limit": 20},
            headers=self.headers,
            name="/api/v1/search [keyword]",
        )

    @task(2)
    def search_semantic(self):
        query = random.choice(SAMPLE_QUERIES)
        self.client.post(
            "/api/v1/search",
            json={"query": query, "mode": "semantic", "limit": 20},
            headers=self.headers,
            name="/api/v1/search [semantic]",
        )

    @task(2)
    def get_facets(self):
        """Facets should be FAST due to 1-hour cache TTL -- good baseline
        to compare against search latency."""
        self.client.get(
            "/api/v1/search/facets",
            headers=self.headers,
        )

    @task(1)
    def get_suggestions(self):
        prefix = random.choice(["att", "deep", "neu", "trans", "mach"])
        self.client.get(
            f"/api/v1/search/suggestions?prefix={prefix}&type=title",
            headers=self.headers,
        )

    @task(1)
    def search_with_filters(self):
        """Filtered search -- exercises the facet filter SQL paths."""
        query = random.choice(SAMPLE_QUERIES)
        self.client.post(
            "/api/v1/search",
            json={
                "query": query,
                "mode": "hybrid",
                "limit": 20,
                "year": 2024,
                "min_confidence": 0.7,
            },
            headers=self.headers,
            name="/api/v1/search [hybrid+filters]",
        )


# ── Custom failure threshold check (optional, run via --headless + script) ──
#
# After a headless run, Locust prints p95/p99 in its summary output. To
# enforce the <600ms p95 target automatically in CI, parse the generated
# --csv output (locust --csv=results ...) and check the
# results_stats.csv "95%" column programmatically, e.g.:
#
#   import csv
#   with open("results_stats.csv") as f:
#       rows = list(csv.DictReader(f))
#       agg = next(r for r in rows if r["Name"] == "Aggregated")
#       p95 = float(agg["95%"])
#       assert p95 < 600, f"p95 latency {p95}ms exceeds 600ms target"
