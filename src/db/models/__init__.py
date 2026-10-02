"""Data layer model registry.

One module per table group under src/db/models/. Alembic env.py imports this
package so autogenerate sees every table.
"""

from src.db.models.account import Account

__all__ = ["Account"]
