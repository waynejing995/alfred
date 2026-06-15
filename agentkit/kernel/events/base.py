from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

_BARE = re.compile(r"^[a-z][a-z0-9_]*$")
_DOTTED = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_.]+$")


class Event(BaseModel):
    name: ClassVar[str]
    blockable: ClassVar[bool] = False
    namespace: ClassVar[str] = ""

    model_config = ConfigDict(frozen=True, extra="forbid")

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        name = getattr(cls, "name", None)
        if name is None:
            return
        namespace = getattr(cls, "namespace", "")
        if namespace:
            if not _DOTTED.match(name) or not name.startswith(f"{namespace}."):
                raise ValueError(
                    f"plugin event {cls.__name__}: name {name!r} must be "
                    f"'{namespace}.<suffix>'"
                )
            return
        if not (_BARE.match(name) or _DOTTED.match(name)):
            raise ValueError(f"event {cls.__name__}: invalid name {name!r}")


def serialize(event: Event) -> dict:
    return {"type": event.name, "payload": event.model_dump(mode="json")}

