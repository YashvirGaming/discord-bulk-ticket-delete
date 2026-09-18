import os
import re
import sys
import time
import asyncio
import platform
from datetime import datetime

import discord
from colorama import init, Fore, Back, Style

init(autoreset=True)

TOKEN_FILE = "bot_tkn.key"
TICKET_PATTERN = re.compile(r"^\d+-[a-zA-Z0-9_][a-zA-Z0-9_-]*$")
CONFIRM_PHRASE = "DELETE ALL TICKETS"
DELETE_DELAY = 0.35
MAX_RETRIES = 3

C_TITLE = Fore.CYAN + Style.BRIGHT
C_OK = Fore.GREEN + Style.BRIGHT
C_WARN = Fore.YELLOW + Style.BRIGHT
C_ERR = Fore.RED + Style.BRIGHT
C_INFO = Fore.WHITE + Style.BRIGHT
C_DIM = Fore.WHITE + Style.DIM
C_ACCENT = Fore.MAGENTA + Style.BRIGHT


def ts():
    return datetime.now().strftime("%H:%M:%S")


def log(level, msg):
    levels = {
        "OK": (C_OK, "OK"),
        "INFO": (C_INFO, "INFO"),
        "WARN": (C_WARN, "WARN"),
        "ERR": (C_ERR, "ERR"),
    }
    color, tag = levels.get(level, (C_INFO, level))
    print(f"{C_DIM}[{ts()}]{Style.RESET_ALL} {color}[{tag:<4}]{Style.RESET_ALL} {msg}")


def hr(char="─", width=70, color=C_DIM):
    print(color + char * width)


def banner():
    hr("═", 70, C_TITLE)
    title = "MEE6 TICKET CLEANUP TOOL"
    print(C_TITLE + title.center(70))
    hr("═", 70, C_TITLE)
    print()


def section(title):
    print()
    hr("─", 70, C_ACCENT)
    print(C_ACCENT + f" {title}")
    hr("─", 70, C_ACCENT)
    print()


def get_masked_input(prompt):
    print(C_INFO + prompt, end="", flush=True)
    system = platform.system()
    buf = []

    if system == "Windows":
        import msvcrt
        while True:
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                print()
                break
            elif ch == "\x03":
                raise KeyboardInterrupt
            elif ch == "\b":
                if buf:
                    buf.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
            else:
                buf.append(ch)
                sys.stdout.write("*")
                sys.stdout.flush()
        return "".join(buf)

    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch in ("\r", "\n"):
                    print()
                    break
                elif ch == "\x03":
                    raise KeyboardInterrupt
                elif ch in ("\x7f", "\b"):
                    if buf:
                        buf.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                else:
                    buf.append(ch)
                    sys.stdout.write("*")
                    sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return "".join(buf)
    except ImportError:
        import getpass
        return getpass.getpass("")


def save_token(token):
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token.strip())
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except (OSError, AttributeError):
        pass


def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        value = f.read().strip()
    return value or None


def prompt_for_token():
    env_token = os.environ.get("DISCORD_BOT_TOKEN")
    if env_token:
        log("OK", "Token loaded from DISCORD_BOT_TOKEN environment variable")
        return env_token.strip()

    existing = load_token()
    if existing:
        log("OK", f"Saved token found in {TOKEN_FILE}")
        masked = existing[:6] + "*" * max(len(existing) - 10, 0) + existing[-4:]
        print(f"{C_DIM}    {masked}")
        choice = input(f"{C_INFO}Use this token? [Y/n]: {Style.RESET_ALL}").strip().lower()
        if choice in ("", "y", "yes"):
            return existing
        log("INFO", "Replacing stored token")

    while True:
        token = get_masked_input("Paste your Discord bot token: ")
        token = token.strip()
        if len(token) < 20:
            log("ERR", "That doesn't look like a valid token, try again")
            continue
        save_token(token)
        log("OK", f"Token saved to {TOKEN_FILE}")
        return token


def is_mee6_ticket(channel):
    if not isinstance(channel, discord.TextChannel):
        return False
    return TICKET_PATTERN.fullmatch(channel.name.strip()) is not None


def ticket_sort_key(channel):
    match = re.match(r"^(\d+)-", channel.name)
    return int(match.group(1)) if match else 10 ** 9


def progress_bar(current, total, width=40):
    filled = int(width * current / total) if total else width
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * current / total) if total else 100
    return f"{C_ACCENT}[{bar}]{Style.RESET_ALL} {pct:3d}%"


class TicketCleanupBot(discord.Client):

    async def on_ready(self):
        banner()
        log("OK", f"Logged in as {C_ACCENT}{self.user}{Style.RESET_ALL} (ID: {self.user.id})")

        guilds = list(self.guilds)
        if not guilds:
            log("ERR", "This bot is not a member of any Discord server")
            await self.close()
            return

        guild = await self.select_guild(guilds)

        section("FETCHING CHANNELS")
        try:
            channels = await guild.fetch_channels()
        except discord.Forbidden:
            log("ERR", "Missing permission to view server channels")
            await self.close()
            return
        except discord.HTTPException as e:
            log("ERR", f"Discord API error while fetching channels: {e}")
            await self.close()
            return

        log("OK", f"Received {len(channels)} channels from {guild.name}")

        text_channels = [c for c in channels if isinstance(c, discord.TextChannel)]
        tickets = sorted((c for c in text_channels if is_mee6_ticket(c)), key=ticket_sort_key)

        section(f"SCAN RESULTS")
        log("INFO", f"Text channels scanned : {len(text_channels)}")
        log("INFO", f"Ticket channels found : {C_ACCENT}{len(tickets)}{Style.RESET_ALL}")

        if not tickets:
            log("WARN", "No matching ticket channels were found")
            print(f"{C_DIM}    Expected pattern examples: #105-user_name, #108-joe-random1815")
            await self.close()
            return

        section("PREVIEW")
        for i, channel in enumerate(tickets, 1):
            category = channel.category.name if channel.category else "ROOT"
            print(
                f"{C_DIM}[{i:03}]{Style.RESET_ALL} "
                f"{C_INFO}#{channel.name:<28}{Style.RESET_ALL} "
                f"{C_DIM}id={channel.id}  category={category}"
            )

        section("CONFIRMATION")
        print(f"{Back.RED}{Fore.WHITE}{Style.BRIGHT} WARNING {Style.RESET_ALL} "
              f"{C_WARN}This will permanently delete {len(tickets)} channel(s). This cannot be undone.")
        print()

        confirmation = await asyncio.to_thread(
            input, f"{C_INFO}Type exactly '{CONFIRM_PHRASE}' to continue: {Style.RESET_ALL}"
        )

        if confirmation.strip() != CONFIRM_PHRASE:
            log("WARN", "Confirmation did not match, nothing was deleted")
            await self.close()
            return

        await self.delete_tickets(tickets)
        await self.close()

    async def select_guild(self, guilds):
        if len(guilds) == 1:
            guild = guilds[0]
            log("OK", f"Server: {C_ACCENT}{guild.name}{Style.RESET_ALL} ({guild.id})")
            return guild

        section("SELECT SERVER")
        for i, g in enumerate(guilds, 1):
            print(f"{C_ACCENT}[{i}]{Style.RESET_ALL} {g.name} {C_DIM}({g.id})")
        print()

        while True:
            raw = await asyncio.to_thread(input, f"{C_INFO}Select server number: {Style.RESET_ALL}")
            try:
                choice = int(raw.strip())
                if 1 <= choice <= len(guilds):
                    return guilds[choice - 1]
            except ValueError:
                pass
            log("ERR", "Invalid selection, try again")

    async def delete_tickets(self, tickets):
        section("DELETING CHANNELS")
        deleted = 0
        failed = 0
        already_gone = 0
        total = len(tickets)

        for index, channel in enumerate(tickets, 1):
            bar = progress_bar(index - 1, total)
            print(f"\r{bar} {C_DIM}deleting #{channel.name:<28}{Style.RESET_ALL}", end="", flush=True)

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    await channel.delete(reason="Bulk cleanup of MEE6 ticket channels")
                    deleted += 1
                    break
                except discord.NotFound:
                    already_gone += 1
                    break
                except discord.Forbidden:
                    failed += 1
                    print()
                    log("ERR", f"#{channel.name}: missing Manage Channels permission")
                    break
                except discord.HTTPException as e:
                    retry_after = getattr(e, "retry_after", None)
                    if retry_after and attempt < MAX_RETRIES:
                        print()
                        log("WARN", f"Rate limited, retrying #{channel.name} in {retry_after:.1f}s")
                        await asyncio.sleep(retry_after)
                        continue
                    failed += 1
                    print()
                    log("ERR", f"#{channel.name}: {e}")
                    break

            await asyncio.sleep(DELETE_DELAY)

        bar = progress_bar(total, total)
        print(f"\r{bar} {C_OK}done{' ' * 30}")

        section("SUMMARY")
        print(f"{C_INFO}Found            {Style.RESET_ALL}{total}")
        print(f"{C_OK}Deleted          {Style.RESET_ALL}{deleted}")
        print(f"{C_WARN}Already deleted  {Style.RESET_ALL}{already_gone}")
        print(f"{C_ERR if failed else C_DIM}Failed           {Style.RESET_ALL}{failed}")
        print()
        hr("═", 70, C_TITLE)


def main():
    banner()
    token = prompt_for_token()

    intents = discord.Intents.default()
    intents.guilds = True

    client = TicketCleanupBot(intents=intents)

    try:
        client.run(token, log_handler=None)
    except discord.LoginFailure:
        log("ERR", "Login failed, the token is invalid")
        try:
            os.remove(TOKEN_FILE)
        except OSError:
            pass
    except KeyboardInterrupt:
        log("WARN", "Interrupted by user")
    except Exception as e:
        log("ERR", f"Unexpected error: {e}")


if __name__ == "__main__":
    main()