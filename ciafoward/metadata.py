"""
GBA Game Metadata Resolver & Automatic Cover Art / Title Screen Downloader.
Supports:
  1. Built-in GBA Serial & Maker Code database
  2. No-Intro filename & ROM header parsing
  3. Online Libretro No-Intro DAT lookup (by CRC32 & Serial)
  4. Online Libretro Thumbnail CDN & GitHub mirror downloading (Boxart, Title Screen, Snap)
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .consoles import SYSTEM_SPECS
from .gba import GBARomInfo

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Official Nintendo 2-character Maker Code -> Publisher Name table
MAKER_CODES: Dict[str, str] = {
    "01": "Nintendo",
    "08": "Capcom",
    "0A": "Jaleco",
    "13": "Electronic Arts",
    "18": "Hudson Soft",
    "20": "Destination Software",
    "28": "Kemco",
    "29": "SETA",
    "36": "Codemasters",
    "41": "Ubisoft",
    "4F": "Eidos Interactive",
    "4Q": "Disney Interactive",
    "4Z": "Crave Entertainment",
    "52": "Activision",
    "54": "Konami",
    "5D": "Midway",
    "5G": "Majesco",
    "5H": "3DO",
    "60": "Titus",
    "64": "LucasArts",
    "69": "Electronic Arts",
    "6S": "TDK Mediactive",
    "70": "Atari",
    "78": "THQ",
    "7D": "Vivendi Universal",
    "8J": "Kadokawa Shoten",
    "8P": "Sega",
    "99": "Marvelous",
    "A4": "Konami",
    "A7": "Takara",
    "AF": "Namco",
    "B2": "Bandai",
    "B4": "Enix",
    "C0": "Taito",
    "C3": "Square",
    "C8": "Koei",
    "CE": "Pony Canyon",
    "D9": "Banpresto",
    "DA": "Tomy",
    "E5": "Epoch",
    "E8": "Asmik Ace",
    "E9": "Natsume",
    "EB": "Atlus",
    "FP": "Mastiff",
    "G8": "Success",
    "G9": "Square Enix",
    "GD": "Square Enix",
    "GN": "Oxygen Games",
    "GY": "The Game Factory",
    "H5": "Level-5",
    "MV": "Marvelous",
    "NK": "Neko Entertainment",
    "PG": "Polygon Magic",
    "WR": "Warner Bros.",
}

# Built-in database of notable GBA Game Code prefixes (3-char) -> (Title, Publisher, Year, No-Intro USA Name)
BUILTIN_GBA_CATALOG: Dict[str, Tuple[str, str, str, str]] = {
    "AWR": ("Advance Wars", "Nintendo", "2001", "Advance Wars (USA) (Rev 1)"),
    "B2W": ("Advance Wars 2: Black Hole Rising", "Nintendo", "2003", "Advance Wars 2 - Black Hole Rising (USA)"),
    "AXV": ("Pokémon Ruby Version", "Nintendo", "2003", "Pokemon - Ruby Version (USA, Europe) (Rev 2)"),
    "AXP": ("Pokémon Sapphire Version", "Nintendo", "2003", "Pokemon - Sapphire Version (USA, Europe) (Rev 2)"),
    "BPE": ("Pokémon Emerald Version", "Nintendo", "2005", "Pokemon - Emerald Version (USA, Europe)"),
    "BPR": ("Pokémon FireRed Version", "Nintendo", "2004", "Pokemon - FireRed Version (USA, Europe) (Rev 1)"),
    "BPG": ("Pokémon LeafGreen Version", "Nintendo", "2004", "Pokemon - LeafGreen Version (USA, Europe) (Rev 1)"),
    "B24": ("Pokémon Mystery Dungeon: Red Rescue Team", "Nintendo", "2006", "Pokemon Mystery Dungeon - Red Rescue Team (USA, Australia)"),
    "AMT": ("Metroid Fusion", "Nintendo", "2002", "Metroid Fusion (USA)"),
    "BMX": ("Metroid: Zero Mission", "Nintendo", "2004", "Metroid - Zero Mission (USA)"),
    "AZL": ("The Legend of Zelda: A Link to the Past", "Nintendo", "2002", "Legend of Zelda, The - A Link to the Past & Four Swords (USA)"),
    "BZM": ("The Legend of Zelda: The Minish Cap", "Nintendo", "2005", "Legend of Zelda, The - The Minish Cap (USA)"),
    "AMA": ("Super Mario Advance", "Nintendo", "2001", "Super Mario Advance (USA, Europe)"),
    "A3A": ("Super Mario World: Super Mario Advance 2", "Nintendo", "2002", "Super Mario World - Super Mario Advance 2 (USA, Australia)"),
    "A2Y": ("Yoshi's Island: Super Mario Advance 3", "Nintendo", "2002", "Yoshi's Island - Super Mario Advance 3 (USA)"),
    "AX4": ("Super Mario Advance 4: Super Mario Bros. 3", "Nintendo", "2003", "Super Mario Advance 4 - Super Mario Bros. 3 (USA, Australia) (Rev 1)"),
    "AMK": ("Mario Kart: Super Circuit", "Nintendo", "2001", "Mario Kart - Super Circuit (USA)"),
    "A88": ("Mario & Luigi: Superstar Saga", "Nintendo", "2003", "Mario & Luigi - Superstar Saga (USA)"),
    "AE7": ("Fire Emblem", "Nintendo", "2003", "Fire Emblem (USA, Australia)"),
    "BE8": ("Fire Emblem: The Sacred Stones", "Nintendo", "2005", "Fire Emblem - The Sacred Stones (USA, Australia)"),
    "AF6": ("Fire Emblem: The Binding Blade", "Nintendo", "2002", "Fire Emblem - Fuuin no Tsurugi (Japan)"),
    "AGS": ("Golden Sun", "Nintendo", "2001", "Golden Sun (USA, Europe)"),
    "AGF": ("Golden Sun: The Lost Age", "Nintendo", "2003", "Golden Sun - The Lost Age (USA, Europe)"),
    "A3U": ("Mother 3", "Nintendo", "2006", "Mother 3 (Japan)"),
    "AFX": ("Final Fantasy Tactics Advance", "Square Enix", "2003", "Final Fantasy Tactics Advance (USA, Australia)"),
    "BZ4": ("Final Fantasy IV Advance", "Square Enix", "2005", "Final Fantasy IV Advance (USA)"),
    "BZ5": ("Final Fantasy V Advance", "Square Enix", "2006", "Final Fantasy V Advance (USA)"),
    "BZ6": ("Final Fantasy VI Advance", "Square Enix", "2007", "Final Fantasy VI Advance (USA)"),
    "A7K": ("Kirby: Nightmare in Dream Land", "Nintendo", "2002", "Kirby - Nightmare in Dream Land (USA)"),
    "B8K": ("Kirby & The Amazing Mirror", "Nintendo", "2004", "Kirby & The Amazing Mirror (USA)"),
    "AA2": ("Wario Land 4", "Nintendo", "2001", "Wario Land 4 (USA, Europe)"),
    "AWZ": ("WarioWare, Inc.: Mega Microgame$!", "Nintendo", "2003", "WarioWare, Inc. - Mega Microgame$! (USA)"),
    "RZW": ("WarioWare: Twisted!", "Nintendo", "2005", "WarioWare - Twisted! (USA)"),
    "ACV": ("Castlevania: Circle of the Moon", "Konami", "2001", "Castlevania - Circle of the Moon (USA)"),
    "A2C": ("Castlevania: Harmony of Dissonance", "Konami", "2002", "Castlevania - Harmony of Dissonance (USA)"),
    "A2O": ("Castlevania: Aria of Sorrow", "Konami", "2003", "Castlevania - Aria of Sorrow (USA)"),
    "A5F": ("F-Zero: Maximum Velocity", "Nintendo", "2001", "F-Zero - Maximum Velocity (USA, Europe)"),
    "BFZ": ("F-Zero: GP Legend", "Nintendo", "2004", "F-Zero - GP Legend (USA)"),
    "BKC": ("Donkey Kong Country", "Nintendo", "2003", "Donkey Kong Country (USA)"),
    "B2D": ("Donkey Kong Country 2", "Nintendo", "2004", "Donkey Kong Country 2 (USA, Australia)"),
    "B3D": ("Donkey Kong Country 3", "Nintendo", "2005", "Donkey Kong Country 3 (USA, Australia)"),
    "AZU": ("Sonic Advance", "Sega", "2002", "Sonic Advance (USA)"),
    "A2N": ("Sonic Advance 2", "Sega", "2003", "Sonic Advance 2 (USA)"),
    "B3S": ("Sonic Advance 3", "Sega", "2004", "Sonic Advance 3 (USA)"),
    "AZ2": ("Mega Man Zero", "Capcom", "2002", "Mega Man Zero (USA, Europe)"),
    "A6Z": ("Mega Man Zero 2", "Capcom", "2003", "Mega Man Zero 2 (USA)"),
    "BZ3": ("Mega Man Zero 3", "Capcom", "2004", "Mega Man Zero 3 (USA)"),
    "B4Z": ("Mega Man Zero 4", "Capcom", "2005", "Mega Man Zero 4 (USA)"),
    "ARE": ("Mega Man Battle Network", "Capcom", "2001", "Mega Man Battle Network (USA)"),
    "AE2": ("Mega Man Battle Network 2", "Capcom", "2002", "Mega Man Battle Network 2 (USA)"),
    "U3I": ("Boktai: The Sun Is in Your Eyes", "Konami", "2003", "Boktai - The Sun Is in Your Eyes (USA)"),
    "AZX": ("Street Fighter Alpha 3", "Capcom", "2002", "Street Fighter Alpha 3 (USA)"),
    "ASF": ("Super Street Fighter II Turbo Revival", "Capcom", "2001", "Super Street Fighter II Turbo Revival (USA)"),
    "BRI": ("Rhythm Tengoku", "Nintendo", "2006", "Rhythm Tengoku (Japan)"),
    "A6T": ("Tactics Ogre: The Knight of Lodis", "Atlus", "2002", "Tactics Ogre - The Knight of Lodis (USA)"),
    "AVS": ("Sword of Mana", "Square Enix", "2003", "Sword of Mana (USA, Australia)"),
    "AGY": ("Phoenix Wright: Ace Attorney", "Capcom", "2001", "Gyakuten Saiban (Japan)"),
}

GENRE_LABELS: Dict[str, Tuple[str, str]] = {
    "rpg": ("RPG", "奇幻角色扮演 (RPG)"),
    "slg": ("SLG", "策略战棋 (SLG)"),
    "act": ("ACT", "横版动作闯关 (ACT)"),
    "arpg": ("ARPG", "银河城/动作RPG (ARPG)"),
    "avg": ("AVG", "冒险探索/解谜 (AVG)"),
    "ftg": ("FTG", "街机格斗对战 (FTG)"),
    "rac": ("RAC", "极速赛车竞速 (RAC)"),
    "puz": ("PUZ", "益智休闲/体育 (PUZ)"),
    "classic": ("GBA", "经典主机原色 (CLASSIC)"),
}

GBA_GENRE_BY_PREFIX: Dict[str, str] = {
    # SLG (Tactical / Strategy)
    "AWR": "slg",
    "B2W": "slg",
    "AE7": "slg",
    "BE8": "slg",
    "AF6": "slg",
    "AFX": "slg",
    "A6T": "slg",
    "A9O": "slg",
    "A9B": "slg",
    "B2R": "slg",
    "B3R": "slg",
    "A3W": "slg",
    "AZT": "slg",
    # RPG (Turn-Based & Fantasy JRPG)
    "AXV": "rpg",
    "AXP": "rpg",
    "BPE": "rpg",
    "BPR": "rpg",
    "BPG": "rpg",
    "B24": "rpg",
    "AGS": "rpg",
    "AGF": "rpg",
    "A3U": "rpg",
    "BZ4": "rpg",
    "BZ5": "rpg",
    "BZ6": "rpg",
    "A88": "rpg",
    "B8P": "rpg",
    "ALR": "rpg",
    "ATD": "rpg",
    "BDQ": "rpg",
    "B54": "rpg",
    "B6P": "rpg",
    "BYG": "rpg",
    "AMH": "rpg",
    # ARPG (Metroidvania & Action RPG)
    "AMT": "arpg",
    "BMX": "arpg",
    "ACV": "arpg",
    "A2C": "arpg",
    "A2O": "arpg",
    "ARE": "arpg",
    "AE2": "arpg",
    "A3X": "arpg",
    "B4W": "arpg",
    "B5W": "arpg",
    "BR5": "arpg",
    "BR6": "arpg",
    "U3I": "arpg",
    "U32": "arpg",
    "U33": "arpg",
    "AVS": "arpg",
    "ASS": "arpg",
    "AU2": "arpg",
    "B8S": "arpg",
    # AVG (Zelda / Adventure / Visual Novel)
    "AZL": "avg",
    "BZM": "avg",
    "AGY": "avg",
    "A2G": "avg",
    "B3G": "avg",
    "AHM": "avg",
    "B8H": "avg",
    "A4N": "avg",
    "BFM": "avg",
    # ACT (Platformer & Run-and-Gun Action)
    "AMA": "act",
    "A3A": "act",
    "A2Y": "act",
    "AX4": "act",
    "A7K": "act",
    "B8K": "act",
    "AA2": "act",
    "BKC": "act",
    "B2D": "act",
    "B3D": "act",
    "AZU": "act",
    "A2N": "act",
    "B3S": "act",
    "AZ2": "act",
    "A6Z": "act",
    "BZ3": "act",
    "B4Z": "act",
    "B6M": "act",
    "ANJ": "act",
    "B3T": "act",
    "AGJ": "act",
    "ACT": "act",
    "ASL": "act",
    "BKL": "act",
    # FTG (Fighting & Beat 'em Up)
    "AZX": "ftg",
    "ASF": "ftg",
    "AKO": "ftg",
    "B2K": "ftg",
    "ATK": "ftg",
    "AGG": "ftg",
    "BDZ": "ftg",
    "B3B": "ftg",
    "ANR": "ftg",
    "AFG": "ftg",
    # RAC (Racing)
    "AMK": "rac",
    "A5F": "rac",
    "BFZ": "rac",
    "BFT": "rac",
    "AGT": "rac",
    "AKW": "rac",
    "BNF": "rac",
    # PUZ (Puzzle, Rhythm, Party & Sports)
    "AWZ": "puz",
    "RZW": "puz",
    "BRI": "puz",
    "B8M": "puz",
    "ACR": "puz",
    "APY": "puz",
    "AT3": "puz",
    "BMG": "puz",
    "BMT": "puz",
    "AFB": "puz",
}


def detect_game_genre(
    game_code: str = "",
    title: str = "",
    canonical_name: str = "",
    internal_title: str = "",
    override_genre: Optional[str] = None,
) -> str:
    """
    Automatically classify a game into one of the visual template genres:
    `("rpg", "slg", "act", "arpg", "avg", "ftg", "rac", "puz", "classic")`.
    """
    if override_genre and override_genre.lower() not in ("auto", ""):
        g = override_genre.lower()
        if g in GENRE_LABELS:
            return g

    prefix3 = (game_code or "")[:3].upper()
    if prefix3 in GBA_GENRE_BY_PREFIX:
        return GBA_GENRE_BY_PREFIX[prefix3]

    combined = f"{title} {canonical_name} {internal_title}".lower()

    # Check keyword patterns in priority order
    slg_kw = (
        "advance wars", "fire emblem", "tactics", "super robot", "srw",
        "ogre", "yu-gi-oh", "yugioh", "sangokushi", "nobunaga", "conquest",
        "shining force", "rebelstar", "onimus", "sd gundam", "wars", "strategy",
    )
    if any(k in combined for k in slg_kw):
        return "slg"

    arpg_kw = (
        "castlevania", "metroid", "battle network", "rockman exe", "boktai",
        "sword of mana", "seiken densetsu", "shining soul", "kingdom hearts",
        "sigma star", "baldur", "diablo", "summon night",
    )
    if any(k in combined for k in arpg_kw):
        return "arpg"

    avg_kw = (
        "zelda", "minish cap", "link to the past", "link's awakening",
        "ace attorney", "gyakuten saiban", "harvest moon", "mineral town",
        "hamtaro", "detective", "broken sword", "another code", "hotel dusk",
    )
    if any(k in combined for k in avg_kw):
        return "avg"

    ftg_kw = (
        "street fighter", "king of fighters", "kof", "tekken", "guilty gear",
        "dragon ball", "mortal kombat", "final fight", "double dragon",
        "sonic battle", "brawler", "fighting", "fighter", "gekido", "hajime no ippo",
    )
    if any(k in combined for k in ftg_kw):
        return "ftg"

    rac_kw = (
        "mario kart", "kart", "f-zero", "f zero", "gt advance", "krazy racers",
        "need for speed", "v-rally", "top gear", "crazy taxi", "motogp",
        "racing", "racer", "rally", "out run", "outrun", "ridge racer",
    )
    if any(k in combined for k in rac_kw):
        return "rac"

    puz_kw = (
        "warioware", "wario ware", "rhythm", "puyo", "tetris", "chuchu",
        "mario party", "puzzle", "dr. mario", "kururin", "monkey ball",
        "mario golf", "mario tennis", "power tennis", "advance tour",
        "pinball", "bomberman", "dodgeball", "nekketsu", "fifa", "madden",
        "tony hawk", "fire pro", "baseball", "basketball", "soccer", "bowling",
    )
    if any(k in combined for k in puz_kw):
        return "puz"

    rpg_kw = (
        "pokemon", "pokémon", "golden sun", "final fantasy", "mother ",
        "earthbound", "mario & luigi", "breath of fire", "dragon quest",
        "dragon warrior", "tales of", "lufia", "lunar", "riviera", "yggdra",
        "magical vacation", "oriental blue", "shin megami", "demikids",
        "medabots", "robopon", "phantasy star", "chrono", "fire emblem",
    )
    if any(k in combined for k in rpg_kw):
        return "rpg"

    act_kw = (
        "mario", "sonic", "kirby", "wario land", "donkey kong", "mega man",
        "rockman", "astro boy", "ninja", "contra", "metal slug", "gunstar",
        "klonoa", "rayman", "crash", "spyro", "starfy", "stafy", "castlevania",
    )
    if any(k in combined for k in act_kw):
        return "act"

    return "classic"

# Characters that Libretro Thumbnails replaces with '_' in PNG filenames
LIBRETRO_SANITIZE_REGEX = re.compile(r'[&*/:`<>?\\|"]')

LIBRETRO_THUMBNAIL_BASES = [
    "https://thumbnails.libretro.com/Nintendo%20-%20Game%20Boy%20Advance",
    "https://raw.githubusercontent.com/libretro-thumbnails/Nintendo_-_Game_Boy_Advance/master",
]

LIBRETRO_NOINTRO_DAT_URL = (
    "https://raw.githubusercontent.com/libretro/libretro-database/master/"
    "metadat/no-intro/Nintendo%20-%20Game%20Boy%20Advance.dat"
)
LIBRETRO_YEAR_DAT_URL = (
    "https://raw.githubusercontent.com/libretro/libretro-database/master/"
    "metadat/releaseyear/Nintendo%20-%20Game%20Boy%20Advance.dat"
)
LIBRETRO_PUBLISHER_DAT_URL = (
    "https://raw.githubusercontent.com/libretro/libretro-database/master/"
    "metadat/publisher/Nintendo%20-%20Game%20Boy%20Advance.dat"
)


@dataclasses.dataclass
class GameMetadata:
    """Resolved metadata and artwork paths for a game ROM."""

    short_title: str
    long_title: str
    publisher: str
    release_year: str
    canonical_name: str
    game_code: str
    region: str
    boxart_path: Optional[str] = None
    title_screen_path: Optional[str] = None
    boxart_url: Optional[str] = None
    title_screen_url: Optional[str] = None
    source: str = "header"
    system: str = "gba"
    system_name: str = "Game Boy Advance"
    genre: str = "auto"
    genre_name: str = ""

    def __post_init__(self) -> None:
        if not self.genre or self.genre == "auto":
            self.genre = detect_game_genre(
                game_code=self.game_code,
                title=self.short_title,
                canonical_name=self.canonical_name,
            )
        if not self.genre_name:
            self.genre_name = GENRE_LABELS.get(self.genre, GENRE_LABELS["classic"])[1]


def _get_libretro_urls_for_system(system: str) -> Tuple[List[str], str, str, str]:
    """Return `(thumbnail_bases, nointro_dat_url, year_dat_url, publisher_dat_url)` for `system`."""
    spec = SYSTEM_SPECS.get(system.lower(), SYSTEM_SPECS["gba"])
    db_name = str(spec["libretro_db"])
    encoded_db = urllib.parse.quote(db_name)
    repo_db = db_name.replace(" - ", "_-_").replace(" ", "_")
    thumb_bases = [
        f"https://thumbnails.libretro.com/{encoded_db}",
        f"https://raw.githubusercontent.com/libretro-thumbnails/{repo_db}/master",
    ]
    nointro_url = f"https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/no-intro/{encoded_db}.dat"
    year_url = f"https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/releaseyear/{encoded_db}.dat"
    pub_url = f"https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/publisher/{encoded_db}.dat"
    return thumb_bases, nointro_url, year_url, pub_url


def get_cache_dir() -> Path:
    """Return the cache directory for ciafoward metadata, templates, and covers."""
    env_dir = os.environ.get("CIAFOWARD_CACHE_DIR")
    if env_dir:
        p = Path(env_dir).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    local_cache = Path(__file__).resolve().parent.parent / ".cache"
    try:
        local_cache.mkdir(parents=True, exist_ok=True)
        return local_cache
    except Exception:
        p = Path.home() / ".cache" / "ciafoward"
        p.mkdir(parents=True, exist_ok=True)
        return p


def sanitize_libretro_name(name: str) -> str:
    """Sanitize a No-Intro ROM name according to Libretro Thumbnail naming rules."""
    return LIBRETRO_SANITIZE_REGEX.sub("_", name.strip())


def clean_display_title_from_nointro(nointro_name: str) -> str:
    """
    Convert a No-Intro name like 'Advance Wars (USA) (Rev 1)' or
    'Legend of Zelda, The - The Minish Cap (USA)' into a clean display title.
    """
    # Strip parenthetical tags like (USA), (Rev 1), [!]
    base = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", nointro_name).strip()
    # Handle ', The' inversion
    if ", The - " in base:
        parts = base.split(", The - ", 1)
        base = f"The {parts[0]}: {parts[1]}"
    elif base.endswith(", The"):
        base = "The " + base[:-5]
    # Replace ' - ' with ': ' for cleaner 3DS display
    if " - " in base:
        base = base.replace(" - ", ": ", 1)
    return base.strip()


def candidate_nointro_names(
    file_path: str,
    rom_info: GBARomInfo,
    db_canonical_name: Optional[str] = None,
) -> List[str]:
    """
    Generate ordered candidate No-Intro names for downloading thumbnails from Libretro.
    """
    candidates: List[str] = []

    def add(name: Optional[str]) -> None:
        if not name:
            return
        name = name.strip()
        if name and name not in candidates:
            candidates.append(name)
        # Also add variant without (Rev X) or (Beta) since Libretro Thumbnails often stores
        # cover art under the base release name e.g. 'Advance Wars (USA)' as well as '(Rev 1)'
        stripped_rev = re.sub(r"\s*\((?:Rev\s*\w+|v\d[\d\.]*|Beta[^\)]*|Proto[^\)]*)\)", "", name).strip()
        if stripped_rev and stripped_rev not in candidates:
            candidates.append(stripped_rev)

    # 1. Exact database match if found
    add(db_canonical_name)

    # 2. Filename stem (if user has a No-Intro named file like 'Advance Wars (USA) (Rev 1).gba')
    stem = Path(file_path).stem
    if "(" in stem and ")" in stem:
        add(stem)

    # 3. Built-in GBA catalog match by 3-char Game Code prefix
    prefix3 = rom_info.game_code[:3].upper()
    if rom_info.system == "gba" and prefix3 in BUILTIN_GBA_CATALOG:
        title, _, _, builtin_nointro = BUILTIN_GBA_CATALOG[prefix3]
        add(builtin_nointro)
        add(f"{title} ({rom_info.region})")
        add(f"{title} (USA)")
        add(f"{title} (USA, Europe)")
        add(f"{title} (Europe)")
        add(f"{title} (Japan)")

    # 4. Fallback from filename stem or internal header title
    clean_stem = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", stem).strip()
    if clean_stem:
        add(f"{clean_stem} ({rom_info.region})")
        add(f"{clean_stem} (USA)")
        add(f"{clean_stem} (Japan)")
        add(f"{clean_stem} (USA, Europe)")
        add(f"{clean_stem} (World)")
        add(clean_stem)
        # Also try hyphenated Japanese suffixes (e.g. 'Dodgeball Bu' -> 'Dodgeball-bu')
        hyph_stem = re.sub(r"\s+(Bu|Kun|Hen|Den)\b", lambda m: "-" + m.group(1).lower(), clean_stem)
        if hyph_stem != clean_stem:
            add(f"{hyph_stem} ({rom_info.region})")
            add(f"{hyph_stem} (Japan)")

    if rom_info.banner_title:
        add(f"{rom_info.banner_title} ({rom_info.region})")
        add(f"{rom_info.banner_title} (USA)")
        add(f"{rom_info.banner_title} (Japan)")
        add(f"{rom_info.banner_title} (Europe)")
        add(rom_info.banner_title)

    if rom_info.internal_title:
        title_cased = rom_info.internal_title.title()
        add(f"{title_cased} ({rom_info.region})")
        add(f"{title_cased} (USA)")

    return candidates


def _http_get_bytes(url: str, timeout: float = 12.0) -> Optional[bytes]:
    """Perform an HTTPS GET request and return bytes on HTTP 200, or None on error."""
    if not url.startswith("https://"):
        raise ValueError(f"Only HTTPS URLs are allowed: {url}")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ciafoward/1.0 (Nintendo 3DS GBA VC Packager)",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return resp.read()
    except Exception:
        return None
    return None


def _parse_libretro_dat(dat_text: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Parse Libretro No-Intro .dat text and return:
      - crc_to_name: { "XXXXXXXX": "Canonical Name" }
      - serial_to_name: { "AWRE": "Canonical Name" }
    """
    crc_to_name: Dict[str, str] = {}
    serial_to_name: Dict[str, str] = {}

    game_blocks = re.findall(r"game\s*\((.*?)\n\)", dat_text, flags=re.DOTALL)
    for block in game_blocks:
        name_m = re.search(r'name\s+"([^"]+)"', block)
        if not name_m:
            continue
        name = name_m.group(1)

        serial_m = re.search(r'serial\s+"([^"]+)"', block)
        if serial_m:
            serial = serial_m.group(1).strip().upper()
            # Keep first or non-Rev base name if multiple revisions share serial
            if serial not in serial_to_name:
                serial_to_name[serial] = name

        for crc_m in re.finditer(r"\bcrc\s+([0-9A-Fa-f]{8})\b", block):
            crc_to_name[crc_m.group(1).upper()] = name

    return crc_to_name, serial_to_name


def _parse_libretro_kv_dat(dat_text: str, field_name: str) -> Dict[str, str]:
    """
    Parse Libretro metadata .dat (such as releaseyear or publisher) into {name: value}.
    """
    result: Dict[str, str] = {}
    game_blocks = re.findall(r"game\s*\((.*?)\n\)", dat_text, flags=re.DOTALL)
    for block in game_blocks:
        name_m = re.search(r'(?:name|comment)\s+"([^"]+)"', block)
        val_m = re.search(rf'{field_name}\s+"?([^"\n\r\)]+)"?', block)
        if name_m and val_m:
            result[name_m.group(1).strip()] = val_m.group(1).strip()
    return result


def lookup_online_libretro_db(
    rom_info: GBARomInfo,
    cache_dir: Path,
    timeout: float = 10.0,
) -> Optional[Dict[str, str]]:
    """
    Look up the ROM by CRC32 or Serial in the Libretro No-Intro database for `rom_info.system`
    (cached locally as JSON).
    """
    sys_key = (rom_info.system or "gba").lower()
    db_cache_file = cache_dir / f"{sys_key}_libretro_index.json"
    index_data: Optional[Dict[str, Dict[str, str]]] = None

    if db_cache_file.exists():
        try:
            index_data = json.loads(db_cache_file.read_text(encoding="utf-8"))
        except Exception:
            index_data = None

    if index_data is None:
        _, nointro_url, year_url, pub_url = _get_libretro_urls_for_system(sys_key)
        raw_dat = _http_get_bytes(nointro_url, timeout=timeout)
        if raw_dat:
            dat_text = raw_dat.decode("utf-8", errors="replace")
            crc_map, serial_map = _parse_libretro_dat(dat_text)
            year_map: Dict[str, str] = {}
            pub_map: Dict[str, str] = {}

            raw_year = _http_get_bytes(year_url, timeout=timeout)
            if raw_year:
                year_map = _parse_libretro_kv_dat(
                    raw_year.decode("utf-8", errors="replace"), "releaseyear"
                )
            raw_pub = _http_get_bytes(pub_url, timeout=timeout)
            if raw_pub:
                pub_map = _parse_libretro_kv_dat(
                    raw_pub.decode("utf-8", errors="replace"), "publisher"
                )

            index_data = {
                "crc": crc_map,
                "serial": serial_map,
                "year": year_map,
                "publisher": pub_map,
            }
            try:
                db_cache_file.write_text(json.dumps(index_data), encoding="utf-8")
            except Exception:
                pass

    if not index_data:
        return None

    crc_map = index_data.get("crc", {})
    serial_map = index_data.get("serial", {})
    year_map = index_data.get("year", {})
    pub_map = index_data.get("publisher", {})

    canonical = (
        crc_map.get(rom_info.lookup_crc32_hex)
        or crc_map.get(rom_info.crc32_hex)
        or serial_map.get(rom_info.game_code.upper())
    )
    if not canonical:
        return None

    return {
        "canonical_name": canonical,
        "release_year": year_map.get(canonical, ""),
        "publisher": pub_map.get(canonical, ""),
    }


def download_libretro_thumbnail(
    candidates: List[str],
    category: str,
    cache_dir: Path,
    timeout: float = 10.0,
    system: str = "gba",
) -> Tuple[Optional[str], Optional[str]]:
    """
    Attempt to download a PNG thumbnail for any of `candidates` from Libretro Thumbnails.
    `category` is one of: "Named_Boxarts", "Named_Titles", "Named_Snaps".
    Returns (local_file_path, source_url) or (None, None).
    """
    sys_key = (system or "gba").lower()
    covers_dir = (
        cache_dir / "covers" / category
        if sys_key == "gba"
        else cache_dir / "covers" / sys_key / category
    )
    covers_dir.mkdir(parents=True, exist_ok=True)
    thumb_bases, _, _, _ = _get_libretro_urls_for_system(sys_key)

    for candidate in candidates:
        safe_name = sanitize_libretro_name(candidate)
        cached_file = covers_dir / f"{safe_name}.png"
        if cached_file.exists() and cached_file.stat().st_size > 64:
            data = cached_file.read_bytes()
            if data.startswith(PNG_MAGIC):
                return str(cached_file), f"cache://{sys_key}/{category}/{safe_name}.png"

        encoded_name = urllib.parse.quote(f"{safe_name}.png")
        for base_url in thumb_bases:
            url = f"{base_url}/{category}/{encoded_name}"
            img_bytes = _http_get_bytes(url, timeout=timeout)
            if img_bytes and img_bytes.startswith(PNG_MAGIC):
                try:
                    cached_file.write_bytes(img_bytes)
                except Exception:
                    pass
                return str(cached_file), url

    return None, None


def resolve_game_metadata(
    rom_info: GBARomInfo,
    custom_title: Optional[str] = None,
    custom_publisher: Optional[str] = None,
    custom_year: Optional[str] = None,
    custom_cover: Optional[str] = None,
    custom_title_screen: Optional[str] = None,
    custom_genre: Optional[str] = None,
    download_artwork: bool = True,
    use_online_db: bool = True,
    timeout: float = 10.0,
) -> GameMetadata:
    """
    Resolve full game metadata (title, publisher, release year) and automatically download
    cover art (boxart) and title screen image if available.
    """
    cache_dir = get_cache_dir()
    sys_key = (rom_info.system or "gba").lower()
    spec = SYSTEM_SPECS.get(sys_key, SYSTEM_SPECS["gba"])
    default_years = {
        "gba": "2001",
        "gb": "1989",
        "gbc": "1998",
        "nes": "1985",
        "snes": "1991",
        "md": "1989",
        "nds": "2004",
    }
    prefix3 = rom_info.game_code[:3].upper()
    publisher = MAKER_CODES.get(
        rom_info.maker_code.upper(),
        rom_info.banner_publisher or str(spec["default_publisher"]),
    )
    release_year = default_years.get(sys_key, "2001")
    display_title = ""
    canonical_name = ""
    source = "header"

    # 1. Check built-in catalog first (for GBA)
    if sys_key == "gba" and prefix3 in BUILTIN_GBA_CATALOG:
        b_title, b_pub, b_year, b_nointro = BUILTIN_GBA_CATALOG[prefix3]
        display_title = b_title
        publisher = b_pub
        release_year = b_year
        canonical_name = b_nointro
        source = "builtin-catalog"

    # 2. Check filename if No-Intro formatted
    stem = Path(rom_info.file_path).stem
    if "(" in stem and ")" in stem:
        canonical_name = stem
        if not display_title:
            display_title = clean_display_title_from_nointro(stem)
            source = "filename"

    # 3. Query online Libretro database if enabled
    if use_online_db and download_artwork:
        online_info = lookup_online_libretro_db(rom_info, cache_dir=cache_dir, timeout=timeout)
        if online_info:
            canonical_name = online_info["canonical_name"] or canonical_name
            if not display_title and canonical_name:
                display_title = clean_display_title_from_nointro(canonical_name)
            if online_info.get("publisher"):
                publisher = online_info["publisher"]
            if online_info.get("release_year"):
                release_year = online_info["release_year"]
            source = "libretro-db"

    # 4. Fallback to embedded NDS banner title, cleaned filename, or ROM internal 12-char title
    if not display_title:
        clean_stem = clean_display_title_from_nointro(stem)
        if rom_info.banner_title:
            display_title = rom_info.banner_title
            source = "rom-banner"
        elif clean_stem:
            display_title = clean_stem
        elif rom_info.internal_title:
            display_title = rom_info.internal_title.title()
        else:
            display_title = f"{spec['name']} ({rom_info.game_code})"

    if not canonical_name:
        canonical_name = f"{display_title} ({rom_info.region})"

    # Apply explicit user CLI overrides
    if custom_title:
        display_title = custom_title.strip()
    if custom_publisher:
        publisher = custom_publisher.strip()
    if custom_year:
        release_year = custom_year.strip()

    short_title = display_title[:63]
    long_title = f"{display_title}\nReleased: {release_year}" if release_year else display_title
    long_title = long_title[:127]

    boxart_path: Optional[str] = None
    boxart_url: Optional[str] = None
    title_screen_path: Optional[str] = None
    title_screen_url: Optional[str] = None

    if custom_cover:
        cover_p = Path(custom_cover).expanduser().resolve()
        if not cover_p.is_file():
            raise FileNotFoundError(f"Custom cover file not found: {custom_cover}")
        boxart_path = str(cover_p)
        boxart_url = "local://custom_cover"

    if custom_title_screen:
        ts_p = Path(custom_title_screen).expanduser().resolve()
        if not ts_p.is_file():
            raise FileNotFoundError(f"Custom title screen file not found: {custom_title_screen}")
        title_screen_path = str(ts_p)
        title_screen_url = "local://custom_title_screen"

    if download_artwork:
        candidates = candidate_nointro_names(
            rom_info.file_path, rom_info, db_canonical_name=canonical_name
        )
        if not boxart_path:
            boxart_path, boxart_url = download_libretro_thumbnail(
                candidates, "Named_Boxarts", cache_dir=cache_dir, timeout=timeout, system=sys_key
            )
        if not title_screen_path:
            title_screen_path, title_screen_url = download_libretro_thumbnail(
                candidates, "Named_Titles", cache_dir=cache_dir, timeout=timeout, system=sys_key
            )
            if not title_screen_path:
                title_screen_path, title_screen_url = download_libretro_thumbnail(
                    candidates, "Named_Snaps", cache_dir=cache_dir, timeout=timeout, system=sys_key
                )

    detected_genre = detect_game_genre(
        game_code=rom_info.game_code,
        title=display_title,
        canonical_name=canonical_name,
        internal_title=rom_info.internal_title,
        override_genre=custom_genre,
    )

    return GameMetadata(
        short_title=short_title,
        long_title=long_title,
        publisher=publisher,
        release_year=release_year,
        canonical_name=canonical_name,
        game_code=rom_info.game_code,
        region=rom_info.region,
        boxart_path=boxart_path,
        title_screen_path=title_screen_path,
        boxart_url=boxart_url,
        title_screen_url=title_screen_url,
        source=source,
        system=sys_key,
        system_name=str(spec["name"]),
        genre=detected_genre,
        genre_name=GENRE_LABELS.get(detected_genre, GENRE_LABELS["classic"])[1],
    )
