from importlib.metadata import PackageNotFoundError, version


def application_version() -> str:
    """Return the installed package version without duplicating release metadata."""
    try:
        return version("cse-hq-bot")
    except PackageNotFoundError:
        return "development"
