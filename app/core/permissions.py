"""All access rules live here. One file, one answer per question.

Why this module exists
----------------------
The same question — "may this user do this to this row?" — used to be answered in five
different places, and the answers had drifted apart:

    project service : owner, superuser, or role == admin
    task service    : owner, superuser, or role in {admin, moderator}
    user service    : self, superuser, or role is admin
    comment service : the author, or staff
    comment service : staff, the assignee, or the project owner

Each one looked reasonable on its own. Together they meant that a moderator could edit a
project but not a task inside it, which is not a rule anybody chose. Now the rules are
declared once, here, and every service asks this module.

How to use it
-------------
Two kinds of function live here, and the difference matters:

* ``can_*`` returns ``True`` or ``False``. Use it to *hide* something — a row in a list,
  a field in a response.
* ``assert_*`` raises ``PermissionDeniedError`` with a message a user can act on. Use it to
  *block* a write.

Never write the check inline in a service again. A new rule belongs here, so the answer
stays the same in all five callers.
"""

from __future__ import annotations

import uuid

from app.core.constants import Role
from app.core.errors import PermissionDeniedError
from app.models.comment import Comment
from app.models.project import Project
from app.models.task import Task
from app.models.user import User


# --------------------------------------------------------------------------- roles
def is_staff(user: User) -> bool:
    """True for moderators, admins and superusers.

    The *definition* of "staff" lives on the model (`User.is_staff`), because it is a fact
    about the row. The rules about what staff may *do* live here. Callers should use this
    function, so that "who is staff" and "what may staff do" are answered in one place.
    """
    return user.is_staff


def has_at_least(user: User, role: Role) -> bool:
    """True when the user has this role or a stronger one."""
    return user.role.level >= role.level


def listing_owner_id(user: User) -> uuid.UUID | None:
    """The owner filter for a list query.

    Staff see every row, so they get ``None`` (no filter). A normal user gets their own
    id, so they only ever see their own rows. Endpoints use this instead of writing
    ``owner_id=None if user.is_staff else user.id`` again.
    """
    return None if is_staff(user) else user.id


# ----------------------------------------------------------------------- projects
def can_read_project(user: User, project: Project) -> bool:
    """Staff may read any project. Everyone else needs to own it."""
    return is_staff(user) or project.owner_id == user.id


def assert_read_project(user: User, project: Project) -> None:
    """Raise unless the user may see this project."""
    if not can_read_project(user, project):
        raise PermissionDeniedError("You may only read your own projects")


def can_write_project(user: User, project: Project) -> bool:
    """Only the owner or an admin may change or delete a project.

    Moderators are deliberately excluded: they moderate content, they do not take over
    other people's projects.
    """
    return project.owner_id == user.id or has_at_least(user, Role.ADMIN)


def assert_write_project(user: User, project: Project) -> None:
    """Raise unless the user may change the project."""
    if not can_write_project(user, project):
        raise PermissionDeniedError("Only the project owner or an admin may change it")


# --------------------------------------------------------------------------- tasks
def can_write_task(user: User, project: Project, task: Task | None = None) -> bool:
    """Who may change a task.

    The project owner may always. Staff may too, so support can fix a stuck task. The
    assignee may change *their own* task but may not delete it — that is the difference
    between ``task`` being given and being ``None``.
    """
    if is_staff(user) or (project is not None and project.owner_id == user.id):
        return True
    return task is not None and task.assignee_id == user.id


def assert_write_task(user: User, project: Project, task: Task | None = None) -> None:
    """Raise unless the user may change the task."""
    if not can_write_task(user, project, task):
        raise PermissionDeniedError("You may only change tasks in your own projects")


def can_read_task(user: User, project: Project | None, task: Task | None = None) -> bool:
    """Who may see a task: the project owner, staff, or the assignee.

    A missing project (``None``) means the parent row is gone, so the rule narrows to
    staff and the assignee — ownership cannot be established for a project that is not
    there.
    """
    if is_staff(user):
        return True
    if project is not None and project.owner_id == user.id:
        return True
    return task is not None and task.assignee_id == user.id


def assert_read_task(user: User, project: Project | None, task: Task | None = None) -> None:
    """Raise unless the user may see the task."""
    if not can_read_task(user, project, task):
        raise PermissionDeniedError("You may only read tasks in your own projects")


# ----------------------------------------------------------------------- comments
def can_edit_comment(user: User, comment: Comment) -> bool:
    """The author of a comment, or staff."""
    return comment.author_id == user.id or is_staff(user)


def assert_edit_comment(user: User, comment: Comment) -> None:
    """Raise unless the user may change the comment."""
    if not can_edit_comment(user, comment):
        raise PermissionDeniedError("Only the author may edit a comment")


def assert_delete_comment(user: User, comment: Comment) -> None:
    """Raise unless the user may delete the comment."""
    if not can_edit_comment(user, comment):
        raise PermissionDeniedError("Only the author may delete a comment")


# --------------------------------------------------------------------------- users
def can_manage_user(actor: User, target: User) -> bool:
    """A user manages their own account; an admin manages anyone's."""
    return actor.id == target.id or has_at_least(actor, Role.ADMIN)


def assert_manage_user(actor: User, target: User) -> None:
    """Raise unless the actor may manage the target account."""
    if not can_manage_user(actor, target):
        raise PermissionDeniedError("You may only manage your own account")


def assert_read_user(actor: User, target: User) -> None:
    """Raise unless the actor may read the target profile.

    The same rule as `assert_manage_user`, different words: someone asking to *see* a
    profile should be told they may only see their own, not that they may only manage it.
    """
    if not can_manage_user(actor, target):
        raise PermissionDeniedError("You may only read your own profile")


def can_change_privileged_fields(actor: User) -> bool:
    """Only an admin may set ``is_active`` or ``role`` on somebody else."""
    return has_at_least(actor, Role.ADMIN)
