# Trading Journal AI on a home server (see "Running on a home server" in the README).
# Stage 1 builds the frontend; stage 2 serves it and the API on one port (backend/server.py).
FROM node:24-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
# Empty API URL: the built app calls the API on the address it was loaded from.
RUN REACT_APP_API_URL= npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATABASE_PATH=/app/data/trading_journal.db UPLOAD_DIR=/app/data/uploads \
    FRONTEND_BUILD=/app/frontend/build PORT=8771
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend ./
COPY VERSION /app/VERSION
COPY --from=frontend /src/build /app/frontend/build
RUN rm -rf tests && mkdir -p /app/data && chown -R 99:100 /app

# Unraid's nobody:users; override with `user:` in compose for other hosts.
USER 99:100
EXPOSE 8771
CMD ["sh", "-c", "exec python -m uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
