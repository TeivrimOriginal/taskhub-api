"""Middleware: code that runs around every request.

The order below is also the order of execution. The last one added is the first one to
run, because Starlette wraps middleware like a stack of plates.

    RequestContextMiddleware   adds a request id, writes one log line per request
      RateLimitMiddleware      counts requests per client, answers 429
        MetricsMiddleware      counts requests and measures how long they take
          GZipMiddleware       makes big answers smaller
            CORSMiddleware     tells the browser which front-ends may call us
              router          the endpoint itself
"""

from app.middleware.logging import RequestContextMiddleware
from app.middleware.metrics import MetricsMiddleware
from app.middleware.rate_limit import RateLimitMiddleware

__all__ = ["MetricsMiddleware", "RateLimitMiddleware", "RequestContextMiddleware"]
