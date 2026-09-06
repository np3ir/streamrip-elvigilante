import sys
from contextlib import contextmanager

from rich.console import Console
from rich.text import Text

# On Windows the default console encoding is cp1252, which can't represent
# many Unicode characters (e.g. fullwidth glyphs in Tidal/Deezer metadata).
# Switching to UTF-8 + replace-on-error prevents UnicodeEncodeError crashes.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

console = Console()


class _QuietStatus:
    def update(self, *_args, **_kwargs):
        """Match Rich Status.update when live rendering is unavailable."""


@contextmanager
def console_status(*args, **kwargs):
    """Render a spinner only on an interactive terminal.

    Rich live displays are inappropriate for redirected output and can collide
    with an existing progress display. Plain output remains fully functional.
    """

    if not console.is_terminal or getattr(console, "_live", None) is not None:
        yield _QuietStatus()
        return
    with console.status(*args, **kwargs) as status:
        yield status

def print_banner():
    banner_text = r"""
  _______ _   _  _____   _____  _
 |__   __| | | ||  __ \ |  __ \| |
    | |  | | | || |  | || |  | | |
    | |  | | | || |  | || |  | | |
    | |  | |_| || |__| || |__| | |
    |_|   \___/ |_____/ |_____/|_|
    """
    # Create a cyan panel with the banner
    grid = Text(banner_text, style="bold cyan")
    console.print(grid)
    console.print("[bold cyan]Streamrip[/bold cyan] [white]styled as[/white] [bold cyan]TiDDL[/bold cyan]\n")
