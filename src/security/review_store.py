"""Review store."""
from sqlalchemy.ext.asyncio import AsyncSession


async def capture(session: AsyncSession, tenant_id: str, run_id: str, prompt_text: str, response_text: str) -> None:
    pass
