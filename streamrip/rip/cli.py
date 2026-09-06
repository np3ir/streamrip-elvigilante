import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from functools import wraps
from pathlib import Path
from typing import Any

import aiofiles
import aiohttp
import click
from click_help_colors import HelpColorsGroup  # type: ignore
from rich.logging import RichHandler
from rich.prompt import Confirm
from rich.traceback import install

from .. import __version__, db
from ..config import DEFAULT_CONFIG_PATH, Config, OutdatedConfigError, set_user_defaults
from ..console import console
from ..exceptions import ReferenceIdentityUnavailableError, TidalRateLimitError
from ..progress import clear_progress
from ..utils.ssl_utils import get_aiohttp_connector_kwargs
from .main import Main


def coro(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return asyncio.run(f(*args, **kwargs))
        except TidalRateLimitError as error:
            raise click.ClickException(
                f"{error}. The run stopped safely; retry later."
            ) from error
        finally:
            clear_progress()

    return wrapper


def _is_help_invocation(argv=None) -> bool:
    """Return whether Click was invoked only to render help.

    Click runs the parent group callback before subcommand help, so without this
    guard even ``rip compare --help`` can migrate the user's configuration.
    """

    args = sys.argv[1:] if argv is None else argv
    return any(arg in {"-h", "--help"} for arg in args)


async def _get_logged_in_client_bounded(
    main,
    source: str,
    *,
    prompt_on_missing: bool = True,
    timeout: float = 45.0,
):
    """Prevent one external login from stalling a multi-service operation."""

    return await asyncio.wait_for(
        main.get_logged_in_client(source, prompt_on_missing=prompt_on_missing),
        timeout=timeout,
    )


async def _compare_with_reference_failover(
    *, source, track_id, metadata, reference_client, reference_quality,
    comparator, qualities, ceiling,
):
    """Continue on other services when TIDAL's run-wide breaker trips."""

    from ..client.candidate import service_candidate, track_identity

    try:
        downloadable = await reference_client.get_downloadable(
            track_id, reference_quality
        )
    except TidalRateLimitError as error:
        if source != "tidal":
            raise
        identity = track_identity(source, metadata)
        has_exact_identity = bool((identity.isrc or "").strip())
        has_safe_fallback_identity = bool(
            identity.source_id
            and identity.title.strip()
            and identity.artist.strip()
        )
        if not has_exact_identity and not has_safe_fallback_identity:
            raise ReferenceIdentityUnavailableError(
                "TIDAL rate-limit failover stopped because the recording "
                "identity is incomplete"
            ) from error
        report = await comparator.compare(
            identity, qualities, ceiling=ceiling
        )
        report.errors["tidal"] = f"{type(error).__name__}: {error}"
        return report

    reference = service_candidate(source, metadata, downloadable)
    return await comparator.compare(
        reference.identity,
        qualities,
        reference_candidate=reference,
        ceiling=ceiling,
    )


@click.group(
    cls=HelpColorsGroup,
    help_headers_color="yellow",
    help_options_color="green",
)
@click.version_option(version=__version__)
@click.option(
    "--config-path",
    default=DEFAULT_CONFIG_PATH,
    help="Path to the configuration file",
    type=click.Path(readable=True, writable=True),
)
@click.option(
    "-f",
    "--folder",
    help="The folder to download items into.",
    type=click.Path(file_okay=False, dir_okay=True),
)
@click.option(
    "-ndb",
    "--no-db",
    help="Download items even if they have been logged in the database",
    default=False,
    is_flag=True,
)
@click.option(
    "-q",
    "--quality",
    help="The maximum quality allowed to download",
    type=click.IntRange(min=0, max=4),
)
@click.option(
    "-c",
    "--codec",
    help="Convert the downloaded files to an audio codec (ALAC, FLAC, MP3, AAC, or OGG)",
)
@click.option(
    "--no-progress",
    help="Do not show progress bars",
    is_flag=True,
    default=False,
)
@click.option(
    "--no-ssl-verify",
    help="Disable SSL certificate verification (use if you encounter SSL errors)",
    is_flag=True,
    default=False,
)
@click.option(
    "-v",
    "--verbose",
    help="Enable verbose output (debug mode)",
    is_flag=True,
)
@click.pass_context
def rip(
    ctx, config_path, folder, no_db, quality, codec, no_progress, no_ssl_verify, verbose
):
    """Streamrip: the all in one music downloader."""
    ctx.ensure_object(dict)
    if _is_help_invocation():
        ctx.obj["config_path"] = config_path
        ctx.obj["config"] = None
        return

    global logger
    logging.basicConfig(
        level="INFO",
        format="%(message)s",
        datefmt="[%X]",
        # Reuse the process-level console. Click's CliRunner replaces and then
        # closes its temporary stdout; a handler-created Console can retain
        # that closed stream across invocations and poison later Rich output.
        handlers=[RichHandler(console=console)],
    )
    logger = logging.getLogger("streamrip")
    if verbose:
        install(
            console=console,
            suppress=[
                click,
            ],
            show_locals=True,
            locals_hide_sunder=False,
        )
        logger.setLevel(logging.DEBUG)
        logger.debug("Showing all debug logs")
    else:
        install(console=console, suppress=[click, asyncio], max_frames=1)
        logger.setLevel(logging.INFO)

    if not os.path.isfile(config_path):
        console.print(
            f"No file found at [bold cyan]{config_path}[/bold cyan], creating default config.",
        )
        set_user_defaults(config_path)

    # pass to subcommands
    ctx.obj["config_path"] = config_path

    try:
        c = Config(config_path)
    except OutdatedConfigError as e:
        console.print(e)
        console.print("Auto-updating config file...")
        Config.update_file(config_path)
        c = Config(config_path)
    except Exception as e:
        console.print(
            f"[red]Error loading config from[/red] [bold cyan]{config_path}[/bold cyan]: {e}\n"
            "Try running [bold]rip config reset[/bold]",
        )
        ctx.obj["config"] = None
        return

    # set session config values to command line args
    if no_db:
        c.session.database.downloads_enabled = False
        c.session.database.failed_downloads_enabled = False
        c.session.database.isrc_enabled = False
    if folder is not None:
        c.session.downloads.folder = folder

    if quality is not None:
        c.session.qobuz.quality = quality
        c.session.tidal.quality = quality
        c.session.deezer.quality = quality
        c.session.soundcloud.quality = quality

    if codec is not None:
        c.session.conversion.enabled = True
        assert codec.upper() in ("ALAC", "FLAC", "OGG", "MP3", "AAC")
        c.session.conversion.codec = codec.upper()

    if no_progress:
        c.session.cli.progress_bars = False

    if no_ssl_verify:
        c.session.downloads.verify_ssl = False

    ctx.obj["config"] = c


@rip.command()
@click.argument("urls", nargs=-1, required=True)
@click.pass_context
@coro
async def url(ctx, urls):
    """Download content from URLs."""
    if ctx.obj["config"] is None:
        return

    try:
        with ctx.obj["config"] as cfg:
            cfg: Config
            async with Main(cfg) as main:
                await main.add_all(urls)
                await main.resolve()
                await main.rip()

    except aiohttp.ClientConnectorCertificateError as e:
        from ..utils.ssl_utils import print_ssl_error_help

        console.print(f"[red]SSL Certificate verification error: {e}[/red]")
        print_ssl_error_help()


@rip.command()
@click.argument(
    "path",
    required=True,
    type=click.Path(exists=True, readable=True, file_okay=True, dir_okay=False),
)
@click.pass_context
@coro
async def file(ctx, path):
    """Download content from URLs in a file.

    Example usage:

        rip file urls.txt
    """
    try:
        with ctx.obj["config"] as cfg:
            async with Main(cfg) as main:
                async with aiofiles.open(path, "r") as f:
                    content = await f.read()
                    try:
                        items: Any = json.loads(content)
                        loaded = True
                    except json.JSONDecodeError:
                        items = content.split()
                        loaded = False
                if loaded:
                    console.print(
                        f"Detected json file. Loading [yellow]{len(items)}[/yellow] items"
                    )
                    await main.add_all_by_id(
                        [(i["source"], i["media_type"], i["id"]) for i in items]
                    )
                else:
                    s = set(items)
                    if len(s) < len(items):
                        console.print(
                            f"Found [orange]{len(items)-len(s)}[/orange] repeated URLs!"
                        )
                        items = list(s)
                    console.print(
                        f"Detected list of urls. Loading [yellow]{len(items)}[/yellow] items"
                    )
                    await main.add_all(items)

                await main.resolve()
                await main.rip()
    except aiohttp.ClientConnectorCertificateError as e:
        from ..utils.ssl_utils import print_ssl_error_help

        console.print(f"[red]SSL Certificate verification error: {e}[/red]")
        print_ssl_error_help()


@rip.group()
def config():
    """Manage configuration files."""


@config.command("open")
@click.option("-v", "--vim", help="Open in (Neo)Vim", is_flag=True)
@click.pass_context
def config_open(ctx, vim):
    """Open the config file in a text editor."""
    config_path = ctx.obj["config_path"]

    console.print(f"Opening file at [bold cyan]{config_path}")
    if vim:
        if shutil.which("nvim") is not None:
            subprocess.run(["nvim", config_path])
        elif shutil.which("vim") is not None:
            subprocess.run(["vim", config_path])
        else:
            logger.error("Could not find nvim or vim. Using default launcher.")
            click.launch(config_path)
    else:
        click.launch(config_path)


@config.command("reset")
@click.option("-y", "--yes", help="Don't ask for confirmation.", is_flag=True)
@click.pass_context
def config_reset(ctx, yes):
    """Reset the config file."""
    config_path = ctx.obj["config_path"]
    if not yes:
        if not Confirm.ask(
            f"Are you sure you want to reset the config file at {config_path}?",
        ):
            console.print("[green]Reset aborted")
            return

    set_user_defaults(config_path)
    console.print(f"Reset the config file at [bold cyan]{config_path}!")


@config.command("path")
@click.pass_context
def config_path(ctx):
    """Display the path of the config file."""
    config_path = ctx.obj["config_path"]
    console.print(f"Config path: [bold cyan]'{config_path}'")


@rip.group("login")
def login_group():
    """Manage private service sessions."""


@login_group.command("tidal")
@click.option(
    "--fallback",
    is_flag=True,
    help="Authorize the TV-client token used for reliable LOSSLESS fallback.",
)
@click.pass_context
@coro
async def login_tidal(ctx, fallback):
    """Authorize a TIDAL HiRes or LOSSLESS-fallback session."""

    from ..client import TidalClient

    cfg: Config | None = ctx.obj["config"]
    if cfg is None:
        return

    client = TidalClient.lossless_fallback(cfg) if fallback else TidalClient(cfg)
    label = "LOSSLESS fallback" if fallback else "HiRes"
    try:
        await client._open_session()
        device_code, uri = await client._get_device_code()
        login_link = f"https://{uri}"
        console.print(
            f"Authorize the TIDAL {label} session at "
            f"[blue underline]{login_link}[/blue underline]."
        )
        click.launch(login_link)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 600
        info = None
        while loop.time() < deadline:
            status, result = await client._get_auth_status(device_code)
            if status == 0:
                info = result
                break
            if status != 2:
                raise click.ClickException("TIDAL authorization failed.")
            await asyncio.sleep(4)
        if info is None:
            raise click.ClickException("TIDAL authorization timed out.")

        client.apply_device_auth(info)
        if not fallback:
            session = cfg.session.tidal
            stored = cfg.file.tidal
            stored.user_id = session.user_id
            stored.country_code = session.country_code
            stored.access_token = session.access_token
            stored.refresh_token = session.refresh_token
            stored.token_expiry = session.token_expiry
            cfg.file.set_modified()
            cfg.save_file()
    finally:
        await client.close()

    console.print(f"[green]TIDAL {label} session configured.[/green]")


@login_group.command("qobuz")
@click.option(
    "--token",
    "use_auth_token",
    is_flag=True,
    help="Use a Qobuz user ID and auth token instead of email and password.",
)
@click.pass_context
@coro
async def login_qobuz(ctx, use_auth_token):
    """Log in to Qobuz and store only the resulting auth token."""

    from .login import authenticate_qobuz

    cfg: Config | None = ctx.obj["config"]
    if cfg is None:
        return

    identity = click.prompt("Qobuz user ID" if use_auth_token else "Qobuz email")
    credential = click.prompt(
        "Qobuz auth token" if use_auth_token else "Qobuz password",
        hide_input=True,
    )
    try:
        with cfg:
            user_id = await authenticate_qobuz(
                cfg,
                identity,
                credential,
                use_auth_token=use_auth_token,
            )
    except Exception:
        raise click.ClickException("Qobuz authentication failed.") from None

    console.print(f"[green]Qobuz login validated for user {user_id}.[/green]")


@login_group.command("deezer")
@click.option(
    "--arl",
    "manual_arl",
    is_flag=True,
    help="Enter an ARL manually instead of opening the assisted browser login.",
)
@click.pass_context
@coro
async def login_deezer(ctx, manual_arl):
    """Log in to Deezer using a browser or a manually supplied ARL."""

    from .login import (
        BrowserLoginCancelledError,
        BrowserLoginUnavailableError,
        authenticate_deezer,
        capture_deezer_arl,
    )

    cfg: Config | None = ctx.obj["config"]
    if cfg is None:
        return

    if manual_arl:
        arl = click.prompt("Deezer ARL", hide_input=True)
    else:
        console.print(
            "Opening a private Deezer login window. Streamrip will store only "
            "the resulting session cookie."
        )
        try:
            arl = capture_deezer_arl()
        except (BrowserLoginUnavailableError, BrowserLoginCancelledError) as error:
            raise click.ClickException(str(error)) from None
    try:
        with cfg:
            user = await authenticate_deezer(cfg, arl)
    except Exception:
        raise click.ClickException("Deezer authentication failed.") from None

    console.print(f"[green]Deezer login validated for {user}.[/green]")


@login_group.command("status")
@click.pass_context
def login_status(ctx):
    """Show configured sessions without contacting a service."""

    from .login import configured_services

    cfg: Config | None = ctx.obj["config"]
    if cfg is None:
        return
    for service, configured in configured_services(cfg).items():
        state = "configured" if configured else "not configured"
        console.print(f"{service}: {state}")


@login_group.command("logout")
@click.argument(
    "service",
    type=click.Choice(["qobuz", "deezer"], case_sensitive=False),
)
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
@click.pass_context
def login_logout(ctx, service, yes):
    """Remove locally stored credentials for one service."""

    from .login import logout_service

    cfg: Config | None = ctx.obj["config"]
    if cfg is None:
        return
    if not yes and not Confirm.ask(f"Remove the stored {service} session?"):
        console.print("Logout aborted")
        return
    with cfg:
        logout_service(cfg, service)
    console.print(f"[green]Removed the stored {service} session.[/green]")


@rip.group("recovery")
def recovery_group():
    """Inspect and recover verified media staging files."""


@recovery_group.command("list")
def recovery_list():
    """List verified staging files retained after publication failures."""

    from ..file_publish import list_recoveries

    entries = list_recoveries()
    if not entries:
        console.print("No retained staging files.")
        return
    for entry in entries:
        console.print(
            f"{entry.id}  {entry.size} bytes  {entry.destination_path}"
        )


@recovery_group.command("retry")
@click.argument("entry_id")
@click.pass_context
@coro
async def recovery_retry(ctx, entry_id):
    """Retry atomic publication of one unchanged retained staging file."""

    from ..file_publish import RecoveryError, retry_recovery

    try:
        cfg: Config | None = ctx.obj["config"]
        mode = (
            cfg.session.downloads.destination_identity if cfg is not None else "off"
        )
        entry = await retry_recovery(entry_id.lower(), identity_mode=mode)
    except (RecoveryError, OSError) as error:
        raise click.ClickException(str(error)) from None
    console.print(f"[green]Recovered {entry.destination_path}.[/green]")


@recovery_group.command("discard")
@click.argument("entry_id")
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
def recovery_discard(entry_id, yes):
    """Delete one verified retained stage and its recovery record."""

    from ..file_publish import RecoveryError, get_recovery, remove_recovery

    try:
        entry = get_recovery(entry_id.lower())
        if not yes and not Confirm.ask(
            f"Permanently delete retained staging file for {entry.destination_path}?"
        ):
            console.print("Discard aborted")
            return
        remove_recovery(entry.id, delete_staging=True)
    except (RecoveryError, OSError) as error:
        raise click.ClickException(str(error)) from None
    console.print("[green]Removed the retained staging file and recovery record.[/green]")


@rip.group("destination")
def destination_group():
    """Manage trusted destination-volume identities."""


@destination_group.command("trust")
@click.argument(
    "root", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--adopt-existing",
    is_flag=True,
    help="Adopt an existing marker created by another Streamrip installation.",
)
@click.option("-y", "--yes", is_flag=True, help="Confirm the volume is mounted.")
def destination_trust(root, adopt_existing, yes):
    """Trust the volume currently mounted at ROOT."""

    from ..destination_identity import DestinationIdentityError, trust_destination

    if not yes and not Confirm.ask(
        f"Is the intended destination volume currently mounted at {root}?"
    ):
        console.print("Trust aborted")
        return
    try:
        trust_destination(root, adopt_existing=adopt_existing)
    except DestinationIdentityError as error:
        raise click.ClickException(str(error)) from None
    console.print(f"[green]Trusted destination {root} on this machine.[/green]")


@destination_group.command("status")
@click.argument("root", type=click.Path(file_okay=False, path_type=Path))
def destination_status(root):
    """Check whether ROOT currently matches its local trust record."""

    from ..destination_identity import DestinationIdentityError, check_destination

    try:
        record = check_destination(root, root)
    except DestinationIdentityError as error:
        raise click.ClickException(str(error)) from None
    console.print(
        f"[green]Trusted destination is present.[/green] ID {record.anchor_id}"
    )


@destination_group.command("forget")
@click.argument("root", type=click.Path(file_okay=False, path_type=Path))
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
def destination_forget(root, yes):
    """Remove local trust without modifying the destination marker."""

    from ..destination_identity import forget_destination

    if not yes and not Confirm.ask(f"Forget local trust for {root}?"):
        console.print("Forget aborted")
        return
    removed = forget_destination(root)
    message = "Removed local destination trust." if removed else "No local trust existed."
    console.print(f"[green]{message}[/green]")


@rip.group()
def database():
    """View and modify the downloads and failed downloads databases."""


@database.command("browse")
@click.argument("table")
@click.pass_context
def database_browse(ctx, table):
    """Browse the contents of a table.

    Available tables:

        * Downloads

        * Failed
    """
    from rich.table import Table

    cfg: Config = ctx.obj["config"]

    if table.lower() == "downloads":
        downloads = db.Downloads(cfg.session.database.downloads_path)
        t = Table(title="Downloads database")
        t.add_column("Row")
        t.add_column("ID")
        for i, row in enumerate(downloads.all()):
            t.add_row(f"{i:02}", *row)
        console.print(t)

    elif table.lower() == "failed":
        failed = db.Failed(cfg.session.database.failed_downloads_path)
        t = Table(title="Failed downloads database")
        t.add_column("Source")
        t.add_column("Media Type")
        t.add_column("ID")
        for i, row in enumerate(failed.all()):
            t.add_row(f"{i:02}", *row)
        console.print(t)

    else:
        console.print(
            f"[red]Invalid database[/red] [bold]{table}[/bold]. [red]Choose[/red] [bold]downloads "
            "[red]or[/red] failed[/bold].",
        )


@rip.command()
@click.option(
    "-f",
    "--first",
    help="Automatically download the first search result without showing the menu.",
    is_flag=True,
)
@click.option(
    "-o",
    "--output-file",
    help="Write search results to a file instead of showing interactive menu.",
    type=click.Path(writable=True),
)
@click.option(
    "-n",
    "--num-results",
    help="Maximum number of search results to show",
    default=100,
    type=click.IntRange(min=1),
)
@click.argument("source", required=True)
@click.argument("media-type", required=True)
@click.argument("query", required=True)
@click.pass_context
@coro
async def search(ctx, first, output_file, num_results, source, media_type, query):
    """Search for content using a specific source.

    Example:

        rip search qobuz album 'rumours'
    """
    if first and output_file:
        console.print("Cannot choose --first and --output-file!")
        return
    with ctx.obj["config"] as cfg:
        async with Main(cfg) as main:
            if first:
                await main.search_take_first(source, media_type, query)
            elif output_file:
                await main.search_output_file(
                    source, media_type, query, output_file, num_results
                )
            else:
                await main.search_interactive(source, media_type, query)
            await main.resolve()
            await main.rip()


@rip.command("compare")
@click.option(
    "--download-best",
    is_flag=True,
    help="Download the highest-fidelity match for each compared track.",
)
@click.option(
    "--service",
    "services",
    multiple=True,
    type=click.Choice(["tidal", "qobuz", "deezer"], case_sensitive=False),
    help="Service to compare; repeat the option. Defaults to all three.",
)
@click.option(
    "--priority",
    "service_priority",
    multiple=True,
    type=click.Choice(["tidal", "qobuz", "deezer"], case_sensitive=False),
    help="Tie-break service order; repeat from highest to lowest priority.",
)
@click.option(
    "--type",
    "media_type",
    type=click.Choice(["track", "album", "playlist", "artist"], case_sensitive=False),
    default="track",
    show_default=True,
    help="Type of an ID target; URLs detect their type automatically.",
)
@click.option(
    "--max-bit-depth",
    type=click.IntRange(min=1, max=64),
    help="Maximum lossless bit depth; higher deliveries are excluded.",
)
@click.option(
    "--max-sample-rate",
    type=click.FloatRange(min=1),
    help="Maximum sample rate in kHz (for example 44.1, 48, 96 or 192).",
)
@click.option(
    "--prefer-lossless/--no-prefer-lossless",
    default=None,
    help="Override the permanent lossless preference for this run.",
)
@click.option(
    "--fallback-to-lossy/--no-fallback-to-lossy",
    default=None,
    help="Allow or reject lossy fallback when no lossless candidate qualifies.",
)
@click.argument("source-or-url")
@click.argument("item-id", required=False)
@click.pass_context
@coro
async def compare_sources(
    ctx,
    download_best,
    media_type,
    services,
    service_priority,
    max_bit_depth,
    max_sample_rate,
    prefer_lossless,
    fallback_to_lossy,
    source_or_url,
    item_id,
):
    """Compare a track or collection across services; downloading is opt-in."""

    from ..comparison import (
        MultiSourceComparator,
        format_quality,
        resolve_comparison_collection,
        service_quality_for_ceiling,
    )
    from ..multisource import QualityCeiling, match_tracks, normalize_sample_rate
    from .parse_url import GenericURL, parse_url

    if ctx.obj["config"] is None:
        return

    if item_id is None:
        parsed = parse_url(source_or_url)
        if not isinstance(parsed, GenericURL):
            raise click.UsageError(
                "Provide SOURCE ITEM_ID or a Tidal, Qobuz, or Deezer URL."
            )
        source, detected_type, item_id = parsed.match.groups()
        if detected_type == "mix":
            detected_type = "playlist"
        if detected_type not in {"track", "album", "playlist", "artist"}:
            raise click.UsageError(f"Unsupported comparison URL type: {detected_type}")
        media_type = detected_type
    else:
        source = source_or_url.lower()
        if source not in {"tidal", "qobuz", "deezer"}:
            raise click.UsageError("SOURCE must be tidal, qobuz, or deezer.")

    configured = tuple(dict.fromkeys(services or ("tidal", "qobuz", "deezer")))
    if source not in configured:
        configured = (source, *configured)

    with ctx.obj["config"] as cfg:
        policy = cfg.session.comparison
        configured_priority = tuple(policy.service_priority)
        effective_priority = tuple(
            dict.fromkeys((*service_priority, *configured_priority))
        )
        bit_depth = (
            max_bit_depth
            if max_bit_depth is not None
            else (policy.max_bit_depth or None)
        )
        sample_rate = (
            max_sample_rate
            if max_sample_rate is not None
            else (policy.max_sample_rate or None)
        )
        ceiling = QualityCeiling(
            bit_depth=bit_depth,
            sample_rate_hz=normalize_sample_rate(sample_rate),
            prefer_lossless=(
                prefer_lossless
                if prefer_lossless is not None
                else policy.prefer_lossless
            ),
            fallback_to_lossy=(
                fallback_to_lossy
                if fallback_to_lossy is not None
                else policy.fallback_to_lossy
            ),
        )
        async with Main(cfg) as main:
            reference_client = await _get_logged_in_client_bounded(main, source)
            collection = await resolve_comparison_collection(
                reference_client, media_type, item_id
            )
            if not collection.track_ids:
                console.print("[yellow]The collection contains no comparable tracks.[/yellow]")
                return
            if media_type != "track":
                console.print(
                    f"[bold cyan]{collection.name}[/bold cyan] — "
                    f"{len(collection.track_ids)} track(s)"
                )
            reference_quality = service_quality_for_ceiling(
                source,
                cfg.session.get_source(source).quality,
                ceiling,
            )
            active_clients = {source: reference_client}
            login_errors = {}
            for service in configured:
                if service == source:
                    continue
                try:
                    active_clients[service] = await _get_logged_in_client_bounded(
                        main, service, prompt_on_missing=False
                    )
                except Exception as error:
                    login_errors[service] = f"{type(error).__name__}: {error}"

            qualities = {
                service: service_quality_for_ceiling(
                    service,
                    cfg.session.get_source(service).quality,
                    ceiling,
                )
                for service in active_clients
            }
            comparator = MultiSourceComparator(
                active_clients,
                service_priority=effective_priority,
            )
            winners: dict[str, int] = {}
            selected_tracks = []
            for position, track_id in enumerate(collection.track_ids, start=1):
                reference_metadata = collection.track_metadata.get(track_id)
                if reference_metadata is None:
                    reference_metadata = await reference_client.get_metadata(
                        track_id, "track"
                    )
                report = await _compare_with_reference_failover(
                    source=source,
                    track_id=track_id,
                    metadata=reference_metadata,
                    reference_client=reference_client,
                    reference_quality=reference_quality,
                    comparator=comparator,
                    qualities=qualities,
                    ceiling=ceiling,
                )
                report.errors.update(login_errors)
                if media_type != "track":
                    console.print(
                        f"\n[bold]Track {position}/{len(collection.track_ids)}:[/bold] "
                        f"{report.reference.artist} — {report.reference.title}"
                    )
                _print_comparison_report(report, format_quality, match_tracks)
                if report.selected is not None:
                    winner = report.selected.identity.source
                    winners[winner] = winners.get(winner, 0) + 1
                    selected_tracks.append(report.selected)

            if media_type != "track":
                summary = ", ".join(
                    f"{service}: {count}" for service, count in winners.items()
                ) or "none"
                console.print(f"\n[bold green]Collection winners:[/bold green] {summary}")

            if download_best and selected_tracks:
                for selected in selected_tracks:
                    await main.add_by_id(
                        selected.identity.source,
                        "track",
                        selected.identity.source_id,
                    )
                await main.resolve()
                await main.rip()
                console.print(
                    f"[green]Downloaded {len(selected_tracks)} best-source track(s).[/green]"
                )


def _print_comparison_report(report, format_quality, match_tracks):
    from rich.table import Table

    table = Table(title="Cross-service audio comparison")
    table.add_column("Service")
    table.add_column("Track ID")
    table.add_column("Match")
    table.add_column("Available quality")
    table.add_column("Selected")
    for candidate in report.candidates:
        match = match_tracks(report.reference, candidate.identity).value.upper()
        table.add_row(
            candidate.identity.source,
            candidate.identity.source_id,
            match,
            format_quality(candidate.quality),
            "✓" if candidate is report.selected else "",
        )
    console.print(table)

    if report.selected is None:
        console.print("[yellow]No equivalent playable recording was found.[/yellow]")
    else:
        selected = report.selected
        console.print(
            f"[green]Best source:[/green] {selected.identity.source} "
            f"({format_quality(selected.quality)})"
        )
    for service, error in report.errors.items():
        console.print(f"[yellow]{service} unavailable:[/yellow] {error}")


@rip.command()
@click.option("-s", "--source", help="The source to search tracks on.")
@click.option(
    "-fs",
    "--fallback-source",
    help="The source to search tracks on if no results were found with the main source.",
)
@click.argument("url", required=True)
@click.pass_context
@coro
async def lastfm(ctx, source, fallback_source, url):
    """Download tracks from a last.fm playlist."""
    config = ctx.obj["config"]
    if source is not None:
        config.session.lastfm.source = source
    if fallback_source is not None:
        config.session.lastfm.fallback_source = fallback_source
    with config as cfg:
        async with Main(cfg) as main:
            await main.resolve_lastfm(url)
            await main.rip()


@rip.command()
@click.argument("source")
@click.argument("media-type")
@click.argument("id")
@click.pass_context
@coro
async def id(ctx, source, media_type, id):
    """Download an item by ID."""
    with ctx.obj["config"] as cfg:
        async with Main(cfg) as main:
            await main.add_by_id(source, media_type, id)
            await main.resolve()
            await main.rip()


@rip.command("library")
@click.option("--tracks", "expansion", flag_value="tracks", default=True,
              help="Process playlist entries as standalone recordings.")
@click.option("--albums", "expansion", flag_value="albums",
              help="Expand playlist entries into their complete albums.")
@click.option("--artists", "expansion", flag_value="artists",
              help="Expand playlist credits into complete artist discographies.")
@click.option("--dry-run/--download", default=True,
              help="Plan only, or explicitly download the selected recordings.")
@click.option("--resume/--no-resume", default=False,
              help="Skip track keys completed by the same URL and policy.")
@click.option("--max-tracks", type=click.IntRange(min=0), default=0,
              help="Stop after this many new tracks; zero means unlimited.")
@click.option("--workers", type=click.IntRange(min=1, max=8),
              help="Concurrent comparisons; defaults to comparison.library_workers.")
@click.option("--manifest/--no-manifest", default=None,
              help="Write a credential-free JSONL audit manifest.")
@click.option("--manifest-path", type=click.Path(path_type=Path, dir_okay=False),
              help="Override the audit manifest path for this job.")
@click.option(
    "--priority", "service_priority", multiple=True,
    type=click.Choice(["tidal", "qobuz", "deezer"], case_sensitive=False),
    help="Tie-break service order; repeat from highest to lowest priority.",
)
@click.option(
    "--max-bit-depth", type=click.IntRange(min=1, max=64),
    help="Maximum lossless bit depth for this job.",
)
@click.option(
    "--max-sample-rate", type=click.FloatRange(min=1),
    help="Maximum sample rate in kHz for this job.",
)
@click.option(
    "--save-lyrics/--no-save-lyrics", default=None,
    help="Save synchronized .lrc sidecars for this job.",
)
@click.argument("url")
@click.pass_context
@coro
async def library(
    ctx,
    expansion,
    dry_run,
    resume,
    max_tracks,
    workers,
    manifest,
    manifest_path,
    service_priority,
    max_bit_depth,
    max_sample_rate,
    save_lyrics,
    url,
):
    """Build a resumable best-quality library plan from a service URL."""

    from ..comparison import MultiSourceComparator, service_quality_for_ceiling
    from ..library import (
        LibraryCheckpoint,
        LibraryManifest,
        bounded_ordered_map,
        iter_library_tracks,
        library_job_signature,
    )
    from ..multisource import QualityCeiling, normalize_sample_rate
    from .parse_url import GenericURL, parse_url

    parsed = parse_url(url)
    if not isinstance(parsed, GenericURL):
        raise click.UsageError("Provide a standard Tidal, Qobuz, or Deezer URL.")
    source, media_type, item_id = parsed.match.groups()
    if media_type == "mix":
        media_type = "mix"
    if media_type not in {"track", "album", "playlist", "artist", "mix"}:
        raise click.UsageError(f"Unsupported library URL type: {media_type}")

    with ctx.obj["config"] as cfg:
        if save_lyrics is not None:
            cfg.session.lyrics.save_lrc = save_lyrics
        policy = cfg.session.comparison
        bit_depth = max_bit_depth if max_bit_depth is not None else (
            policy.max_bit_depth or None
        )
        sample_rate = max_sample_rate if max_sample_rate is not None else (
            policy.max_sample_rate or None
        )
        ceiling = QualityCeiling(
            bit_depth=bit_depth,
            sample_rate_hz=normalize_sample_rate(sample_rate),
            prefer_lossless=policy.prefer_lossless,
            fallback_to_lossy=policy.fallback_to_lossy,
        )
        priority = tuple(
            dict.fromkeys((*service_priority, *policy.service_priority))
        )
        comparison_workers = workers or policy.library_workers
        manifest_enabled = (
            policy.library_manifest if manifest is None else manifest
        ) or manifest_path is not None
        signature = library_job_signature(
            {
                "url": url.strip(),
                "expansion": expansion,
                "download": not dry_run,
                "bit_depth": bit_depth,
                "sample_rate": sample_rate,
                "prefer_lossless": ceiling.prefer_lossless,
                "fallback_to_lossy": ceiling.fallback_to_lossy,
                "priority": priority,
                "save_lyrics": cfg.session.lyrics.save_lrc,
            }
        )
        checkpoint = LibraryCheckpoint(signature).load()
        audit = (
            LibraryManifest(signature, path=manifest_path)
            if manifest_enabled
            else None
        )
        if resume and checkpoint.completed:
            console.print(
                f"[dim]Resume checkpoint: {len(checkpoint.completed)} completed key(s).[/dim]"
            )

        async with Main(cfg) as main:
            reference_client = await _get_logged_in_client_bounded(main, source)
            clients = {source: reference_client}
            unavailable = {}
            for service in ("tidal", "deezer", "qobuz"):
                if service == source:
                    continue
                try:
                    clients[service] = await _get_logged_in_client_bounded(
                        main, service, prompt_on_missing=False
                    )
                except Exception as error:
                    unavailable[service] = f"{type(error).__name__}: {error}"

            qualities = {
                service: service_quality_for_ceiling(
                    service,
                    cfg.session.get_source(service).quality,
                    ceiling,
                )
                for service in clients
            }
            comparator = MultiSourceComparator(
                clients, service_priority=priority
            )
            reference_quality = qualities[source]
            processed = 0
            attempted = 0
            skipped_resume = 0
            skipped_duplicate = 0
            failed = 0
            winners: dict[str, int] = {}
            seen: set[str] = set()
            if not dry_run:
                main.start_download_workers(
                    count=min(4, comparison_workers),
                    queue_size=max(4, comparison_workers * 2),
                )

            console.print(
                f"[bold cyan]Library job[/bold cyan] — {media_type} → {expansion}; "
                f"mode={'preview' if dry_run else 'download'}; "
                f"workers={comparison_workers}; signature={signature}"
            )

            async def eligible_tracks():
                nonlocal attempted, skipped_duplicate, skipped_resume
                async for track in iter_library_tracks(
                    reference_client, media_type, item_id, expansion
                ):
                    key = track.job_key(expansion)
                    if key in seen:
                        skipped_duplicate += 1
                        continue
                    seen.add(key)
                    if resume and checkpoint.is_done(key):
                        skipped_resume += 1
                        continue
                    if max_tracks and attempted >= max_tracks:
                        break
                    attempted += 1
                    yield track, key

            async def compare_track(item):
                track, key = item
                try:
                    report = await _compare_with_reference_failover(
                        source=source,
                        track_id=track.source_id,
                        metadata=track.reference_metadata,
                        reference_client=reference_client,
                        reference_quality=reference_quality,
                        comparator=comparator,
                        qualities=qualities,
                        ceiling=ceiling,
                    )
                except Exception as error:
                    return track, key, None, error
                return track, key, report, None

            async for track, key, report, error in bounded_ordered_map(
                eligible_tracks(), compare_track, comparison_workers
            ):
                if error is not None:
                    if isinstance(error, ReferenceIdentityUnavailableError):
                        raise click.ClickException(str(error)) from error
                    failed += 1
                    console.print(
                        f"[red]FAILED[/red] {track.artist} — {track.title}: "
                        f"{type(error).__name__}: {error}"
                    )
                    if audit is not None:
                        audit.record(
                            "failed",
                            key=key,
                            reference_source=track.source,
                            reference_id=track.source_id,
                            isrc=track.isrc,
                            title=track.title,
                            artist=track.artist,
                            error_type=type(error).__name__,
                        )
                    continue
                processed += 1
                selected = report.selected
                if selected is None:
                    failed += 1
                    console.print(
                        f"[{processed}] [yellow]NO MATCH[/yellow] "
                        f"{track.artist} — {track.title}"
                    )
                    if audit is not None:
                        audit.record(
                            "no_match",
                            key=key,
                            reference_source=track.source,
                            reference_id=track.source_id,
                            isrc=track.isrc,
                            title=track.title,
                            artist=track.artist,
                        )
                    continue
                winner = selected.identity.source
                winners[winner] = winners.get(winner, 0) + 1
                quality = selected.quality
                quality_text = "/".join(
                    part for part in (
                        quality.codec.upper(),
                        f"{quality.bit_depth}-bit" if quality.bit_depth else "",
                        f"{quality.sample_rate_hz / 1000:g}kHz"
                        if quality.sample_rate_hz else "",
                    ) if part
                )
                console.print(
                    f"[{processed}] {winner}: {quality_text} — "
                    f"{track.artist} — {track.title}"
                )
                if dry_run:
                    checkpoint.mark_done(key)
                    if audit is not None:
                        audit.record(
                            "previewed",
                            key=key,
                            reference_source=track.source,
                            reference_id=track.source_id,
                            isrc=track.isrc,
                            title=track.title,
                            artist=track.artist,
                            audio_source=winner,
                            audio_id=selected.identity.source_id,
                            codec=quality.codec,
                            lossless=quality.lossless,
                            bit_depth=quality.bit_depth,
                            sample_rate_hz=quality.sample_rate_hz,
                        )
                else:
                    if audit is not None:
                        audit.record(
                            "selected",
                            key=key,
                            reference_source=track.source,
                            reference_id=track.source_id,
                            isrc=track.isrc,
                            title=track.title,
                            artist=track.artist,
                            audio_source=winner,
                            audio_id=selected.identity.source_id,
                            codec=quality.codec,
                            lossless=quality.lossless,
                            bit_depth=quality.bit_depth,
                            sample_rate_hz=quality.sample_rate_hz,
                        )
                    lyrics_sources = tuple(
                        (clients[service], candidate.identity.source_id)
                        for service in ("tidal", "deezer")
                        for candidate in report.candidates
                        if candidate.identity.source == service and service in clients
                    )
                    def mark_completed(path, *, track=track, selected=selected, key=key):
                        checkpoint.mark_done(key)
                        if audit is not None:
                            audit.record(
                                "completed",
                                key=key,
                                reference_source=track.source,
                                reference_id=track.source_id,
                                isrc=track.isrc,
                                title=track.title,
                                artist=track.artist,
                                audio_source=selected.identity.source,
                                audio_id=selected.identity.source_id,
                                codec=selected.quality.codec,
                                lossless=selected.quality.lossless,
                                bit_depth=selected.quality.bit_depth,
                                sample_rate_hz=selected.quality.sample_rate_hz,
                                path=path,
                            )

                    def mark_download_failed(
                        path, *, track=track, selected=selected, key=key
                    ):
                        nonlocal failed
                        failed += 1
                        if audit is not None:
                            audit.record(
                                "failed",
                                key=key,
                                reference_source=track.source,
                                reference_id=track.source_id,
                                isrc=track.isrc,
                                title=track.title,
                                artist=track.artist,
                                audio_source=selected.identity.source,
                                audio_id=selected.identity.source_id,
                                codec=selected.quality.codec,
                                lossless=selected.quality.lossless,
                                bit_depth=selected.quality.bit_depth,
                                sample_rate_hz=selected.quality.sample_rate_hz,
                                path=path or None,
                                error_type="download_or_processing",
                            )

                    await main.add_library_track(
                        reference_id=track.source_id,
                        reference_client=reference_client,
                        audio_id=selected.identity.source_id,
                        audio_client=clients[winner],
                        audio_quality=qualities[winner],
                        reference_metadata=track.reference_metadata,
                        album_metadata=track.album_metadata,
                        lyrics_sources=lyrics_sources,
                        completion_callback=mark_completed,
                        failure_callback=mark_download_failed,
                    )

            if not dry_run:
                await main.finish_download_workers()

            summary = ", ".join(
                f"{service}: {count}" for service, count in winners.items()
            ) or "none"
            console.print(
                f"\n[bold green]Library summary:[/bold green] processed={processed}, "
                f"attempted={attempted}, winners=({summary}), failed={failed}, "
                f"duplicates={skipped_duplicate}, resume-skipped={skipped_resume}"
            )
            for service, error in unavailable.items():
                console.print(f"[yellow]{service} unavailable:[/yellow] {error}")
            if audit is not None:
                console.print(f"[dim]Manifest: {audit.path}[/dim]")



async def latest_streamrip_version(verify_ssl: bool = True) -> tuple[str, str | None]:
    """Get the latest streamrip-elvigilante version from the fork's GitHub releases.

    Returns a tuple of (latest_version_tag, release_notes_or_None).
    Never raises — returns the current version on any network or parse error.
    """
    connector_kwargs = get_aiohttp_connector_kwargs(verify_ssl=verify_ssl)
    connector = aiohttp.TCPConnector(**connector_kwargs)
    try:
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.get(
                "https://api.github.com/repos/Np3ir/streamrip-elvigilante/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            ) as resp:
                if resp.status != 200:
                    return __version__, None
                data = await resp.json()
        tag = data.get("tag_name", "").lstrip("v")
        if not tag:
            return __version__, None
        notes = data.get("body") or None
        return tag, notes
    except Exception:
        return __version__, None


if __name__ == "__main__":
    rip()
