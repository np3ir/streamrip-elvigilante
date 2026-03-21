import sys

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.style import Style

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
