"""Outbound e-mail.

Uses `smtplib` in a thread so a slow SMTP server never blocks the event loop, and
fails soft: a broken mail server must not roll back the write that triggered the e-mail.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import Settings, get_settings
from app.core.constants import TokenType
from app.core.logging import get_logger
from app.core.security import create_token

logger = get_logger(__name__)


class EmailService:
    """Renders and sends the transactional e-mails of the application."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ public
    async def send_task_assigned(self, *, to: str, task_title: str, project_name: str) -> bool:
        """Tell someone they were assigned a task."""
        subject = f"You were assigned to «{task_title}»"
        body = (
            f"Hi!\n\n"
            f"You were assigned to «{task_title}» in the project «{project_name}».\n"
            f"Open TaskHub to see the details.\n"
        )
        return await self.send(to=to, subject=subject, body=body)

    async def send_overdue_notice(self, *, to: str, task_title: str, due_date: str) -> bool:
        """Single overdue reminder."""
        subject = f"Overdue: {task_title}"
        body = (
            f"Hi!\n\n"
            f"«{task_title}» was due {due_date} and is still open.\n"
            f"Reschedule or close it in TaskHub.\n"
        )
        return await self.send(to=to, subject=subject, body=body)

    async def send_overdue_digest(self, *, to: str, lines: list[str]) -> bool:
        """One e-mail listing every overdue task instead of N separate mails."""
        if not lines:
            return False
        body = "Hi!\n\nThe following tasks are past their due date:\n\n"
        body += "\n".join(f"  • {line}" for line in lines)
        body += "\n\nOpen TaskHub to reschedule or close them.\n"
        return await self.send(
            to=to,
            subject=f"{len(lines)} overdue task(s) in TaskHub",
            body=body,
        )

    async def send_welcome(self, *, to: str, username: str) -> bool:
        """Confirmation sent after registration."""
        return await self.send(
            to=to,
            subject="Welcome to TaskHub",
            body=(
                f"Hi {username}!\n\n"
                f"Your TaskHub account is ready. Create a project, add a task, "
                f"and you have a plan for the day.\n"
            ),
        )

    # ----------------------------------------------------------------- private
    async def send(self, *, to: str, subject: str, body: str) -> bool:
        """Send one e-mail. Returns `False` instead of raising on transport errors."""
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr(("TaskHub", self.settings.first_superuser_email))
        message["To"] = to
        message.set_content(body)

        try:
            await self._send_via_smtp(message)
        except Exception as exc:
            logger.warning("email_send_failed", to=to, subject=subject, error=str(exc))
            return False
        logger.info("email_sent", to=to, subject=subject)
        return True

    async def _send_via_smtp(self, message: EmailMessage) -> None:
        """Blocking SMTP call offloaded to a worker thread."""
        import anyio.to_thread

        settings = self.settings

        def _send() -> None:
            with smtplib.SMTP(
                host=settings.smtp_host,
                port=settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
            ) as server:
                if settings.smtp_user and settings.smtp_password:
                    server.starttls()
                    server.login(
                        settings.smtp_user.get_secret_value(),
                        settings.smtp_password.get_secret_value(),
                    )
                server.send_message(message)

        await anyio.to_thread.run_sync(_send)

    @staticmethod
    def build_login_link(settings: Settings, *, email: str) -> str:
        """Build a one-tap login link (used by the e-mail templates in production)."""
        token = create_token(
            subject=email,
            token_type=TokenType.ACCESS,
            settings=settings,
            additional_claims={"purpose": "magic-link"},
        )
        return f"{settings.frontend_base_url}/auth/magic?token={token.token}"
