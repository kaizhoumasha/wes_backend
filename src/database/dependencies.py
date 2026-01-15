from typing import Annotated

from fastapi import Depends

from .db import AsyncSession, get_db
from .redis_client import get_redis
from redis.asyncio import Redis

AsyncSessionDep = Annotated[AsyncSession, Depends(get_db)]

RedisDep = Annotated[Redis, Depends(get_redis)]