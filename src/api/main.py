import inngest.fast_api
from fastapi import FastAPI

from src.api.runs import router as runs_router
from src.orchestrator.functions import client, run_research

app = FastAPI(title="Account Intelligence API")
app.include_router(runs_router)
inngest.fast_api.serve(app, client, [run_research])


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
