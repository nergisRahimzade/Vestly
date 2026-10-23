from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Identity(Base):
    __tablename__ = "identities"

    handle: Mapped[str] = mapped_column(String(48), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    department: Mapped[str | None] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
