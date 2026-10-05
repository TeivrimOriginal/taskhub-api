"""Access rules. Pure logic, so these tests need no database.

`app.core.permissions` is the single answer to "may this user do this?", which means
these tests are not a nice-to-have: they are the regression net for every endpoint that
asks the question.

The objects below are real ORM instances built in memory. No session is involved, so
nothing touches PostgreSQL, and the rules are checked against the actual model types
instead of a hand-written stand-in that could drift from them.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.constants import Role
from app.core.errors import PermissionDeniedError
from app.core.permissions import (
    assert_delete_comment,
    assert_edit_comment,
    assert_manage_user,
    assert_read_project,
    assert_write_project,
    assert_write_task,
    can_change_privileged_fields,
    can_read_task,
    can_write_task,
    has_at_least,
    is_staff,
    listing_owner_id,
)
from app.models.comment import Comment
from app.models.project import Project
from app.models.task import Task
from app.models.user import User

pytestmark = pytest.mark.unit

OWNER_ID = uuid.uuid4()
OTHER_ID = uuid.uuid4()


def make_user(
    role: Role = Role.USER,
    *,
    is_superuser: bool = False,
    user_id: uuid.UUID | None = None,
) -> User:
    """A user row in memory. Never saved, only used to ask a question about."""
    return User(
        id=user_id or uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@taskhub.test",
        username=uuid.uuid4().hex[:12],
        hashed_password="not-a-real-hash",
        role=role,
        is_superuser=is_superuser,
        is_active=True,
    )


def make_project(owner_id: uuid.UUID) -> Project:
    """A project row in memory, owned by `owner_id`."""
    return Project(id=uuid.uuid4(), owner_id=owner_id, name="Test project")


def make_task(assignee_id: uuid.UUID | None = None) -> Task:
    """A task row in memory."""
    return Task(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        title="Test task",
        assignee_id=assignee_id,
    )


def make_comment(author_id: uuid.UUID) -> Comment:
    """A comment row in memory."""
    return Comment(
        id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        author_id=author_id,
        body="Test comment",
    )


owner = make_user(user_id=OWNER_ID)
stranger = make_user(user_id=OTHER_ID)
moderator = make_user(Role.MODERATOR)
admin = make_user(Role.ADMIN)
superuser = make_user(is_superuser=True)

project_of_owner = make_project(OWNER_ID)


class TestRoleHelpers:
    @pytest.mark.parametrize("user", [moderator, admin, superuser])
    def test_staff_means_moderator_admin_or_superuser(self, user: User) -> None:
        assert is_staff(user) is True

    @pytest.mark.parametrize("user", [owner, stranger])
    def test_a_plain_user_is_not_staff(self, user: User) -> None:
        assert is_staff(user) is False

    def test_roles_are_compared_by_strength(self) -> None:
        assert has_at_least(admin, Role.USER) is True
        assert has_at_least(admin, Role.ADMIN) is True
        assert has_at_least(owner, Role.ADMIN) is False
        assert has_at_least(moderator, Role.ADMIN) is False

    def test_the_superuser_flag_is_checked_next_to_the_role(self) -> None:
        assert has_at_least(superuser, Role.ADMIN) is False, "the role field is the role"
        assert is_staff(superuser) is True, "the superuser flag is checked separately"

    def test_staff_get_no_owner_filter(self) -> None:
        assert listing_owner_id(moderator) is None
        assert listing_owner_id(admin) is None

    def test_a_normal_user_is_filtered_to_their_own_rows(self) -> None:
        assert listing_owner_id(owner) == OWNER_ID

    def test_only_an_admin_may_change_privileged_fields(self) -> None:
        assert can_change_privileged_fields(admin) is True
        assert can_change_privileged_fields(moderator) is False
        assert can_change_privileged_fields(owner) is False


class TestProjectRules:
    def test_the_owner_may_read_their_project(self) -> None:
        assert_read_project(owner, project_of_owner)  # does not raise

    @pytest.mark.parametrize("staff", [moderator, admin, superuser])
    def test_staff_may_read_any_project(self, staff: User) -> None:
        assert_read_project(staff, project_of_owner)

    def test_a_stranger_may_not_read_a_project(self) -> None:
        with pytest.raises(PermissionDeniedError):
            assert_read_project(stranger, project_of_owner)

    def test_only_the_owner_or_an_admin_may_write(self) -> None:
        assert_write_project(owner, project_of_owner)
        assert_write_project(admin, project_of_owner)

    def test_a_moderator_may_not_take_over_a_project(self) -> None:
        # Moderators moderate content; they do not edit other people's projects.
        with pytest.raises(PermissionDeniedError):
            assert_write_project(moderator, project_of_owner)

    def test_a_stranger_may_not_write(self) -> None:
        with pytest.raises(PermissionDeniedError):
            assert_write_project(stranger, project_of_owner)

    def test_the_error_message_names_the_rule(self) -> None:
        with pytest.raises(PermissionDeniedError, match="owner or an admin"):
            assert_write_project(stranger, project_of_owner)


class TestTaskRules:
    def test_the_project_owner_may_write_any_task(self) -> None:
        assert_write_task(owner, project_of_owner)

    @pytest.mark.parametrize("staff", [moderator, admin, superuser])
    def test_staff_may_write_any_task(self, staff: User) -> None:
        assert_write_task(staff, project_of_owner)

    def test_a_stranger_may_not_write(self) -> None:
        with pytest.raises(PermissionDeniedError):
            assert_write_task(stranger, project_of_owner)

    def test_the_assignee_may_change_their_own_task(self) -> None:
        mine = make_task(assignee_id=OTHER_ID)
        assert can_write_task(stranger, project_of_owner, mine) is True

    def test_the_assignee_may_not_delete_by_sending_no_task(self) -> None:
        """No task means "the caller did not present one", which is the delete path."""
        assert can_write_task(stranger, project_of_owner, None) is False

    def test_someone_elses_task_is_not_their_task(self) -> None:
        theirs = make_task(assignee_id=OWNER_ID)
        assert can_write_task(stranger, project_of_owner, theirs) is False

    def test_reading_a_task_needs_ownership_staff_or_assignment(self) -> None:
        mine = make_task(assignee_id=OTHER_ID)
        theirs = make_task(assignee_id=OWNER_ID)

        assert can_read_task(owner, project_of_owner, theirs) is True
        assert can_read_task(moderator, project_of_owner, theirs) is True
        assert can_read_task(stranger, project_of_owner, mine) is True
        assert can_read_task(stranger, project_of_owner, theirs) is False

    def test_a_missing_project_narrows_the_rule_to_staff_and_assignee(self) -> None:
        """Without the parent row, ownership cannot be established.

        So a task that belongs to somebody else is out of reach, and only staff or the
        assignee itself still get in.
        """
        assert can_read_task(owner, None, make_task(assignee_id=OTHER_ID)) is False
        assert can_read_task(admin, None, None) is True
        assert can_read_task(stranger, None, make_task(assignee_id=OTHER_ID)) is True


class TestCommentRules:
    def test_the_author_may_edit_and_delete(self) -> None:
        comment = make_comment(author_id=OTHER_ID)
        assert_edit_comment(stranger, comment)
        assert_delete_comment(stranger, comment)

    @pytest.mark.parametrize("staff", [moderator, admin, superuser])
    def test_staff_may_edit_any_comment(self, staff: User) -> None:
        assert_edit_comment(staff, make_comment(author_id=OWNER_ID))

    def test_someone_elses_comment_is_off_limits(self) -> None:
        comment = make_comment(author_id=OWNER_ID)
        with pytest.raises(PermissionDeniedError, match="author"):
            assert_edit_comment(stranger, comment)
        with pytest.raises(PermissionDeniedError, match="author"):
            assert_delete_comment(stranger, comment)


class TestUserRules:
    def test_a_user_manages_their_own_account(self) -> None:
        assert_manage_user(owner, owner)

    def test_an_admin_manages_any_account(self) -> None:
        assert_manage_user(admin, owner)

    def test_a_stranger_manages_nothing(self) -> None:
        with pytest.raises(PermissionDeniedError, match="own account"):
            assert_manage_user(stranger, owner)

    def test_a_moderator_is_not_an_admin(self) -> None:
        with pytest.raises(PermissionDeniedError):
            assert_manage_user(moderator, owner)


class TestTheModuleIsTheOnlyPlaceWithTheRules:
    """A guard against the duplication coming back.

    If a service starts answering an access question on its own, this fails at test time
    instead of being discovered in review — which is the point of centralising.
    """

    def test_no_service_raises_permission_denied_itself(self) -> None:
        services = Path(__file__).resolve().parents[2] / "app" / "services"
        offenders = [
            path.name
            for path in services.glob("*.py")
            if "raise PermissionDeniedError" in path.read_text(encoding="utf-8")
        ]
        assert not offenders, f"these services decide access on their own: {offenders}"

    def test_every_service_imports_the_central_rules(self) -> None:
        services = Path(__file__).resolve().parents[2] / "app" / "services"
        for path in services.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "PermissionDeniedError" in text:
                assert "from app.core.permissions import" in text, (
                    f"{path.name} talks about PermissionDeniedError but does not import "
                    "the central rules"
                )

    def test_the_module_explains_itself(self) -> None:
        """A file without a docstring would give the next reader nothing."""
        from app.core import permissions

        assert len(permissions.__doc__ or "") > 200
