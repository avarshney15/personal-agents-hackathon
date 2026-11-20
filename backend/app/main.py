"""FastAPI app. Owned by Part 5 (backend). The lead's version only boots."""

from fastapi import FastAPI

app = FastAPI(title="Event Agent")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
