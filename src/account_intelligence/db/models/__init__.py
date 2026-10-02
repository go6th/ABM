"""ORM models for the data layer.

One module per table group under src/account_intelligence/db/models/. Alembic
env.py imports this package so autogenerate sees every table.
"""

from account_intelligence.db.models.account import Account
from account_intelligence.db.models.tenant import Tenant

__all__ = ["Account", "Tenant"]
