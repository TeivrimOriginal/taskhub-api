"""Versioned API routers.

Health probes are deliberately *not* part of this router: they are mounted at the root
(`/health/...`) by `app.main`, because orchestrators and load balancers must reach them
without knowing the API version.
"""

from fastapi import APIRouter

from app.api.v1.routers import auth, comments, projects, stats, tags, tasks, users

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(comments.router, tags=["comments"])
api_router.include_router(tags.router, prefix="/tags", tags=["tags"])
api_router.include_router(stats.router, prefix="/stats", tags=["stats"])

__all__ = ["api_router"]
