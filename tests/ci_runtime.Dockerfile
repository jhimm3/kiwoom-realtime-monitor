FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md requirements.lock.txt ./
COPY src ./src

# Install only the server-side packages exercised by offline integration gates.
# The shared lock constrains both direct and transitive versions on Linux.
RUN pip install --no-cache-dir --no-deps . \
    && pip install --no-cache-dir --constraint requirements.lock.txt \
        'fastapi>=0.116,<1' 'uvicorn[standard]>=0.35,<1' \
        'psycopg[binary]>=3.2,<4' 'cryptography>=46,<47' 'websockets==16.1.1'

ENV PYTHONPATH=/app/src
RUN python -c 'from kiwoom_monitor.central_server.database import PostgresQueryStore; print(PostgresQueryStore.__name__)'
