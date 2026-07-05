FROM python:3.11-slim

RUN python --version

WORKDIR /app

# -- WeasyPrint system dependencies -----------------------------------------
# WeasyPrint is a PURE PYTHON PDF renderer, but it links against real
# native libraries for text shaping/layout (Pango) -- these are NOT
# installable via pip, they must come from apt.
#
# IMPORTANT: this is NOT the older/larger dependency list you'll find in
# a lot of WeasyPrint Docker tutorials online (libcairo2,
# libgdk-pixbuf2.0-0, libffi-dev, etc.) -- that list was for WeasyPrint
# versions before it dropped its pycairo/PyGObject bindings. The list
# below is copied verbatim from the OFFICIAL WeasyPrint 69.0 docs
# (https://doc.courtbouillon.org/weasyprint/stable/first_steps.html) for
# installing via wheels, and verified here by checking `apt-cache
# depends` confirms libcairo2/libgdk-pixbuf2.0-0 are NOT even transitive
# dependencies of these packages on Debian Bookworm -- they're genuinely
# not needed for weasyprint==69.0.
#
# fonts-liberation + fonts-dejavu-core are added on top so rendered PDFs
# have real glyphs instead of missing-character boxes -- WeasyPrint's
# own minimal dependency list assumes you already have SOME fonts on the
# system, which a bare python:3.11-slim image does not.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    fonts-liberation \
    fonts-dejavu-core \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# App code
COPY app/ ./app/
COPY scripts/ ./scripts/

# Non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8006/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8006"]
