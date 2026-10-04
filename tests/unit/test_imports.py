"""Guards against the classic CI packaging trap.

When a package is installed (`pip install -e .`) *and* the repository root is on
`sys.path` (pytest's `pythonpath = ["."]`), the application can end up imported from a
stale location — a site-packages copy, a previous build, or an editable finder pointing
elsewhere. The symptom is baffling: every test passes while coverage reports whole
service methods as never executed.

These tests fail loudly in that situation instead of silently skewing the coverage gate,
which is the only reason the reported number can be trusted.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from app import models, repositories, services

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _modules() -> list[ModuleType]:
    return [
        models,
        repositories,
        services,
        pytest.importorskip("app.services.task"),
        pytest.importorskip("app.api.v1.router"),
        pytest.importorskip("app.main"),
    ]


@pytest.mark.parametrize("module", _modules(), ids=lambda m: m.__name__)
def test_module_comes_from_the_checkout(module: ModuleType) -> None:
    """Every application module must load from this repository."""
    source = Path(module.__file__ or "").resolve()
    assert source.is_relative_to(PROJECT_ROOT), (
        f"{module.__name__} was imported from {source}, which is outside {PROJECT_ROOT}. "
        "An installed copy is shadowing the source tree."
    )


def _package_root() -> Path:
    import app

    return Path(app.__file__ or ".").resolve().parent


def test_no_app_module_is_loaded_from_two_locations() -> None:
    """Every `app.*` module in `sys.modules` must live inside the same package root."""
    package_root = _package_root()
    strays = sorted(
        str(Path(module.__file__ or ".").resolve())
        for name, module in sys.modules.items()
        if name.startswith("app.")
        and getattr(module, "__file__", None)
        and not Path(module.__file__ or ".").resolve().is_relative_to(package_root)
    )
    assert not strays, f"`app` modules loaded from outside {package_root}: {strays}"


def test_only_one_importable_app_package_exists() -> None:
    """`sys.path` must not offer two different directories containing `app`."""
    package_root = _package_root()
    found = {
        str(Path(entry).resolve() / "app")
        for entry in sys.path
        if entry and (Path(entry) / "app" / "__init__.py").is_file()
    }
    assert found <= {str(package_root)}, f"multiple importable `app` packages: {sorted(found)}"


def test_the_package_metadata_points_at_this_checkout() -> None:
    """The editable install must resolve back to this tree, not to a copied wheel."""
    from importlib.metadata import distribution

    files = distribution("taskhub-api").files or []
    assert files, "the distribution reports no files — is it installed?"
    direct_url = distribution("taskhub-api").read_text("direct_url.json")
    assert direct_url is None or "true" in direct_url, (
        "the distribution looks like a regular install, not an editable one; "
        "a copied copy of the source could shadow this checkout"
    )
