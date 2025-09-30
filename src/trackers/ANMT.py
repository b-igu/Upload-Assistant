# -*- coding: utf-8 -*-
import asyncio
import json
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from xml.etree import ElementTree

import httpx
import requests
from bs4 import BeautifulSoup

from src.console import console
from src.trackers.COMMON import COMMON


class ANMT(COMMON):
    def __init__(self, config):
        super().__init__(config)
        self.tracker = "ANMT"
        self.source_flag = ""
        self.banned_groups = [""]
        self.base_url = "https://animetorrents.me"
        self.announce_url = f"{self.base_url}/announce.php"
        self.upload_url = f"{self.base_url}/upload.php"
        self.search_url = f"{self.base_url}/ajax/torrents_data.php"
        self.torrent_url = f"{self.base_url}/download.php?torid="
        self.session = httpx.AsyncClient(
            headers={"User-Agent": f"Audionut's Upload Assistant ({platform.system()} {platform.release()})"},
            timeout=120.0,
        )
        self.signature = (
            "[center][url=https://github.com/Audionut/Upload-Assistant]Uploaded with Audionut's Upload Assistant[/url][/center]"
        )

    # SECTION MAIN FUNCTIONS START
    # Functions called outside of the class

    async def upload(self, meta, disctype):
        mal_data = await self.get_mal_data(meta["mal_id"])
        cookies = await self.load_cookies(meta)
        data = await self.fetch_data(meta, mal_data)
        files = await self.fetch_files(meta, mal_data)
        status_message = ""

        if not meta.get("debug", False):
            headers = self.session.headers
            headers["Connections"] = "keep-alive"
            response = requests.post(
                url=self.upload_url,
                data=data,
                files=files,
                headers={
                    "User-Agent": f"Audionut's Upload Assistant ({platform.system()} {platform.release()})",
                    "Connection": "keep-alive",
                },
                timeout=120,
                cookies=cookies,
            )
            with open(f"{meta['base_dir']}/tmp/{meta['uuid']}/upload_response.html", "w", encoding="utf-8") as response_file:
                response_file.write(response.text)
            if "Upload Successful" in response.text:
                console.print("[bold green]Torrent uploaded successful.[/bold green]")
                status_message = "Upload Successful"

                soup = BeautifulSoup(response.text, "html.parser")
                anchor_element = soup.select_one('a[title="Download"]')
                href = anchor_element.get("href")
                t_id = href.split("torid=")[-1]
                meta["tracker_status"][self.tracker]["torrent_id"] = t_id
                console.print(f"[green]RESULT TORRENT:[/green] {href}")
                await self.download_resulting_torrent(meta, href)
            else:
                console.print("[bold red]Error while uploading torrent.[/bold red]")
                status_message = "Upload Failed"
        else:
            console.print("[cyan]Request Data:")
            console.print(data)
            console.print("[cyan]Request Files:")
            console.print(files)
            status_message = "Debug mode enabled, not uploading."

        meta["tracker_status"][self.tracker]["status_message"] = status_message

    async def search_existing(self, meta, disctype):
        dupes = []
        anmt_type = await self.get_anmt_type(meta["type"])
        anmt_resolution = await self.get_anmt_resolution(meta["resolution"])
        search_title = f"{meta["imdb_info"]["title"]}"
        console.print(f"[yellow]Searching for: {search_title}[/yellow]")
        params = {"total": "1", "cat": "0", "searchin": "filedisc", "search": search_title, "page": "1"}
        await self.load_cookies(meta)
        headers = self.session.headers
        headers["X-Requested-With"] = "XMLHttpRequest"
        try:
            response = await self.session.get(self.search_url, params=params, headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            releases = soup.select("td>a")
        except Exception as e:
            console.print(f"[bold red]Error while search existing releases in {self.tracker}: {e}[/bold red]")
            return dupes

        for release in releases:
            inner_text = release.get_text().strip()
            if search_title in inner_text and anmt_type in inner_text and anmt_resolution in inner_text:
                console.print(f"[bold yellow]Dupe found: {inner_text}[/bold yellow]")
                dupes.append(inner_text)
        return dupes

    async def validate_credentials(self, meta):
        await self.load_cookies(meta)
        try:
            test_url = f"{self.base_url}/torrents.php"
            response = await self.session.get(test_url, timeout=30.0)

            response.raise_for_status()
            if response.status_code == 404:
                console.print(f"[bold red]Error while validating {self.tracker}. Cookie expired.[/bold red]")
                return False
            else:
                return True

        except httpx.TimeoutException:
            console.print(f"[bold red]Error in {self.tracker}: Timeout (30s) while validating credentials.[/bold red]")
            return False
        except httpx.HTTPStatusError as e:
            console.print(
                f"[bold red]HTTP error while validating {self.tracker} credentials: Status {e.response.status_code}.[/bold red]"
            )
            return False
        except httpx.RequestError as e:
            console.print(f"[bold red]Network error while validating {self.tracker} credentials: {e.__class__.__name__}[/bold red]")
            return False

    # SECTION MAIN FUNCTIONS END

    # SECTION GET REQUEST DATA START
    # Functions that get data and files to send in the upload request

    async def fetch_files(self, meta: dict, mal_data: dict) -> list[tuple]:
        """Loads and returns torrent, cover and screenshot files to be sent"""
        subtitles = await self.get_media_subtitles_languages(meta)
        if subtitles:
            try:
                screenshots_paths = await self.take_subbed_screenshots(meta)
            except Exception as e:
                console.print(f"[bold red]Error taking subbed screeenshots: {e} [/bold red]")
                console.print("[bold yellow]Defaulting to existing screenshots.[/bold yellow]")
                console.print("[bold yellow]REMEMBER TO UPLOAD SUBBED SCREENSHOTS MANUALLY!!![/bold yellow]")
                screenshots_paths = await self.retrieve_existing_screenshots(meta)
        else:
            screenshots_paths = await self.retrieve_existing_screenshots(meta)

        tmp_path = Path(f"{meta['base_dir']}/tmp/{meta['uuid']}")
        files_to_send = []

        # filelink
        await self.edit_torrent(meta, self.tracker, self.source_flag, announce_url=self.announce_url)
        torrent_path = tmp_path.joinpath(f"[{self.tracker}].torrent")
        torrent_file = open(str(torrent_path.absolute()), "rb")
        torrent_tuple = (
            "filelink",
            (f"[{self.tracker}].torrent", torrent_file, "application/x-bittorrent"),
        )
        files_to_send.append(torrent_tuple)

        # covers
        covers_dir_path = tmp_path.joinpath("covers")
        covers_dir_path.mkdir(exist_ok=True, parents=True)
        cover_url = mal_data["images"]["jpg"]["image_url"]
        cover_path = covers_dir_path.joinpath("cover.jpeg")
        await self.download_image(cover_url, str(cover_path.absolute()))
        cover_file = open(str(cover_path.absolute()), "rb")
        cover_tuple = ("covers[]", ("cover.jpeg", cover_file, "image/jpeg"))
        files_to_send.append(cover_tuple)

        # screens
        for screen_path in screenshots_paths:
            screen_file = open(screen_path, "rb")
            extension = str(screen_path).rsplit(".", maxsplit=2)[-1]
            screen_tuple = ("screens[]", (Path(screen_path).name, screen_file, f"image/{extension}"))
            files_to_send.append(screen_tuple)

        return files_to_send

    async def fetch_data(self, meta: dict, mal_data: dict) -> dict:
        """Builds main form data"""

        title = await self.get_title(meta, mal_data)
        category_id = await self.get_cat_id(meta["category"])
        resolution_id = await self.get_res_id(meta["resolution"])
        type_id = await self.get_type_id(meta["type"])
        main_language_id = await self.get_main_language_id(meta)
        description = await self.build_description(meta, mal_data)
        ann_id = await self.get_ann_id(meta["imdb_info"]["title"])
        anidb_id = await self.get_anidb_id(meta["imdb_info"]["title"])

        if meta["bdinfo"] is not None:
            techspecs = open(
                f"{meta['base_dir']}/tmp/{meta['uuid']}/BD_SUMMARY_00.txt",
                "r",
                encoding="utf-8",
            ).read()
        else:
            techspecs = open(
                f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt",
                "r",
                encoding="utf-8",
            ).read()

        data = {
            "form": "uploadtorrent",
            "filename": title,
            "category": category_id,
            "tags": meta["keywords"] + ", " + meta["type"],
            "language": main_language_id,
            "youtubeurl": mal_data["trailer"].get("url", ""),
            "annurl": ann_id,
            "anidburl": anidb_id,
            "malurl": f"{meta['mal_id']}",
            "vidquality": resolution_id,
            "vidriptype": type_id,
            "description": description,
            "techspecs": techspecs,
        }
        return data

    # SECTION GET REQUEST DATA END

    # SECTION GET DATA START
    # Functions that format and return data to build the upload request

    async def get_cat_id(self, category_name: str) -> str:
        category_id = {
            "MOVIE": "6",
            "TV": "7",
        }.get(category_name, "0")
        return category_id

    async def get_type_id(self, type: str) -> str:
        type_id = {
            "DISC": "15",
            "REMUX": "14",
            "WEBDL": "13",
            "WEBRIP": "13",
            "HDTV": "4",
            "ENCODE": "2",
        }.get(type, "NA")
        return type_id

    async def get_res_id(self, resolution: str) -> str:
        resolution_id = {
            "1080i": "6",
            "1080p": "5",
            "720p": "4",
            "576p": "3",
            "480p": "2",
        }.get(resolution, "NA")
        return resolution_id

    async def get_ann_id(self, title: str) -> str:
        try:
            params = {"title": f"~{title.lower()}"}
            response = requests.get("https://cdn.animenewsnetwork.com/encyclopedia/api.xml", params=params, timeout=120)
            response.raise_for_status()
            tree = ElementTree.fromstring(response.content)
            anime_node = tree.find("anime")
            ann_id = anime_node.get("id")
            return ann_id
        except Exception as e:
            console.print(f"[bold red]Error retrieving ANN data[/bold red]: {e}")
            return "0"

    async def get_anidb_id(self, title: str) -> str:
        try:
            headers = self.session.headers
            headers["Connection"] = "keep-alive"
            params = {"adb.search": title.lower(), "do.search": "1", "fullmatch": "1"}
            response = await self.session.get(
                "https://anidb.net/anime/", params=params, timeout=120, headers=headers, follow_redirects=True
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            input_elements = soup.select('input[name="aid"]')
            input_element = input_elements[0]
            anidb_id = input_element.get("value")
            return anidb_id
        except Exception as e:
            console.print(f"[bold red]Error retrieving ANIDB data[/bold red]: {e}")
            return "0"

    async def get_main_language_id(self, meta: dict) -> str:
        """Retrieves code from media default audio track"""
        if meta.get("bdinfo"):
            # BDINFO does not have a 'default' field.
            # So we're just checking if it has JP or EN audio.
            # Defaults to JP audio if other language is the main one
            media_audios_languages = await self.get_media_audios_languages(meta)
            if "japanese" in media_audios_languages:
                return "29"
            if "english" in media_audios_languages:
                return "14"
            return "29"

        tracks = meta["mediainfo"]["media"]["track"]
        default_language = "jp"
        for track in tracks:
            if track["@type"] == "Audio" and track["Default"] == "Yes":
                default_language = track["Language"]

        main_language_id = {
            "sq": "1",  # Albanian
            "ar": "2",  # Arabic
            "hy": "3",  # Armenian
            "bn": "4",  # Bengali
            "bs": "5",  # Bosnian
            "bg": "7",  # Bulgarian
            "ca": "8",  # Catalan
            "zh": "9",  # Chinese (Generic)
            "hr": "10",  # Croatian
            "cs": "11",  # Czech
            "da": "12",  # Danish
            "nl": "13",  # Dutch
            "en": "14",  # English
            "eo": "15",  # Esperanto
            "et": "16",  # Estonian
            "fa": "39",  # Farsi (Persian)
            "fi": "17",  # Finnish
            "fr": "18",  # French
            "gl": "19",  # Galician
            "ka": "20",  # Georgian
            "de": "21",  # German
            "el": "22",  # Greek
            "he": "23",  # Hebrew
            "hi": "24",  # Hindi
            "hu": "25",  # Hungarian
            "is": "26",  # Icelandic
            "id": "27",  # Indonesian
            "it": "28",  # Italian
            "ja": "29",  # Japanese
            "kk": "30",  # Kazakh
            "ko": "31",  # Korean
            "lv": "32",  # Latvian
            "lt": "33",  # Lithuanian
            "lb": "34",  # Luxembourgish
            "mk": "35",  # Macedonian
            "ms": "36",  # Malay
            "no": "37",  # Norwegian (Generic)
            "oc": "38",  # Occitan
            "pt": "40",  # Portuguese (Generic)
            "pt-BR": "6",  # Portuguese - Brazilian
            "ro": "41",  # Romanian
            "ru": "42",  # Russian
            "sr": "43",  # Serbian (Generic)
            "si": "44",  # Sinhalese
            "sk": "45",  # Slovak
            "sl": "46",  # Slovenian
            "es": "47",  # Spanish
            "sv": "48",  # Swedish
            "syr": "49",  # Syriac
            "tl": "50",  # Tagalog
            "th": "51",  # Thai
            "tr": "52",  # Turkish
            "uk": "53",  # Ukrainian
            "ur": "54",  # Urdu
            "vi": "55",  # Vietnamese
        }.get(default_language, "29")
        return main_language_id

    async def get_anmt_type(self, type: str) -> str:
        """Retrieves upload type using the tracker pattern"""
        std_type_id = await self.get_type_id(type)
        anmt_type = {
            "15": "BDMV",
            "14": "BR Remux",
            "13": "WEBRip",
            "4": "HDTV",
            "2": "BRRip",
        }.get(std_type_id, "UNKNOWN")
        return anmt_type

    async def get_anmt_resolution(self, resolution: str) -> str:
        """Retrieves upload resolution using the tracker pattern"""
        std_type_id = await self.get_res_id(resolution)
        anmt_type = {"6": "HD 1080i", "5": "HD 1080p", "4": "HD 720p", "3": "SD 576p", "2": "SD 480p"}.get(std_type_id, "UNKNOWN")
        return anmt_type

    async def get_mal_data(self, anime_id: int) -> dict:
        response = requests.get(f"https://api.jikan.moe/v4/anime/{anime_id}", timeout=120)
        content = response.json()
        return content["data"]

    async def get_title(self, meta: dict, mal_data: dict) -> str:
        """Builds the title that'll appear on torrents list"""
        tags = await asyncio.gather(
            self.get_base_title(mal_data),
            self.get_year(mal_data),
            self.get_episode(meta),
            self.get_ripper_tag(meta),
            self.get_raw_tag(meta),
            self.get_audio_tag(meta),
            self.get_hevc_tag(meta),
            self.get_bit_tag(meta),
        )

        title_data = {}
        for tag in tags:
            title_data.update(tag)

        title_list_order = [
            title_data["base_title"],
            title_data["year"],
            title_data["episode"],
            title_data["ripper_tag"],
            title_data["raw_tag"],
            title_data["audio_tag"],
            title_data["hevc_tag"],
            title_data["bit_tag"],
        ]

        return " ".join(title_list_order).strip().replace("  ", "")

    async def get_alternative_titles(self, titles: list[dict]) -> list[str]:
        titles_by_type = {}

        # Group titles by their type
        for item in titles:
            title_type = item.get("type")
            title = item.get("title")
            if title_type and title:
                if title_type not in titles_by_type:
                    titles_by_type[title_type] = []
                titles_by_type[title_type].append(title)

        # Format the titles into the desired string
        formatted_lines = []
        for title_type, titles_list in titles_by_type.items():
            # Handle the "Synonym" case with a plural label
            if title_type == "Synonym":
                label = "Synonyms"
            else:
                label = title_type

            formatted_titles = ", ".join(titles_list)
            formatted_lines.append(f"{label}: {formatted_titles}")

        return formatted_lines

    async def get_producers(self, mal_data: dict) -> str:
        producers = []
        for producer in mal_data["producers"]:
            if producer["name"] not in producers:
                producers.append(producer["name"])
        return ", ".join(producers)

    async def get_genres(self, meta: dict, mal_data: dict) -> list[str]:
        if meta.get("genres"):
            return meta["genres"]
        genres = []
        for genre in mal_data["genres"]:
            if genre["name"] not in genres:
                genres.append(genre["name"])
        return ", ".join(genres)

    async def get_base_title(self, mal_data: dict) -> str:
        return {"base_title": mal_data["title"]}

    async def get_year(self, mal_data: dict) -> str:
        return {"year": f"({mal_data["year"]})"}

    async def get_episode(self, meta: dict) -> str:
        value = ""
        if meta.get("category") == "TV":
            episode = meta.get("episode_int", 0)
            if episode != 0:
                value = f" - {episode:02d}"
        return {"episode": value}

    async def get_ripper_tag(self, meta: dict) -> str:
        tag = meta.get("tag", "")
        value = ""
        if tag:
            value = f" [{meta['tag'][1:]}] "
        return {"ripper_tag": value}

    async def get_audio_tag(self, meta: dict) -> str:
        """Retrieves audio tag using the tracker pattern"""
        value = ""
        audios_languages = await self.get_media_audios_languages(meta)
        en_audio = "en" if meta.get("mediainfo") else "english"

        if len(audios_languages) > 2:
            value = "[Multi Audio]"
        elif len(audios_languages) == 2:
            value = "[Dual Audio]"
        elif en_audio in audios_languages:
            value = "[English Dubbed]"

        return {"audio_tag": value}

    async def get_hevc_tag(self, meta: dict) -> str:
        video_encode = meta.get("video_encode", None)
        value = ""
        if video_encode:
            if "265" in video_encode:
                value = " [HEVC]"
        else:
            if "HEVC" in meta["bdinfo"]["video"][0]["codec"]:
                value = " [HEVC]"

        return {"hevc_tag": value}

    async def get_bit_tag(self, meta: dict) -> str:
        bit_depth = meta.get("bit_depth", "")
        value = ""
        if "10" in bit_depth:
            value = " [10-bit]"
        return {"bit_tag": value}

    async def get_raw_tag(self, meta: dict) -> str:
        value = ""
        media_audios_languages = await self.get_media_audios_languages(meta)
        media_subtitles_languages = await self.get_media_subtitles_languages(meta)

        if meta.get("bdinfo"):
            japanese_language = "japanese"
        else:
            japanese_language = "jp"

        has_non_jp_audio = len(list(filter(lambda language: japanese_language not in language, media_audios_languages))) > 0
        has_non_jp_subtitles = len(list(filter(lambda language: japanese_language not in language, media_subtitles_languages))) > 0

        if not (has_non_jp_audio or has_non_jp_subtitles):
            value = " [RAW]"
        return {"raw_tag": value}

    async def build_description(self, meta: dict, mal_data: dict) -> str:
        description_parts = []

        # Alternative Titles
        description_parts.append("[size=150][b]Alternative Titles[/b][/size]")
        description_parts.append("[list]")
        alternative_titles = await self.get_alternative_titles(mal_data["titles"])
        for title in alternative_titles:
            description_parts.append(f"[*]{title}")
        description_parts.append("[/list]")

        group = meta.get("tag", "-NoGroup")[1:]

        # Information
        description_parts.append("[size=150][b]Information[/b][/size]")
        description_parts.append("[list]")
        description_parts.append(f"[*][b]Type: [/b] {meta['category']}")
        description_parts.append(f"[*][b]Episodes: [/b] {mal_data['episodes']}")
        description_parts.append(f"[*][b]Group: [/b] {group}")
        description_parts.append(f"[*][b]Status: [/b] {mal_data['status']}")
        description_parts.append(f"[*][b]Aired: [/b] {mal_data['aired']['string']}")
        producers = await self.get_producers(mal_data)
        description_parts.append(f"[*][b]Producers: [/b] {producers}")
        genres = await self.get_genres(meta, mal_data)
        description_parts.append(f"[*][b]Genres: [/b] {genres}")
        description_parts.append(f"[*][b]Duration: [/b] {mal_data["duration"]}")
        description_parts.append(f"[*][b]Rating: [/b] {mal_data["rating"]}")
        description_parts.append("[/list]")

        # Synopsis
        description_parts.append("[size=150][b]Synopsis[/b][/size]")
        description_parts.append("\n")
        description_parts.append(mal_data["synopsis"])

        # Signature
        description_parts.append(self.signature)

        description_full = "\n".join(description_parts)
        description_path = Path(f"{meta['base_dir']}/tmp/{meta['uuid']}/[{self.tracker}]DESCRIPTION.txt")
        with open(description_path, "w", encoding="utf-8") as description_file:
            description_file.write(description_full)

        return description_full

    # SECTION GET DATA END

    # SECTION AUX START
    # Auxiliary functions

    async def download_image(self, url: str, save_path: str):
        try:
            response = requests.get(url, stream=True, timeout=120)
            response.raise_for_status()

            with open(save_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)

        except requests.exceptions.RequestException as e:
            console.print(f"[bold error]Error downloading image: {e}[/bold error]")
        except IOError as e:
            console.print(f"[bold error]Error saving file: {e}[/bold error]")

    async def take_subbed_screenshots(self, meta: dict) -> list[str]:
        if meta.get("bdinfo"):
            video_path = meta["discs"][0]["playlists"][0]["items"][0]["file"]
        else:
            video_path = meta["filelist"][0]
        extractor = ScreenshotExtractor(video_path, meta.get("debug", False))
        output_path = f"{meta['base_dir']}/tmp/{meta['uuid']}"
        subbed_screenshots = await extractor.generate_screenshots(output_path)
        return subbed_screenshots

    async def retrieve_existing_screenshots(self, meta: dict) -> list[str]:
        tmp_dir_path = f"{meta['base_dir']}/tmp/{meta['uuid']}"
        existing_screenshots = []
        for root, _, files in os.walk(tmp_dir_path):
            for file in files:
                if file.rsplit(".", 2)[-1] == "png":
                    existing_screenshots.append(f"{root}/{file}")
                    if len(existing_screenshots) == 6:
                        return existing_screenshots
        return existing_screenshots

    async def download_resulting_torrent(self, meta: dict, downurl: str):
        path = f"{meta['base_dir']}/tmp/{meta['uuid']}/[{self.tracker}].torrent"
        if downurl is not None:
            try:
                async with self.session.stream("GET", downurl) as r:
                    r.raise_for_status()
                    with open(path, "wb") as f:
                        async for chunk in r.aiter_bytes():
                            f.write(chunk)
                    return
            except Exception as e:
                console.print(f"[red]Warning: Could not download torrent file: {str(e)}[/red]")
                console.print("[red]Download manually from the tracker.[/red]")
                return

    async def load_cookies(self, meta):
        cookie_file = os.path.abspath(f"{meta['base_dir']}/data/cookies/{self.tracker}.txt")
        if not os.path.exists(cookie_file):
            console.print(f"[bold red]Cookie file for {self.tracker} was not found: {cookie_file}[/bold red]")
            return False
        cookies = await self.parseCookieFile(cookie_file)
        self.session.cookies = cookies
        return cookies

    async def get_media_audios_languages(self, meta: dict) -> list[str]:
        audios_languages = []
        if meta.get("bdinfo"):
            for audio_track in meta["bdinfo"]["audio"]:
                if audio_track["language"] not in audios_languages:
                    audios_languages.append(str(audio_track["language"]).lower())
        else:
            for track in meta["mediainfo"]["media"]["track"]:
                if track["@type"] == "Audio" and track["Language"] not in audios_languages:
                    audios_languages.append(track["Language"])

        return audios_languages

    async def get_media_subtitles_languages(self, meta: dict) -> list[str]:
        subtitles_languages = []
        if meta.get("bdinfo"):
            for subtitle in meta["bdinfo"]["subtitles"]:
                if subtitle not in subtitles_languages:
                    subtitles_languages.append(str(subtitle).lower())
        else:
            for track in meta["mediainfo"]["media"]["track"]:
                if track["@type"] == "Text" and track["Language"] not in subtitles_languages:
                    subtitles_languages.append(track["Language"])

        return subtitles_languages

    # SECTION AUX END


class ScreenshotExtractor:
    def __init__(self, input_file, verbose=False):
        self.input_file = Path(input_file).expanduser().resolve()
        if not self.input_file.exists():
            raise FileNotFoundError(f"Input file not found: {self.input_file}")
        self.temp_dir = Path(tempfile.mkdtemp())
        self.font_dir = self.temp_dir / "fonts"
        self.font_dir.mkdir(exist_ok=True)
        self.ass_file = self.temp_dir / "subs.ass"
        self.sub_timestamps = []
        self.duration = None
        self.subtitle_stream_index = None
        self.fontconfig_file = self.temp_dir / "fonts.conf"
        self.verbose = verbose
        self._prepare()

    def _run(self, args):
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Command failed: {' '.join(args)}\n{result.stderr}")
        return result.stdout, result.stderr

    def _prepare(self):
        self._extract_metadata()
        self._extract_fonts()
        self._extract_subtitles()
        self._parse_ass_for_dialog()

    def _extract_metadata(self):
        cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(self.input_file)]
        output, _ = self._run(cmd)
        data = json.loads(output)
        self.duration = float(data["format"]["duration"])

        for stream in data["streams"]:
            if stream.get("codec_type") == "subtitle" and stream.get("codec_name") == "ass":
                self.subtitle_stream_index = stream["index"]
                break

        if self.subtitle_stream_index is None:
            raise RuntimeError("No ASS subtitle stream found.")

    def _extract_fonts(self):
        font_dir = self.temp_dir / "fonts"
        font_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Use ffprobe to find attachment streams with font-like filenames
        cmd = [
            "ffprobe",
            "-loglevel",
            "error",
            "-select_streams",
            "t",  # attachments
            "-show_entries",
            "stream=index:stream_tags=filename",
            "-print_format",
            "json",
            str(self.input_file),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError("ffprobe failed to list attachments.")

        probe = json.loads(result.stdout)
        font_streams = []

        for stream in probe.get("streams", []):
            filename = stream.get("tags", {}).get("filename", "")
            if filename.lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
                font_streams.append((stream["index"], filename))

        if not font_streams:
            print("[INFO] No embedded fonts found.")
            return font_dir

        # Step 2: Extract only those fonts using ffmpeg
        for index, filename in font_streams:
            output_path = font_dir / filename
            cmd = ["ffmpeg", "-y", "-dump_attachment:t", filename, "-i", str(self.input_file), "-map", f"0:{index}", "-f", "null", "-"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to extract font: {filename}")
            shutil.move(filename, output_path)
            print(f"[✓] Extracted font: {filename}")

        return font_dir

    def _extract_subtitles(self):
        self._run(["ffmpeg", "-y", "-i", str(self.input_file), "-map", f"0:{self.subtitle_stream_index}", str(self.ass_file)])

    def _parse_ass_for_dialog(self):
        dialog_times = []
        min_gap = 30  # seconds between selected subtitle times
        last_selected_time = -min_gap  # ensure the first one qualifies

        with open(self.ass_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("Dialogue:"):
                    parts = line.split(",", 3)
                    if len(parts) >= 3:
                        start_str = parts[1].strip()
                        try:
                            t = self._parse_ass_timestamp(start_str)
                            if t >= 300 and t - last_selected_time >= min_gap:
                                dialog_times.append(t)
                                last_selected_time = t
                        except Exception:
                            continue

        self.sub_timestamps = sorted(set(dialog_times))[:2]
        if self.verbose:
            print(f"[Debug] self.sub_timestamps: {self.sub_timestamps}")

    def _parse_ass_timestamp(self, ts):
        h, m, s = ts.strip().split(":")
        sec, cs = map(int, s.split("."))
        return int(h) * 3600 + int(m) * 60 + sec + cs / 100

    def _setup_fontconfig(self):
        conf = f"""
        <!DOCTYPE fontconfig SYSTEM "fonts.dtd">
        <fontconfig>
            <dir>{self.font_dir}</dir>
        </fontconfig>
        """
        self.fontconfig_file.write_text(conf)

        # Rebuild font cache
        subprocess.run(["fc-cache", str(self.font_dir)], check=True)

    def _choose_static_timestamps(self):
        timestamps = []
        if self.duration < 60:
            raise RuntimeError("Video too short (< 1 minutes) for meaningful screenshots.")
        elif self.duration >= 360:
            base_times = [180, 210, 240, 270]  # 3:00 to 4:30 to avoid OP if possible
        else:
            base_times = [10, 20, 30, 40]  # At least try to get different keyframes for shorts
        return base_times

    def _format_timestamp(self, seconds):
        return str(timedelta(seconds=seconds))

    async def generate_screenshots(self, output_dir):
        output_dir = Path(output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Setup fontconfig so ffmpeg/libass can find fonts
        self._setup_fontconfig()

        # Step 2: Choose timestamps
        static_times = self._choose_static_timestamps()
        final_timestamps = static_times + self.sub_timestamps

        screenshots_paths = []

        for i, ts in enumerate(final_timestamps):
            out_file = output_dir / f"_subbed_screenshot_{i+1:02}.png"
            screenshots_paths.append(out_file)
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{ts:.2f}",
                "-copyts",
                "-i",
                str(self.input_file),
                "-vf",
                f"subtitles={shlex.quote(str(self.ass_file))}:fontsdir={shlex.quote(str(self.font_dir))}",
                "-vframes",
                "1",
                "-pix_fmt",
                "rgb24",
                "-y",
                str(out_file),
            ]

            if self.verbose:
                print(f"[Debug] Running: {' '.join(shlex.quote(arg) for arg in cmd)}")

            # Run with environment variable for fontconfig
            env = os.environ.copy()
            env["FONTCONFIG_FILE"] = str(self.fontconfig_file)

            try:
                subprocess.run(cmd, env=env, check=True)
                print(f"[✓] Saved screenshot: {out_file}")
            except subprocess.CalledProcessError as e:
                print(f"[✗] Failed to generate screenshot at {ts:.2f}s: {e}")

        if screenshots_paths:
            return screenshots_paths
        else:
            raise RuntimeError("No subbed screenshots were generated")

    def cleanup(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
