"""Every ORM model module is imported here, so ``Base.metadata`` is complete for Alembic.

Add one import per new model module. ``tests/app/db/test_migrations.py`` fails if the
migrations and these models drift apart.
"""

from app.db.base import Base

__all__ = ["Base"]
