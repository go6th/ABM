"""Every ORM model module is imported here, so ``Base.metadata`` is complete for Alembic.

Add one import per new model module. ``tests/app/db/test_migrations.py`` fails if the
migrations and these models drift apart.
"""

from app.db.base import Base
from app.db.models.account import Account
from app.db.models.run import Run

__all__ = ["Account", "Base", "Run"]
