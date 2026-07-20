from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)


def rate_limit_handler(request, exc):
    return JSONResponse(
        status_code=429,
        content={"error": {"reason": "rateLimited",
                           "message": f"Rate limit exceeded ({exc.detail}). Slow down."}},
    )
