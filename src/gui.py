import json
import ctypes
import os
import pickle
import queue
import re
import sys
import threading
import shutil
import subprocess
import uuid
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageTk

from core.ai import (
    GenerationStopped,
    ask_ai,
    get_conversation,
    get_library_stats,
    reload_database,
    set_conversation,
    suggest_questions,
    summarize_document,
)


ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")



_THEME_BASE_PATH = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)
_THEME_PATH = _THEME_BASE_PATH / "assets" / "calypso_theme.json"
ctk.set_default_color_theme(
    str(_THEME_PATH) if _THEME_PATH.exists() else "blue"
)


class CalypsoGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        if getattr(sys, "frozen", False):
            self.base_path = Path(sys.executable).resolve().parent
        else:
            self.base_path = Path(__file__).resolve().parent.parent

        self.data_path = self.base_path / "data"
        self.chats_file = self.data_path / "chats.json"
        self.settings_file = self.data_path / "settings.json"
        self.library_metadata_file = (
            self.data_path / "library_metadata.json"
        )
        self.settings = self._load_settings()
        self.library_metadata = self._load_library_metadata()

        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Calypso.PrivateOfflineAI"
            )
        except Exception:
            pass

        self.window_icon = None
        icon_path = self.base_path / "assets" / "icon.ico"
        logo_path = self.base_path / "assets" / "logo.png"

        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except Exception:
                pass

        if logo_path.exists():
            try:
                icon_image = Image.open(logo_path).convert("RGBA")
                self.window_icon = ImageTk.PhotoImage(icon_image)
                self.iconphoto(True, self.window_icon)
            except Exception:
                self.window_icon = None

        ctk.set_appearance_mode(self.settings.get("appearance", "Dark").lower())
        self.configure(fg_color=("#FFF9FC", "#18131C"))

        self.title("Calypso")
        self.geometry(self.settings.get("window_geometry", "1160x760"))
        self.minsize(900, 620)

        self.result_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.logo_image = None
        self.sidebar_logo = None
        self.thinking_bubble = None
        self.thinking_label = None
        self.thinking_animation_running = False
        self.thinking_step = 0
        self.shimmer_bars = []

        self.chats = []
        self.active_chat_id = None
        self.chat_buttons = {}
        self.chat_search_text = ""
        self.last_question = ""
        self.current_library_manager = None
        self.current_memory_window = None
        self._closing = False

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._load_chats()
        self._build_sidebar()
        self._build_main_area()

        saved_font_size = self.settings.get("font_size", "Medium")
        self.font_menu.set(saved_font_size)
        self._change_font_size(saved_font_size)

        self._refresh_chat_list()
        self._render_active_chat()
        self._cleanup_stale_library_data()
        self._check_library_changes()
        self._refresh_document_selector()
        self._enable_file_drop()

        self.after(100, self._check_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_logo(self, size):
        logo_path = self.base_path / "assets" / "logo.png"
        if not logo_path.exists():
            return None

        try:
            image = Image.open(logo_path)
            return ctk.CTkImage(
                light_image=image,
                dark_image=image,
                size=size,
            )
        except Exception:
            return None

    def _apply_window_icon(self, window):
        icon_path = self.base_path / "assets" / "icon.ico"
        logo_path = self.base_path / "assets" / "logo.png"

        if icon_path.exists():
            try:
                window.iconbitmap(str(icon_path))
            except Exception:
                pass

        if logo_path.exists():
            try:
                icon_image = Image.open(logo_path).convert("RGBA")
                window._calypso_icon_image = ImageTk.PhotoImage(
                    icon_image
                )
                window.iconphoto(
                    True,
                    window._calypso_icon_image,
                )
            except Exception:
                pass

    def _vector_cache_name(self, book):
        safe = "".join(
            character if character.isalnum() else "_"
            for character in book
        )
        return f"{safe}.vectors.pkl"

    def _cleanup_stale_library_data(self):
        current_files = set(self._library_files())

        collections = self.library_metadata.setdefault(
            "collections",
            {},
        )
        summaries = self.library_metadata.setdefault(
            "summaries",
            {},
        )
        recent = self.library_metadata.setdefault(
            "recent_documents",
            [],
        )

        changed = False

        for book in list(collections):
            if book not in current_files:
                collections.pop(book, None)
                changed = True

        for book in list(summaries):
            if book not in current_files:
                summaries.pop(book, None)
                changed = True

        cleaned_recent = [
            book for book in recent if book in current_files
        ]
        if cleaned_recent != recent:
            self.library_metadata["recent_documents"] = cleaned_recent
            changed = True

        vector_cache_folder = self.data_path / "vector_cache"
        valid_cache_names = {
            self._vector_cache_name(book)
            for book in current_files
        }

        if vector_cache_folder.exists():
            for cache_path in vector_cache_folder.glob(
                "*.vectors.pkl"
            ):
                if cache_path.name not in valid_cache_names:
                    try:
                        cache_path.unlink()
                    except OSError:
                        pass

        if changed:
            self._save_library_metadata()

    def _load_library_metadata(self):
        defaults = {
            "collections": {},
            "recent_documents": [],
            "summaries": {},
        }

        try:
            if self.library_metadata_file.exists():
                loaded = json.loads(
                    self.library_metadata_file.read_text(
                        encoding="utf-8"
                    )
                )
                if isinstance(loaded, dict):
                    defaults.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass

        return defaults

    def _save_library_metadata(self):
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.library_metadata_file.write_text(
            json.dumps(
                self.library_metadata,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _load_settings(self):
        defaults = {
            "font_size": "Medium",
            "appearance": "Dark",
            "window_geometry": "1160x760",
            "library_signature": "",
        }

        try:
            if self.settings_file.exists():
                loaded = json.loads(
                    self.settings_file.read_text(encoding="utf-8")
                )
                if isinstance(loaded, dict):
                    defaults.update(loaded)
        except Exception:
            pass

        return defaults

    def _save_settings(self):
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(
            json.dumps(self.settings, indent=2),
            encoding="utf-8",
        )

    def _default_chat(self):
        return {
            "id": str(uuid.uuid4()),
            "title": "New conversation",
            "messages": [],
            "conversation": [],
        }

    def _load_chats(self):
        self.data_path.mkdir(parents=True, exist_ok=True)

        try:
            if self.chats_file.exists():
                data = json.loads(self.chats_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self.chats = [
                        chat for chat in data
                        if isinstance(chat, dict) and chat.get("id")
                    ]
        except Exception:
            self.chats = []

        if not self.chats:
            self.chats = [self._default_chat()]

        self.active_chat_id = self.chats[0]["id"]

    def _save_chats(self):
        self.data_path.mkdir(parents=True, exist_ok=True)

        if self.chats_file.exists():
            backup_path = self.data_path / "chats_backup.json"
            try:
                shutil.copy2(self.chats_file, backup_path)
            except OSError:
                pass

        self.chats_file.write_text(
            json.dumps(self.chats, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _active_chat(self):
        for chat in self.chats:
            if chat["id"] == self.active_chat_id:
                return chat

        self.active_chat_id = self.chats[0]["id"]
        return self.chats[0]

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(
            self,
            width=260,
            corner_radius=0,
            fg_color=("gray88", "gray13"),
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(4, weight=1)

        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.grid(
            row=0,
            column=0,
            padx=14,
            pady=(12, 8),
            sticky="ew",
        )
        brand.grid_columnconfigure(1, weight=1)

        self.sidebar_logo = self._load_logo((52, 52))
        ctk.CTkLabel(
            brand,
            text="" if self.sidebar_logo else "A",
            image=self.sidebar_logo,
            font=ctk.CTkFont(size=26, weight="bold"),
        ).grid(
            row=0,
            column=0,
            rowspan=2,
            padx=(0, 12),
            pady=(2, 0),
            sticky="w",
        )

        ctk.CTkLabel(
            brand,
            text="Calypso",
            font=ctk.CTkFont(size=21, weight="bold"),
            anchor="w",
        ).grid(
            row=0,
            column=1,
            sticky="sw",
            pady=(1, 0),
        )

        ctk.CTkLabel(
            brand,
            text="Your private, source-grounded\noffline AI",
            text_color=("#6F5A72", "#BFAFC2"),
            font=ctk.CTkFont(size=9),
            justify="left",
            anchor="w",
        ).grid(
            row=1,
            column=1,
            sticky="nw",
            pady=(0, 1),
        )

        ctk.CTkButton(
            self.sidebar,
            text="+ New Chat",
            height=30,
            corner_radius=8,
            command=self.new_chat,
        ).grid(
            row=1,
            column=0,
            padx=12,
            pady=(0, 4),
            sticky="ew",
        )

        ctk.CTkButton(
            self.sidebar,
            text="Clear Chat",
            height=32,
            corner_radius=9,
            fg_color="transparent",
            border_width=1,
            border_color=("#D7BEDC", "#6B5474"),
            text_color=("#392E3E", "#F8EFFA"),
            hover_color=("#F0E2F2", "#3A2D43"),
            command=self.clear_chat,
        ).grid(
            row=2,
            column=0,
            padx=12,
            pady=4,
            sticky="ew",
        )

        chats_header = ctk.CTkFrame(
            self.sidebar,
            fg_color="transparent",
        )
        chats_header.grid(
            row=3,
            column=0,
            padx=12,
            pady=(16, 5),
            sticky="ew",
        )
        chats_header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            chats_header,
            text="CHATS",
            text_color=("#745F78", "#B9A5BD"),
            font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(
            row=0,
            column=0,
            padx=(3, 8),
            sticky="w",
        )

        self.chat_search = ctk.CTkEntry(
            chats_header,
            placeholder_text="Search...",
            height=28,
            corner_radius=8,
            font=ctk.CTkFont(size=10),
        )
        self.chat_search.grid(
            row=0,
            column=1,
            sticky="ew",
        )
        self.chat_search.bind("<KeyRelease>", self._on_chat_search)

        self.chats_frame = ctk.CTkScrollableFrame(
            self.sidebar,
            fg_color="transparent",
            corner_radius=0,
        )
        self.chats_frame.grid(
            row=4,
            column=0,
            padx=8,
            pady=(0, 6),
            sticky="nsew",
        )
        self.chats_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkFrame(
            self.sidebar,
            height=1,
            fg_color=("#E8DCEB", "#3A303F"),
        ).grid(
            row=5,
            column=0,
            sticky="ew",
            padx=14,
            pady=(2, 4),
        )

        settings = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        settings.grid(
            row=6,
            column=0,
            padx=8,
            pady=0,
            sticky="ew",
        )
        settings.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            settings,
            text="SETTINGS",
            text_color=("#745F78", "#B9A5BD"),
            font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(
            row=0,
            column=0,
            padx=15,
            pady=(8, 4),
            sticky="w",
        )

        action_row = ctk.CTkFrame(settings, fg_color="transparent")
        action_row.grid(
            row=1,
            column=0,
            padx=12,
            pady=(0, 6),
            sticky="ew",
        )
        action_row.grid_columnconfigure(
            0,
            weight=1,
            uniform="sidebar_actions",
        )
        action_row.grid_columnconfigure(
            1,
            weight=1,
            uniform="sidebar_actions",
        )

        self.rebuild_button = ctk.CTkButton(
            action_row,
            text="Rebuild",
            font=ctk.CTkFont(size=11),
            height=32,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=("#D7BEDC", "#6B5474"),
            text_color=("#392E3E", "#F8EFFA"),
            hover_color=("#F0E2F2", "#3A2D43"),
            command=self.rebuild_library,
        )
        self.rebuild_button.grid(
            row=0,
            column=0,
            padx=(0, 4),
            sticky="ew",
        )

        self.export_button = ctk.CTkButton(
            action_row,
            text="Export Chat",
            font=ctk.CTkFont(size=11),
            height=32,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            text_color=("#392E3E", "#F8EFFA"),
            hover_color=("#F0E2F2", "#3A2D43"),
            command=self.export_current_chat,
        )
        self.export_button.grid(
            row=0,
            column=1,
            padx=(4, 0),
            sticky="ew",
        )

        library_row = ctk.CTkFrame(
            settings,
            fg_color="transparent",
        )
        library_row.grid(
            row=2,
            column=0,
            padx=12,
            pady=(0, 6),
            sticky="ew",
        )
        library_row.grid_columnconfigure(
            0,
            weight=1,
            uniform="library_memory",
        )
        library_row.grid_columnconfigure(
            1,
            weight=1,
            uniform="library_memory",
        )

        self.manage_library_button = ctk.CTkButton(
            library_row,
            text="Manage",
            font=ctk.CTkFont(size=11),
            height=32,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=("#D7BEDC", "#6B5474"),
            text_color=("#392E3E", "#F8EFFA"),
            hover_color=("#F0E2F2", "#3A2D43"),
            command=self.open_library_manager,
        )
        self.manage_library_button.grid(
            row=0,
            column=0,
            padx=(0, 4),
            sticky="ew",
        )

        self.memory_button = ctk.CTkButton(
            library_row,
            text="Memory",
            font=ctk.CTkFont(size=11),
            height=32,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=("#D7BEDC", "#6B5474"),
            text_color=("#392E3E", "#F8EFFA"),
            hover_color=("#F0E2F2", "#3A2D43"),
            command=self.open_memory_panel,
        )
        self.memory_button.grid(
            row=0,
            column=1,
            padx=(4, 0),
            sticky="ew",
        )

        ctk.CTkFrame(
            settings,
            height=1,
            fg_color=("#E8DCEB", "#3A303F"),
        ).grid(
            row=3,
            column=0,
            sticky="ew",
            padx=14,
            pady=(2, 8),
        )

        preference_row = ctk.CTkFrame(settings, fg_color="transparent")
        preference_row.grid(
            row=4,
            column=0,
            padx=12,
            pady=(0, 6),
            sticky="ew",
        )
        preference_row.grid_columnconfigure(
            0,
            weight=1,
            uniform="sidebar_preferences",
        )
        preference_row.grid_columnconfigure(
            1,
            weight=1,
            uniform="sidebar_preferences",
        )

        self.appearance_menu = ctk.CTkOptionMenu(
            preference_row,
            values=["Dark", "Light", "System"],
            command=self._change_appearance,
            height=34,
            corner_radius=9,
            anchor="center",
        )
        self.appearance_menu.set(self.settings.get("appearance", "Dark"))
        self.appearance_menu.grid(
            row=0,
            column=0,
            padx=(0, 4),
            sticky="ew",
        )

        self.font_menu = ctk.CTkOptionMenu(
            preference_row,
            values=["Small", "Medium", "Large"],
            command=self._change_font_size,
            height=34,
            corner_radius=9,
            anchor="center",
        )
        self.font_menu.set(self.settings.get("font_size", "Medium"))
        self.font_menu.grid(
            row=0,
            column=1,
            padx=(4, 0),
            sticky="ew",
        )

        stats = get_library_stats()
        self.library_stats = ctk.CTkLabel(
            settings,
            text=(
                f"{stats['pdfs']} PDF · "
                f"{stats['docx']} DOCX · "
                f"{stats['txt']} TXT\n"
                f"{stats['pages']} pages · "
                f"{stats['chunks']} chunks"
            ),
            text_color=("#745F78", "#BFAFC2"),
            font=ctk.CTkFont(size=10),
            anchor="w",
        )
        self.library_stats.grid(
            row=5,
            column=0,
            padx=15,
            pady=(1, 1),
            sticky="ew",
        )

        self.sidebar_status = ctk.CTkLabel(
            settings,
            text="● Library ready",
            text_color=("#6E9F85", "#82C9A2"),
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self.sidebar_status.grid(
            row=6,
            column=0,
            padx=15,
            pady=(2, 12),
            sticky="ew",
        )

    def _build_main_area(self):
        self.main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(1, weight=1)

        self._build_chat()
        self._build_input()
        self._build_progress()

    def _build_chat(self):
        mode_bar = ctk.CTkFrame(
            self.main,
            fg_color="transparent",
        )
        mode_bar.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=16,
            pady=(10, 0),
        )
        mode_bar.grid_columnconfigure(0, weight=1)

        self.interaction_mode = ctk.CTkSegmentedButton(
            mode_bar,
            values=["Research", "Chat"],
            width=168,
            height=25,
            corner_radius=100,
            border_width=0,
            fg_color=("#FFF9FC", "#18131C"),
            unselected_color=("#FFF9FC", "#18131C"),
            unselected_hover_color=("#F5EDF7", "#302737"),
            selected_color=("#F1DDF0", "#5B4664"),
            selected_hover_color=("#EBCFE8", "#6A5074"),
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._change_interaction_mode,
        )
        self.interaction_mode.set("Research")
        self.interaction_mode.grid(
            row=0,
            column=0,
            sticky="n",
        )

        self.mode_subtitle = ctk.CTkLabel(
            mode_bar,
            text="Research · Searches your library",
            font=ctk.CTkFont(size=9),
            text_color=("#8A748D", "#AE9CB2"),
        )
        self.mode_subtitle.grid(
            row=1,
            column=0,
            sticky="n",
            pady=(2, 0),
        )

        self.chat_scroll = ctk.CTkScrollableFrame(
            self.main,
            corner_radius=14,
            fg_color=("#FFFDFE", "#17121B"),
        )
        self.chat_scroll.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=16,
            pady=(7, 10),
        )
        self.chat_scroll.grid_columnconfigure(0, weight=1)

    def _build_input(self):
        input_frame = ctk.CTkFrame(self.main, corner_radius=14)
        input_frame.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=16,
            pady=(0, 8),
        )
        input_frame.grid_columnconfigure(0, weight=1)

        selector_row = ctk.CTkFrame(
            input_frame,
            fg_color="transparent",
        )
        selector_row.grid(
            row=0,
            column=0,
            columnspan=3,
            sticky="ew",
            padx=10,
            pady=(9, 0),
        )
        selector_row.grid_columnconfigure(1, weight=1)
        selector_row.grid_columnconfigure(3, weight=0)

        ctk.CTkLabel(
            selector_row,
            text="Search in",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("#745F78", "#BFAFC2"),
        ).grid(row=0, column=0, padx=(2, 8), sticky="w")

        self.document_menu = ctk.CTkOptionMenu(
            selector_row,
            values=["Entire library"],
            height=28,
            corner_radius=8,
            anchor="w",
        )
        self.document_menu.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(0, 10),
        )

        ctk.CTkLabel(
            selector_row,
            text="Mode",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("#745F78", "#BFAFC2"),
        ).grid(row=0, column=2, padx=(0, 8), sticky="w")

        self.answer_mode_menu = ctk.CTkOptionMenu(
            selector_row,
            values=[
                "Balanced",
                "Quick",
                "Detailed",
                "Bullet points",
                "Explain simply",
            ],
            width=125,
            height=28,
            corner_radius=8,
        )
        self.answer_mode_menu.set("Balanced")
        self.answer_mode_menu.grid(row=0, column=3, sticky="e")

        self.entry = ctk.CTkTextbox(
            input_frame,
            height=46,
            corner_radius=12,
            wrap="word",
            font=ctk.CTkFont(size=15),
        )
        self.entry.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=(10, 8),
            pady=10,
        )
        self.entry.bind("<Return>", self._handle_enter)
        self.entry.bind("<Shift-Return>", self._handle_shift_enter)

        self.stop_button = ctk.CTkButton(
            input_frame,
            text="Stop",
            width=90,
            height=46,
            corner_radius=12,
            fg_color="#A33A56",
            hover_color="#8D3048",
            text_color="#FFFFFF",
            text_color_disabled="#F7E9EE",
            state="disabled",
            command=self.stop_generation,
        )
        self.stop_button.grid(
            row=1,
            column=1,
            padx=(0, 8),
            pady=10,
        )

        self.send_button = ctk.CTkButton(
            input_frame,
            text="Send",
            width=105,
            height=46,
            corner_radius=12,
            command=self.send_message,
        )
        self.send_button.grid(
            row=1,
            column=2,
            padx=(0, 10),
            pady=10,
        )

    def _build_progress(self):
        progress_frame = ctk.CTkFrame(self.main, fg_color="transparent")
        progress_frame.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=20,
            pady=(0, 10),
        )
        progress_frame.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(
            progress_frame,
            corner_radius=10,
        )
        self.progress_bar.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 12),
        )
        self.progress_bar.set(0)

        self.status = ctk.CTkLabel(
            progress_frame,
            text="Ready",
            width=280,
            anchor="e",
            text_color=("#8A748B", "#A994AD"),
            font=ctk.CTkFont(size=11),
        )
        self.status.grid(row=0, column=1, sticky="e")

    def _change_interaction_mode(self, value):
        is_research = value == "Research"
        selector_state = "normal" if is_research else "disabled"

        self.document_menu.configure(state=selector_state)

        if is_research:
            self.mode_subtitle.configure(
                text="Research · Searches your library"
            )
            self.status.configure(
                text="Research mode · Uses your library"
            )
        else:
            self.mode_subtitle.configure(
                text="Chat · Uses built-in knowledge"
            )
            self.status.configure(
                text="Chat mode · No library search or references"
            )

    def _change_appearance(self, value):
        ctk.set_appearance_mode(value.lower())
        self.settings["appearance"] = value
        self._save_settings()

    def _change_font_size(self, value):
        scales = {
            "Small": 0.88,
            "Medium": 1.00,
            "Large": 1.16,
        }

        scale = scales.get(value, 1.00)
        ctk.set_widget_scaling(scale)

        self.settings["font_size"] = value
        self._save_settings()

    def _handle_enter(self, event):
        self.send_message()
        return "break"

    def _handle_shift_enter(self, event):
        self.entry.insert("insert", "\n")
        return "break"

    def _get_input_text(self):
        return self.entry.get("1.0", "end-1c").strip()

    def _clear_input(self):
        self.entry.delete("1.0", "end")

    def open_library_folder(self):
        library_folder = self.base_path / "library"
        library_folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(library_folder))

    def export_current_chat(self):
        chat = self._active_chat()
        messages = chat.get("messages", [])

        if not messages:
            messagebox.showinfo("Export Chat", "This chat is empty.")
            return

        safe_title = re.sub(
            r"[^A-Za-z0-9 _-]",
            "",
            chat.get("title", "chat"),
        ).strip() or "chat"

        path = filedialog.asksaveasfilename(
            title="Export chat",
            initialfile=f"{safe_title}.txt",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt")],
        )

        if not path:
            return

        lines = [f"Calypso — {chat.get('title', 'Conversation')}", ""]

        for message in messages:
            role = message.get("role")
            if role == "user":
                lines.extend(["You:", str(message.get("text", "")), ""])
            elif role == "assistant":
                lines.extend(["Calypso:", str(message.get("text", ""))])

                sources = message.get("sources", [])
                if sources:
                    lines.append("Sources:")
                    for source in sources:
                        lines.append(
                            f"- {source.get('book', 'Unknown')} "
                            f"(page {source.get('page', '?')})"
                        )
                lines.append("")

        Path(path).write_text("\n".join(lines), encoding="utf-8")
        self.status.configure(text="Chat exported")

    def _update_library_stats(self):
        stats = get_library_stats()
        self.library_stats.configure(
            text=(
                f"{stats['pdfs']} PDF · "
                f"{stats['docx']} DOCX · "
                f"{stats['txt']} TXT\n"
                f"{stats['pages']} pages · "
                f"{stats['chunks']} chunks"
            )
        )

    def _library_files(self):
        library_folder = self.base_path / "library"
        library_folder.mkdir(parents=True, exist_ok=True)
        return sorted(
            path.name
            for path in library_folder.iterdir()
            if path.is_file()
            and path.suffix.lower() in {".pdf", ".docx", ".txt"}
        )

    def _refresh_document_selector(self):
        files = self._library_files()
        collections = sorted(
            {
                str(value).strip()
                for value in self.library_metadata.get(
                    "collections",
                    {},
                ).values()
                if str(value).strip()
            }
        )

        values = ["Entire library"]
        values.extend(f"Collection: {name}" for name in collections)
        values.extend(files)

        current = self.document_menu.get()
        self.document_menu.configure(values=values)
        self.document_menu.set(
            current if current in values else "Entire library"
        )

    def _selected_scope(self):
        selected = self.document_menu.get().strip()

        if selected == "Entire library":
            return None, None

        if selected.startswith("Collection: "):
            collection = selected.split(": ", 1)[1]
            books = [
                book
                for book, assigned in self.library_metadata.get(
                    "collections",
                    {},
                ).items()
                if assigned == collection
            ]
            return None, books

        return selected, None

    def _on_chat_search(self, _event=None):
        self.chat_search_text = self.chat_search.get().strip().lower()
        self._refresh_chat_list()

    def _current_library_signature(self):
        library_folder = self.base_path / "library"
        library_folder.mkdir(parents=True, exist_ok=True)

        items = []
        for path in sorted(library_folder.iterdir()):
            if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".txt"}:
                stat = path.stat()
                items.append(
                    f"{path.name}|{stat.st_size}|{stat.st_mtime_ns}"
                )

        return "||".join(items)

    def _check_library_changes(self):
        current = self._current_library_signature()
        saved = self.settings.get("library_signature", "")

        if not saved:
            self.settings["library_signature"] = current
            self._save_settings()
            self._set_library_status("ready", "● Library ready")
            return

        if current != saved:
            self._set_library_status(
                "rebuilding",
                "● Library changed — rebuild needed",
            )
        else:
            self._set_library_status("ready", "● Library ready")

    def _refresh_chat_list(self):
        for widget in self.chats_frame.winfo_children():
            widget.destroy()

        self.chat_buttons = {}

        visible_chats = [
            chat for chat in self.chats
            if not self.chat_search_text
            or self.chat_search_text in chat.get("title", "").lower()
        ]

        for index, chat in enumerate(visible_chats):
            row_frame = ctk.CTkFrame(
                self.chats_frame,
                fg_color="transparent",
            )
            row_frame.grid(
                row=index,
                column=0,
                padx=0,
                pady=2,
                sticky="ew",
            )
            row_frame.grid_columnconfigure(0, weight=1)

            is_active = chat["id"] == self.active_chat_id

            button = ctk.CTkButton(
                row_frame,
                text=chat.get("title", "New conversation"),
                width=0,
                anchor="w",
                height=30,
                corner_radius=8,
                fg_color=("#EEDDF0", "#34273D") if is_active else "transparent",
                hover_color=("#E7D3EA", "#403049"),
                text_color=("#392E3E", "#F8EFFA"),
                command=lambda chat_id=chat["id"]: self.switch_chat(chat_id),
            )
            button.grid(
                row=0,
                column=0,
                padx=(0, 5),
                sticky="ew",
            )

            delete_button = ctk.CTkButton(
                row_frame,
                text="×",
                width=30,
                height=30,
                corner_radius=8,
                fg_color="transparent",
                border_width=1,
                border_color=("#D7BEDC", "#6B5474"),
                text_color=("#392E3E", "#F8EFFA"),
                hover_color=("#F1D3DC", "#713B4D"),
                command=lambda chat_id=chat["id"]: self.delete_chat(chat_id),
            )
            rename_button = ctk.CTkButton(
                row_frame,
                text="✎",
                width=30,
                height=30,
                corner_radius=8,
                fg_color="transparent",
                border_width=1,
                border_color=("#D7BEDC", "#6B5474"),
                text_color=("#392E3E", "#F8EFFA"),
                hover_color=("#F0E2F2", "#403049"),
                command=lambda chat_id=chat["id"]: self.rename_chat(chat_id),
            )
            rename_button.grid(row=0, column=1, padx=(0, 5))

            delete_button.grid(row=0, column=2)

            self.chat_buttons[chat["id"]] = button

    def switch_chat(self, chat_id):
        if self.send_button.cget("state") == "disabled":
            return

        current = self._active_chat()
        current["conversation"] = get_conversation()

        self.active_chat_id = chat_id
        set_conversation(self._active_chat().get("conversation", []))
        self._save_chats()
        self._refresh_chat_list()
        self._render_active_chat()

    def new_chat(self):
        if self.send_button.cget("state") == "disabled":
            return

        current = self._active_chat()
        current["conversation"] = get_conversation()

        new_chat = self._default_chat()
        self.chats.insert(0, new_chat)
        self.active_chat_id = new_chat["id"]

        set_conversation([])
        self._save_chats()
        self._refresh_chat_list()
        self._render_active_chat()

    def rename_chat(self, chat_id):
        chat = next(
            (item for item in self.chats if item["id"] == chat_id),
            None,
        )
        if chat is None:
            return

        dialog = ctk.CTkInputDialog(
            title="Rename Chat",
            text="Enter a new chat name:",
        )
        new_name = dialog.get_input()

        if not new_name:
            return

        chat["title"] = new_name.strip()[:50]
        self._save_chats()
        self._refresh_chat_list()

    def delete_chat(self, chat_id):
        if self.send_button.cget("state") == "disabled":
            return

        chat = next(
            (item for item in self.chats if item["id"] == chat_id),
            None,
        )
        title = chat.get("title", "this chat") if chat else "this chat"

        if not messagebox.askyesno(
            "Delete Chat",
            f'Delete "{title}" permanently?',
        ):
            return

        self.chats = [chat for chat in self.chats if chat["id"] != chat_id]

        if not self.chats:
            self.chats = [self._default_chat()]

        if self.active_chat_id == chat_id:
            self.active_chat_id = self.chats[0]["id"]

        set_conversation(self._active_chat().get("conversation", []))
        self._save_chats()
        self._refresh_chat_list()
        self._render_active_chat()

    def clear_chat(self):
        if self.send_button.cget("state") == "disabled":
            return

        chat = self._active_chat()
        chat["messages"] = []
        chat["conversation"] = []
        chat["title"] = "New conversation"

        set_conversation([])
        self._save_chats()
        self._refresh_chat_list()
        self._render_active_chat()

    def _render_active_chat(self):
        self._stop_thinking_bubble()

        for widget in self.chat_scroll.winfo_children():
            widget.destroy()

        chat = self._active_chat()
        messages = chat.get("messages", [])

        if not messages:
            self._add_system_message(
                "Ask a question about your offline library."
            )
            return

        for message in messages:
            role = message.get("role")
            if role == "user":
                self._add_user_message(
                    str(message.get("text", "")),
                    save=False,
                    timestamp=message.get("timestamp"),
                )
            elif role == "assistant":
                self._add_ai_message(
                    str(message.get("text", "")),
                    message.get("sources", []),
                    save=False,
                    timestamp=message.get("timestamp"),
                )
            elif role == "system":
                self._add_system_message(
                    str(message.get("text", "")),
                    save=False,
                    timestamp=message.get("timestamp"),
                )

    def _scroll_to_bottom(self):
        self.after(
            40,
            lambda: self.chat_scroll._parent_canvas.yview_moveto(1.0),
        )

    def _next_row(self):
        return len(self.chat_scroll.winfo_children())

    def _add_system_message(self, text, save=True, timestamp=None):
        frame = ctk.CTkFrame(self.chat_scroll, fg_color="transparent")
        frame.grid(
            row=self._next_row(),
            column=0,
            sticky="ew",
            padx=10,
            pady=(10, 6),
        )
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            frame,
            text=text,
            text_color=("#7D687F", "#B9A5BD"),
            font=ctk.CTkFont(size=13),
            wraplength=720,
            justify="center",
        ).grid(row=0, column=0, pady=(8, 2))

        shown_time = timestamp or datetime.now().isoformat(timespec="minutes")
        ctk.CTkLabel(
            frame,
            text=shown_time.replace("T", " "),
            text_color=("#958098", "#A994AD"),
            font=ctk.CTkFont(size=9),
        ).grid(row=1, column=0, pady=(0, 6))

        if save:
            self._active_chat()["messages"].append(
                {"role": "system", "text": text, "timestamp": shown_time}
            )
            self._save_chats()

        self._scroll_to_bottom()

    def _add_user_message(self, text, save=True, timestamp=None):
        outer = ctk.CTkFrame(self.chat_scroll, fg_color="transparent")
        outer.grid(
            row=self._next_row(),
            column=0,
            sticky="ew",
            padx=10,
            pady=7,
        )
        outer.grid_columnconfigure(0, weight=1)

        bubble = ctk.CTkFrame(
            outer,
            corner_radius=13,
            fg_color=("#EBC7E5", "#76517F"),
        )
        bubble.grid(row=0, column=1, sticky="e")
        bubble.grid_columnconfigure(0, weight=1)

        shown_time = timestamp or datetime.now().isoformat(timespec="minutes")

        user_header = ctk.CTkFrame(
            bubble,
            fg_color="transparent",
        )
        user_header.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=11,
            pady=(6, 1),
        )
        user_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            user_header,
            text="You",
            text_color=("#392E3E", "#FFF8FD"),
            font=ctk.CTkFont(size=11, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            user_header,
            text=shown_time.replace("T", " "),
            text_color=("#6D5870", "#E6D9E8"),
            font=ctk.CTkFont(size=9),
        ).grid(row=0, column=1, sticky="e", padx=(12, 0))

        ctk.CTkLabel(
            bubble,
            text=text,
            justify="left",
            anchor="w",
            wraplength=500,
            font=ctk.CTkFont(size=14),
        ).grid(
            row=1,
            column=0,
            padx=11,
            pady=(1, 7),
            sticky="w",
        )

        if save:
            chat = self._active_chat()
            chat["messages"].append({"role": "user", "text": text, "timestamp": shown_time})

            if chat.get("title") == "New conversation":
                chat["title"] = text[:28] + ("…" if len(text) > 28 else "")

            self._save_chats()
            self._refresh_chat_list()

        self._scroll_to_bottom()

    def _start_thinking_bubble(self):
        outer = ctk.CTkFrame(self.chat_scroll, fg_color="transparent")
        outer.grid(
            row=self._next_row(),
            column=0,
            sticky="ew",
            padx=10,
            pady=7,
        )
        outer.grid_columnconfigure(1, weight=1)

        bubble = ctk.CTkFrame(
            outer,
            corner_radius=13,
            fg_color=("#F5EAF6", "#251C2B"),
        )
        bubble.grid(row=0, column=0, sticky="w")

        shimmer = ctk.CTkFrame(bubble, fg_color="transparent")
        shimmer.grid(padx=11, pady=9, sticky="w")

        self.shimmer_bars = []
        widths = [220, 170, 120]

        for index, width in enumerate(widths):
            bar = ctk.CTkFrame(
                shimmer,
                width=width,
                height=10,
                corner_radius=5,
                fg_color=("#E2CFE5", "#493752"),
            )
            bar.grid(
                row=index,
                column=0,
                sticky="w",
                pady=3,
            )
            bar.grid_propagate(False)
            self.shimmer_bars.append(bar)

        self.thinking_bubble = outer
        self.thinking_animation_running = True
        self.thinking_step = 0
        self._animate_thinking()
        self._scroll_to_bottom()

    def _animate_thinking(self):
        if not self.thinking_animation_running or not self.shimmer_bars:
            return

        light = ("#D7B9DD", "#76577F")
        dark = ("#ECDFF0", "#3B2D43")
        active = self.thinking_step % len(self.shimmer_bars)

        for index, bar in enumerate(self.shimmer_bars):
            bar.configure(fg_color=light if index == active else dark)

        self.thinking_step += 1
        self.after(220, self._animate_thinking)

    def _stop_thinking_bubble(self):
        self.thinking_animation_running = False

        if self.thinking_bubble is not None:
            self.thinking_bubble.destroy()

        self.thinking_bubble = None
        self.thinking_label = None
        self.shimmer_bars = []

    def _copy_text(self, text, button):
        self.clipboard_clear()
        self.clipboard_append(text)

        original = button.cget("text")
        button.configure(text="Copied")
        self.after(1200, lambda: button.configure(text=original))

    def _add_ai_message(self, text, sources, save=True, timestamp=None):
        outer = ctk.CTkFrame(self.chat_scroll, fg_color="transparent")
        outer.grid(
            row=self._next_row(),
            column=0,
            sticky="ew",
            padx=10,
            pady=7,
        )
        outer.grid_columnconfigure(1, weight=1)

        content = ctk.CTkFrame(
            outer,
            corner_radius=13,
            fg_color=("#F5EAF6", "#251C2B"),
        )
        content.grid(row=0, column=0, sticky="w")
        content.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(content, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=11, pady=(6, 1))
        header.grid_columnconfigure(0, weight=0)
        header.grid_columnconfigure(1, weight=1)

        shown_time = timestamp or datetime.now().isoformat(timespec="minutes")

        ctk.CTkLabel(
            header,
            text="Calypso",
            text_color=("#69546D", "#D7C5DA"),
            font=ctk.CTkFont(size=11, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text=shown_time.replace("T", " "),
            text_color=("#8B748E", "#A994AD"),
            font=ctk.CTkFont(size=9),
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))

        copy_button = ctk.CTkButton(
            header,
            text="Copy",
            width=52,
            height=24,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=("#D7BEDC", "#6B5474"),
            text_color=("#392E3E", "#F8EFFA"),
            hover_color=("#F0E2F2", "#403049"),
            font=ctk.CTkFont(size=10),
        )
        copy_button.configure(
            command=lambda: self._copy_text(text, copy_button)
        )
        copy_button.grid(row=0, column=2, sticky="e")

        ctk.CTkLabel(
            content,
            text=text,
            justify="left",
            anchor="w",
            wraplength=650,
            font=ctk.CTkFont(size=14),
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=11,
            pady=(1, 7),
        )

        unique_sources = []
        seen = set()

        for source in sources:
            book = str(source.get("book", "Unknown"))
            page = source.get("page", "?")
            pdf = str(source.get("pdf", ""))
            preview = str(source.get("preview", "")).strip()
            file_type = str(source.get("file_type", "")).lower()

            key = (book, page, pdf, preview)
            if key in seen:
                continue

            seen.add(key)
            unique_sources.append(
                {
                    "book": book,
                    "page": page,
                    "pdf": pdf,
                    "preview": preview,
                    "file_type": file_type,
                }
            )

        if unique_sources:
            ctk.CTkFrame(
                content,
                height=1,
                fg_color=("#DECBE2", "#4A3952"),
            ).grid(
                row=2,
                column=0,
                sticky="ew",
                padx=14,
                pady=(0, 8),
            )

            sources_frame = ctk.CTkFrame(
                content,
                fg_color="transparent",
            )
            sources_frame.grid(
                row=3,
                column=0,
                sticky="w",
                padx=12,
                pady=(0, 10),
            )

            ctk.CTkLabel(
                sources_frame,
                text="Sources",
                text_color=("#745F78", "#BFAFC2"),
                font=ctk.CTkFont(size=10, weight="bold"),
            ).grid(
                row=0,
                column=0,
                columnspan=3,
                sticky="w",
                padx=2,
                pady=(0, 4),
            )

            for column in range(3):
                sources_frame.grid_columnconfigure(
                    column,
                    weight=1,
                    uniform="source_cards",
                )

            for index, source in enumerate(unique_sources):
                source_row = 1 + index // 3
                source_column = index % 3

                location_label = (
                    f"p.{source['page']}"
                    if source.get("file_type") == "pdf"
                    else f"section {source['page']}"
                )

                full_source_name = str(source.get("book", "Source"))
                maximum_name_length = 24
                if len(full_source_name) > maximum_name_length:
                    display_source_name = (
                        full_source_name[: maximum_name_length - 3].rstrip()
                        + "..."
                    )
                else:
                    display_source_name = full_source_name

                source_button = ctk.CTkButton(
                    sources_frame,
                    text=(
                        f"{display_source_name}  ·  {location_label}"
                    ),
                    height=28,
                    corner_radius=8,
                    font=ctk.CTkFont(size=9),
                    anchor="w",
                    fg_color=("#F5EAF6", "#251C2B"),
                    hover_color=("#F0E2F2", "#3A2D43"),
                    border_width=1,
                    border_color=("#D7BEDC", "#5A4562"),
                    text_color=("#4E3A54", "#F1E7F3"),
                    command=lambda item=dict(source): (
                        self._show_source_preview(item)
                    ),
                )
                source_button.grid(
                    row=source_row,
                    column=source_column,
                    padx=3,
                    pady=3,
                    sticky="ew",
                )

        if save:
            chat = self._active_chat()
            chat["messages"].append(
                {
                    "role": "assistant",
                    "text": text,
                    "sources": unique_sources,
                    "timestamp": shown_time,
                }
            )
            chat["conversation"] = get_conversation()
            self._save_chats()

        self._scroll_to_bottom()

    def _set_library_status(self, state, text):
        colors = {
            "ready": ("#6E9F85", "#82C9A2"),
            "rebuilding": ("#B38B5A", "#D9B77A"),
            "error": ("#BE667B", "#F08AA1"),
        }
        self.sidebar_status.configure(
            text=text,
            text_color=colors.get(state, colors["ready"]),
        )

    def _set_busy(self, busy):
        normal_state = "disabled" if busy else "normal"

        self.send_button.configure(state=normal_state)
        self.entry.configure(state=normal_state)
        self.rebuild_button.configure(state=normal_state)
        self.export_button.configure(state=normal_state)
        document_state = normal_state
        if not busy and self.interaction_mode.get() == "Chat":
            document_state = "disabled"

        self.document_menu.configure(state=document_state)
        self.answer_mode_menu.configure(state=normal_state)
        self.interaction_mode.configure(state=normal_state)
        self.manage_library_button.configure(state=normal_state)
        self.memory_button.configure(state=normal_state)
        self.stop_button.configure(state="normal" if busy else "disabled")

        if not busy:
            self.entry.focus()

    def _queue_progress(self, percent, message):
        self.result_queue.put(("progress", int(percent), message))

    def send_message(self):
        question = self._get_input_text()

        if not question or self.send_button.cget("state") == "disabled":
            return

        set_conversation(self._active_chat().get("conversation", []))

        self.stop_event.clear()
        self._clear_input()
        self._add_user_message(question)
        self._start_thinking_bubble()

        self._set_busy(True)
        self.progress_bar.set(0.02)
        self.status.configure(text="2% · Starting...")

        interaction_mode = self.interaction_mode.get()
        selected_document, selected_books = self._selected_scope()
        answer_mode = self.answer_mode_menu.get()
        self.last_question = question

        if interaction_mode == "Chat":
            selected_document = None
            selected_books = None

        threading.Thread(
            target=self._ask_worker,
            args=(
                question,
                self.active_chat_id,
                selected_document,
                selected_books,
                answer_mode,
                interaction_mode,
            ),
            daemon=True,
        ).start()

    def _ask_worker(
        self,
        question,
        chat_id,
        selected_document,
        selected_books,
        answer_mode,
        interaction_mode,
    ):
        try:
            answer, sources = ask_ai(
                question,
                progress_callback=self._queue_progress,
                stop_event=self.stop_event,
                selected_book=selected_document,
                selected_books=selected_books,
                answer_mode=answer_mode,
                interaction_mode=interaction_mode,
            )
            cleaned_answer = ANSI_RE.sub("", answer).strip()
            self.result_queue.put(
                ("answer", cleaned_answer, sources, chat_id)
            )
        except GenerationStopped:
            self.result_queue.put(("stopped", "", [], chat_id))
        except Exception as error:
            self.result_queue.put(("error", str(error), [], chat_id))

    def stop_generation(self):
        self.stop_event.set()
        self.status.configure(text="Stopping generation...")
        self.stop_button.configure(state="disabled")

    def rebuild_library(self):
        if self.rebuild_button.cget("state") == "disabled":
            return

        self.stop_event.clear()
        self._set_busy(True)
        self.stop_button.configure(state="disabled")
        self._set_library_status("rebuilding", "● Rebuilding library")
        self.progress_bar.set(0.01)
        self.status.configure(text="1% · Starting rebuild...")

        threading.Thread(
            target=self._rebuild_worker,
            daemon=True,
        ).start()

    def _rebuild_worker(self):
        try:
            if getattr(sys, "frozen", False):
                command = [sys.executable, "--rebuild-worker"]
            else:
                command = [
                    sys.executable,
                    str(self.base_path / "src" / "main.py"),
                    "--rebuild-worker",
                ]

            creation_flags = 0
            if os.name == "nt":
                creation_flags = subprocess.CREATE_NO_WINDOW

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
            )

            last_output = ""

            if process.stdout is not None:
                for raw_line in process.stdout:
                    line = raw_line.strip()

                    if not line:
                        continue

                    last_output = line

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type")

                    if event_type == "progress":
                        self._queue_progress(
                            int(event.get("percent", 0)),
                            str(event.get("message", "Rebuilding...")),
                        )
                    elif event_type == "error":
                        raise RuntimeError(
                            str(event.get("message", "Rebuild failed"))
                        )

            return_code = process.wait()

            if return_code != 0:
                detail = last_output or f"Worker exit code: {return_code}"
                raise RuntimeError(
                    "The rebuild worker stopped unexpectedly. " + detail
                )

            self._queue_progress(97, "Reloading new library...")
            reload_database()
            self.result_queue.put(("rebuilt", "", [], self.active_chat_id))

        except Exception as error:
            self.result_queue.put(
                ("error", str(error), [], self.active_chat_id)
            )

    def _check_queue(self):
        if self._closing:
            return

        try:
            while True:
                item = self.result_queue.get_nowait()
                kind = item[0]

                if kind == "progress":
                    _, value, extra = item
                    percent = max(0, min(100, int(value)))
                    self.progress_bar.set(percent / 100)
                    self.status.configure(text=f"{percent}% · {extra}")
                    continue

                _, value, extra, chat_id = item

                if kind == "document_summary":
                    summaries = self.library_metadata.setdefault(
                        "summaries",
                        {},
                    )
                    summaries[str(extra)] = str(value)
                    self._save_library_metadata()
                    self._show_text_window(
                        f"Summary · {extra}",
                        str(value),
                    )
                    self.progress_bar.set(1)
                    self.status.configure(text="Summary ready")
                    continue

                if kind == "suggested_questions":
                    self._show_questions_window(
                        str(extra),
                        list(value),
                    )
                    self.progress_bar.set(1)
                    self.status.configure(text="Questions ready")
                    continue

                if kind == "feature_error":
                    messagebox.showerror(
                        "Calypso",
                        str(value),
                    )
                    self.progress_bar.set(0)
                    self.status.configure(text="Feature error")
                    continue

                if kind == "answer":
                    self._stop_thinking_bubble()
                    self._record_recent_sources(extra)

                    if chat_id == self.active_chat_id:
                        self._add_ai_message(value, extra)
                    else:
                        for chat in self.chats:
                            if chat["id"] == chat_id:
                                chat["messages"].append(
                                    {
                                        "role": "assistant",
                                        "text": value,
                                        "sources": extra,
                                        "timestamp": datetime.now().isoformat(timespec="minutes"),
                                    }
                                )
                                chat["conversation"] = get_conversation()
                                break
                        self._save_chats()

                    self.progress_bar.set(1)
                    self.status.configure(text="100% · Answer ready")
                    self._set_busy(False)

                elif kind == "stopped":
                    self._stop_thinking_bubble()
                    self.progress_bar.set(0)
                    self.status.configure(text="Stopped")
                    self._set_busy(False)

                elif kind == "rebuilt":
                    self.progress_bar.set(1)
                    self.status.configure(
                        text="100% · Library rebuilt and reloaded"
                    )
                    self.settings["library_signature"] = (
                        self._current_library_signature()
                    )
                    self._save_settings()
                    self._set_library_status("ready", "● Library ready")
                    self._cleanup_stale_library_data()
                    self._update_library_stats()
                    self._refresh_document_selector()
                    if (
                        self.current_library_manager is not None
                        and self.current_library_manager.winfo_exists()
                    ):
                        self._populate_library_manager()
                    self._set_busy(False)

                else:
                    self._stop_thinking_bubble()
                    self._add_system_message(f"Error: {value}")
                    self.progress_bar.set(0)
                    self.status.configure(text="Error")
                    self._set_library_status("error", "● Library error")
                    self._set_busy(False)

        except queue.Empty:
            pass

        if not self._closing:
            self.after(100, self._check_queue)

    def _record_recent_sources(self, sources):
        recent = self.library_metadata.setdefault(
            "recent_documents",
            [],
        )

        for source in sources:
            book = str(source.get("book", "")).strip()
            if not book:
                continue

            if book in recent:
                recent.remove(book)
            recent.insert(0, book)

        del recent[10:]
        self._save_library_metadata()

    def _highlight_preview_matches(
        self,
        textbox,
        preview_text,
        question,
    ):
        words = {
            word.lower()
            for word in re.findall(r"[A-Za-z0-9]{4,}", question)
        }

        if not words:
            return

        try:
            textbox.tag_config(
                "match",
                background="#DFA0D4",
                foreground="#241728",
            )

            lower_text = preview_text.lower()
            for word in words:
                start = 0
                while True:
                    index = lower_text.find(word, start)
                    if index < 0:
                        break

                    textbox.tag_add(
                        "match",
                        f"1.0+{index}c",
                        f"1.0+{index + len(word)}c",
                    )
                    start = index + len(word)
        except Exception:
            pass

    def _enable_file_drop(self):
        try:
            import windnd

            windnd.hook_dropfiles(
                self,
                func=lambda paths: self.after(
                    0,
                    lambda: self._handle_dropped_files(paths),
                ),
            )
        except Exception:
            pass

    def _handle_dropped_files(self, paths):
        decoded = []
        for value in paths:
            if isinstance(value, bytes):
                decoded.append(
                    Path(value.decode("utf-8", errors="replace"))
                )
            else:
                decoded.append(Path(value))

        self._add_library_files(decoded)

    def _add_library_files(self, paths=None):
        if paths is None:
            selected = filedialog.askopenfilenames(
                title="Add documents to Calypso",
                filetypes=[
                    ("Supported documents", "*.pdf *.docx *.txt"),
                    ("PDF files", "*.pdf"),
                    ("Word documents", "*.docx"),
                    ("Text files", "*.txt"),
                ],
            )
            paths = [Path(path) for path in selected]

        library_folder = self.base_path / "library"
        library_folder.mkdir(parents=True, exist_ok=True)
        added = 0

        for source in paths:
            source = Path(source)
            if (
                not source.is_file()
                or source.suffix.lower()
                not in {".pdf", ".docx", ".txt"}
            ):
                continue

            destination = library_folder / source.name
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            added += 1

        if added:
            self._check_library_changes()
            self._refresh_document_selector()
            if (
                self.current_library_manager is not None
                and self.current_library_manager.winfo_exists()
            ):
                self._populate_library_manager()

            self.status.configure(
                text=f"Added {added} document(s) · Rebuild required"
            )

    def _folder_size(self, path):
        path = Path(path)

        if not path.exists():
            return 0

        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0

        total = 0
        stack = [path]

        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                total += entry.stat(
                                    follow_symlinks=False
                                ).st_size
                        except OSError:
                            pass
            except OSError:
                pass

        return total

    def _format_bytes(self, value):
        value = float(max(0, value))
        units = ["B", "KB", "MB", "GB", "TB"]

        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.2f} {unit}"
            value /= 1024

        return "0 B"

    def _system_memory_total(self):
        if os.name != "nt":
            return None

        try:
            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(
                ctypes.byref(status)
            )
            return int(status.total_physical)
        except Exception:
            return None

    def _storage_statistics(self):
        data = self.data_path
        library = self.base_path / "library"

        chat_paths = [
            data / "chats.json",
            data / "chats_backup.json",
        ]
        metadata_paths = [
            data / "library_metadata.json",
            data / "settings.json",
        ]

        ollama_folder = (
            Path(os.environ["OLLAMA_MODELS"])
            if os.environ.get("OLLAMA_MODELS")
            else Path.home() / ".ollama" / "models"
        )
        embedding_folder = (
            Path.home()
            / ".cache"
            / "huggingface"
            / "hub"
            / "models--sentence-transformers--all-MiniLM-L6-v2"
        )

        sizes = {
            "library": self._folder_size(library),
            "document_cache": self._folder_size(
                data / "document_cache"
            ),
            "vector_cache": self._folder_size(
                data / "vector_cache"
            ),
            "search_index": self._folder_size(data / "faiss"),
            "documents_index": self._folder_size(
                data / "documents.pkl"
            ),
            "chat_history": sum(
                self._folder_size(path) for path in chat_paths
            ),
            "summaries_metadata": sum(
                self._folder_size(path) for path in metadata_paths
            ),
            "embedding_model": self._folder_size(
                embedding_folder
            ),
            "ai_model": self._folder_size(ollama_folder),
        }

        sizes["calypso_total"] = sum(
            sizes[key]
            for key in (
                "library",
                "document_cache",
                "vector_cache",
                "search_index",
                "documents_index",
                "chat_history",
                "summaries_metadata",
            )
        )
        sizes["all_local_ai_total"] = (
            sizes["calypso_total"]
            + sizes["embedding_model"]
            + sizes["ai_model"]
        )

        disk = shutil.disk_usage(self.base_path)

        return {
            "sizes": sizes,
            "documents": len(self._library_files()),
            "chats": len(self.chats),
            "cpu_threads": os.cpu_count() or 1,
            "system_ram": self._system_memory_total(),
            "disk_free": disk.free,
            "disk_total": disk.total,
        }

    def open_memory_panel(self):
        if (
            self.current_memory_window is not None
            and self.current_memory_window.winfo_exists()
        ):
            self.current_memory_window.focus()
            self._refresh_memory_panel()
            return

        window = ctk.CTkToplevel(self)
        self.current_memory_window = window
        window.title("Memory & Storage")
        self._apply_window_icon(window)
        window.geometry("690x570")
        window.minsize(620, 500)
        window.transient(self)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(window, fg_color="transparent")
        header.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=18,
            pady=(15, 6),
        )
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="Memory & Storage",
            font=ctk.CTkFont(size=19, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        self.memory_refresh_button = ctk.CTkButton(
            header,
            text="Refresh",
            width=78,
            height=29,
            fg_color="transparent",
            border_width=1,
            command=self._refresh_memory_panel,
        )
        self.memory_refresh_button.grid(
            row=0,
            column=1,
            sticky="e",
        )

        self.memory_status_label = ctk.CTkLabel(
            window,
            text="Calculating exact sizes...",
            anchor="w",
            text_color=("#745F78", "#BFAFC2"),
            font=ctk.CTkFont(size=10),
        )
        self.memory_status_label.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=18,
            pady=(0, 7),
        )

        self.memory_scroll = ctk.CTkScrollableFrame(
            window,
            corner_radius=10,
        )
        self.memory_scroll.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=16,
            pady=(0, 10),
        )
        self.memory_scroll.grid_columnconfigure(0, weight=1)

        actions = ctk.CTkFrame(
            window,
            fg_color="transparent",
        )
        actions.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=16,
            pady=(0, 15),
        )
        actions.grid_columnconfigure(
            0,
            weight=1,
            uniform="memory_actions",
        )
        actions.grid_columnconfigure(
            1,
            weight=1,
            uniform="memory_actions",
        )

        ctk.CTkButton(
            actions,
            text="Clear Cache",
            height=34,
            fg_color="transparent",
            border_width=1,
            hover_color=("#F0E2F2", "#3A2D43"),
            command=self._confirm_clear_cache,
        ).grid(
            row=0,
            column=0,
            padx=(0, 5),
            sticky="ew",
        )

        ctk.CTkButton(
            actions,
            text="Reset Calypso",
            height=34,
            fg_color="#A33A56",
            hover_color="#8D3048",
            text_color="#FFFFFF",
            command=self._confirm_full_reset,
        ).grid(
            row=0,
            column=1,
            padx=(5, 0),
            sticky="ew",
        )

        self._refresh_memory_panel()

    def _memory_stat_row(self, parent, row, title, size, note=""):
        item = ctk.CTkFrame(
            parent,
            fg_color="transparent",
        )
        item.grid(
            row=row,
            column=0,
            sticky="ew",
            padx=7,
            pady=2,
        )
        item.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            item,
            text=title,
            font=ctk.CTkFont(size=11),
            anchor="w",
        ).grid(
            row=0,
            column=0,
            sticky="w",
        )

        ctk.CTkLabel(
            item,
            text=self._format_bytes(size),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#7C527F", "#D8A7DD"),
            anchor="e",
        ).grid(
            row=0,
            column=1,
            sticky="e",
        )

        if note:
            ctk.CTkLabel(
                item,
                text=note,
                font=ctk.CTkFont(size=9),
                text_color=("#806B84", "#BFAFC2"),
                anchor="w",
            ).grid(
                row=1,
                column=0,
                columnspan=2,
                sticky="w",
                pady=(0, 2),
            )

    def _refresh_memory_panel(self):
        if (
            self.current_memory_window is None
            or not self.current_memory_window.winfo_exists()
        ):
            return

        self.memory_refresh_button.configure(state="disabled")
        self.memory_status_label.configure(
            text="Calculating exact sizes..."
        )

        for child in self.memory_scroll.winfo_children():
            child.destroy()

        threading.Thread(
            target=self._memory_statistics_worker,
            daemon=True,
        ).start()

    def _memory_statistics_worker(self):
        try:
            statistics = self._storage_statistics()
            self.after(
                0,
                lambda: self._display_memory_statistics(
                    statistics
                ),
            )
        except Exception as error:
            self.after(
                0,
                lambda: self._memory_statistics_failed(
                    str(error)
                ),
            )

    def _memory_statistics_failed(self, message):
        if (
            self.current_memory_window is None
            or not self.current_memory_window.winfo_exists()
        ):
            return

        self.memory_refresh_button.configure(state="normal")
        self.memory_status_label.configure(
            text=f"Could not calculate sizes: {message}"
        )

    def _display_memory_statistics(self, statistics):
        if (
            self.current_memory_window is None
            or not self.current_memory_window.winfo_exists()
        ):
            return

        sizes = statistics["sizes"]
        rows = [
            (
                "Library files",
                sizes["library"],
                f"{statistics['documents']} document(s)",
            ),
            (
                "Extracted-text cache",
                sizes["document_cache"],
                "Safe to clear; rebuilt when needed",
            ),
            (
                "Embedding cache",
                sizes["vector_cache"],
                "Safe to clear; makes rebuilds faster",
            ),
            (
                "Search index",
                sizes["search_index"],
                "Safe to clear; requires Rebuild",
            ),
            (
                "Indexed document data",
                sizes["documents_index"],
                "Safe to clear; requires Rebuild",
            ),
            (
                "Chat history",
                sizes["chat_history"],
                f"{statistics['chats']} saved conversation(s)",
            ),
            (
                "Summaries and metadata",
                sizes["summaries_metadata"],
                "Collections, summaries, and settings metadata",
            ),
            (
                "Embedding AI model",
                sizes["embedding_model"],
                "all-MiniLM-L6-v2",
            ),
            (
                "Chat AI model",
                sizes["ai_model"],
                "Ollama model storage",
            ),
        ]

        for child in self.memory_scroll.winfo_children():
            child.destroy()

        row_number = 0
        for title, size, note in rows:
            self._memory_stat_row(
                self.memory_scroll,
                row_number,
                title,
                size,
                note,
            )
            row_number += 1

        ctk.CTkFrame(
            self.memory_scroll,
            height=1,
            fg_color=("#E8DCEB", "#3A303F"),
        ).grid(
            row=row_number,
            column=0,
            sticky="ew",
            padx=7,
            pady=8,
        )
        row_number += 1

        self._memory_stat_row(
            self.memory_scroll,
            row_number,
            "Calypso data total",
            sizes["calypso_total"],
            "Library, indexes, caches, history, and metadata",
        )
        row_number += 1

        self._memory_stat_row(
            self.memory_scroll,
            row_number,
            "Total including local AI models",
            sizes["all_local_ai_total"],
            "All items listed above",
        )
        row_number += 1

        ram = statistics["system_ram"]
        ram_text = (
            self._format_bytes(ram)
            if ram is not None
            else "Unavailable"
        )

        resources = ctk.CTkLabel(
            self.memory_scroll,
            text=(
                f"Computer: {statistics['cpu_threads']} CPU threads · "
                f"{ram_text} installed RAM\n"
                f"Disk free: {self._format_bytes(statistics['disk_free'])} "
                f"of {self._format_bytes(statistics['disk_total'])}"
            ),
            justify="left",
            anchor="w",
            font=ctk.CTkFont(size=10),
            text_color=("#745F78", "#BFAFC2"),
        )
        resources.grid(
            row=row_number,
            column=0,
            sticky="ew",
            padx=7,
            pady=(10, 5),
        )

        self.memory_refresh_button.configure(state="normal")
        self.memory_status_label.configure(
            text="Exact storage sizes · calculated locally"
        )

    def _confirm_clear_cache(self):
        confirmed = messagebox.askyesno(
            "Clear cache",
            (
                "Delete extracted text, embeddings, and search indexes?\n\n"
                "Your library files, chats, summaries, and settings will "
                "remain. You will need to press Rebuild before Research "
                "mode can search the library again."
            ),
            parent=self.current_memory_window,
        )

        if confirmed:
            self._clear_noncritical_cache()

    def _clear_noncritical_cache(self):
        removable = [
            self.data_path / "document_cache",
            self.data_path / "vector_cache",
            self.data_path / "faiss",
            self.data_path / "faiss_new",
            self.data_path / "faiss_old",
            self.data_path / "documents.pkl",
        ]

        for path in removable:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            except OSError:
                pass

        try:
            reload_database()
        except Exception:
            pass

        self.last_library_signature = None
        self._update_library_stats()
        self._check_library_changes()
        self._refresh_memory_panel()
        self.status.configure(
            text="Cache cleared · Rebuild required"
        )

        messagebox.showinfo(
            "Cache cleared",
            (
                "Non-critical caches and indexes were removed. "
                "Your documents, chats, summaries, and settings were kept."
            ),
            parent=self.current_memory_window,
        )

    def _confirm_full_reset(self):
        first_confirm = messagebox.askyesno(
            "Reset Calypso",
            (
                "This permanently deletes every library document, all "
                "chats, summaries, indexes, and caches.\n\n"
                "The installed AI models and visual settings remain."
            ),
            parent=self.current_memory_window,
        )

        if not first_confirm:
            return

        second_confirm = messagebox.askyesno(
            "Final confirmation",
            (
                "This cannot be undone.\n\n"
                "Reset Calypso to an empty state now?"
            ),
            parent=self.current_memory_window,
        )

        if second_confirm:
            self._reset_calypso_data()

    def _reset_calypso_data(self):
        self.stop_event.set()

        library_folder = self.base_path / "library"
        library_folder.mkdir(parents=True, exist_ok=True)

        for path in library_folder.iterdir():
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError:
                pass

        removable_names = {
            "chats.json",
            "chats_backup.json",
            "library_metadata.json",
            "documents.pkl",
            "document_cache",
            "vector_cache",
            "faiss",
            "faiss_new",
            "faiss_old",
        }

        self.data_path.mkdir(parents=True, exist_ok=True)
        for path in list(self.data_path.iterdir()):
            if path.name not in removable_names:
                continue

            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError:
                pass

        self.library_metadata = {
            "collections": {},
            "recent_documents": [],
            "summaries": {},
        }
        self._save_library_metadata()

        self.chats = [self._default_chat()]
        self.active_chat_id = self.chats[0]["id"]
        set_conversation([])
        self._save_chats()

        try:
            reload_database()
        except Exception:
            pass

        self.stop_event.clear()
        self.last_library_signature = None
        self._refresh_chat_list()
        self._render_active_chat()
        self._refresh_document_selector()
        self._update_library_stats()
        self._check_library_changes()
        self.status.configure(
            text="Calypso reset · Add documents to begin"
        )

        if (
            self.current_library_manager is not None
            and self.current_library_manager.winfo_exists()
        ):
            self._populate_library_manager()

        self._refresh_memory_panel()

        messagebox.showinfo(
            "Calypso reset",
            (
                "All library files, history, indexes, summaries, and "
                "caches were deleted."
            ),
            parent=self.current_memory_window,
        )

    def open_library_manager(self):
        if (
            self.current_library_manager is not None
            and self.current_library_manager.winfo_exists()
        ):
            self.current_library_manager.focus()
            return

        window = ctk.CTkToplevel(self)
        self.current_library_manager = window
        window.title("Library Manager")
        self._apply_window_icon(window)
        window.geometry("820x560")
        window.minsize(700, 460)
        window.transient(self)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(window, fg_color="transparent")
        header.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=16,
            pady=(14, 6),
        )
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="Library Manager",
            font=ctk.CTkFont(size=19, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            header,
            text="+ Add Files",
            width=100,
            command=self._add_library_files,
        ).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkButton(
            header,
            text="Open Folder",
            width=100,
            fg_color="transparent",
            border_width=1,
            command=self.open_library_folder,
        ).grid(row=0, column=2, padx=(8, 0))

        self.library_manager_info = ctk.CTkLabel(
            window,
            text=(
                "Drop PDF, DOCX, or TXT files anywhere on Calypso. "
                "Changes take effect after Rebuild."
            ),
            text_color=("#745F78", "#BFAFC2"),
            anchor="w",
        )
        self.library_manager_info.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=18,
            pady=(0, 8),
        )

        self.library_manager_list = ctk.CTkScrollableFrame(window)
        self.library_manager_list.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=16,
            pady=(0, 16),
        )
        self.library_manager_list.grid_columnconfigure(0, weight=1)

        self._populate_library_manager()

    def _populate_library_manager(self):
        if (
            self.current_library_manager is None
            or not self.current_library_manager.winfo_exists()
        ):
            return

        for child in self.library_manager_list.winfo_children():
            child.destroy()

        files = self._library_files()
        recent = self.library_metadata.get("recent_documents", [])
        collections = self.library_metadata.setdefault("collections", {})
        collection_values = sorted(
            {
                value
                for value in collections.values()
                if str(value).strip()
            }
        )
        choices = ["Unassigned", "New collection..."] + collection_values

        indexed_books = set()
        documents_file = self.data_path / "documents.pkl"
        if documents_file.exists():
            try:
                with documents_file.open("rb") as file:
                    for document in pickle.load(file):
                        indexed_books.add(
                            str(document.metadata.get("book", ""))
                        )
            except Exception:
                pass

        for row_index, book in enumerate(files):
            path = self.base_path / "library" / book
            row = ctk.CTkFrame(
                self.library_manager_list,
                corner_radius=10,
            )
            row.grid(
                row=row_index,
                column=0,
                sticky="ew",
                padx=4,
                pady=4,
            )
            row.grid_columnconfigure(0, weight=1)

            recent_mark = " · Recent" if book in recent[:5] else ""
            indexed = "Indexed" if book in indexed_books else "Needs rebuild"
            size_mb = path.stat().st_size / (1024 * 1024)

            ctk.CTkLabel(
                row,
                text=book,
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="w",
            ).grid(
                row=0,
                column=0,
                sticky="w",
                padx=10,
                pady=(7, 0),
            )

            ctk.CTkLabel(
                row,
                text=f"{path.suffix.upper()[1:]} · {size_mb:.1f} MB · "
                     f"{indexed}{recent_mark}",
                text_color=("#745F78", "#BFAFC2"),
                font=ctk.CTkFont(size=10),
                anchor="w",
            ).grid(
                row=1,
                column=0,
                sticky="w",
                padx=10,
                pady=(0, 7),
            )

            collection_menu = ctk.CTkOptionMenu(
                row,
                values=choices,
                width=135,
                height=28,
                command=lambda value, name=book: (
                    self._assign_collection(name, value)
                ),
            )
            collection_menu.set(
                collections.get(book) or "Unassigned"
            )
            collection_menu.grid(
                row=0,
                column=1,
                rowspan=2,
                padx=4,
                pady=7,
            )

            ctk.CTkButton(
                row,
                text="Summary",
                width=72,
                height=28,
                command=lambda name=book: (
                    self._request_document_summary(name)
                ),
            ).grid(row=0, column=2, rowspan=2, padx=3)

            ctk.CTkButton(
                row,
                text="Questions",
                width=76,
                height=28,
                command=lambda name=book: (
                    self._request_suggested_questions(name)
                ),
            ).grid(row=0, column=3, rowspan=2, padx=3)

            ctk.CTkButton(
                row,
                text="Open",
                width=55,
                height=28,
                fg_color="transparent",
                border_width=1,
                command=lambda file_path=path: os.startfile(
                    str(file_path)
                ),
            ).grid(row=0, column=4, rowspan=2, padx=3)

            ctk.CTkButton(
                row,
                text="Remove",
                width=65,
                height=28,
                fg_color="#A33A56",
                hover_color="#8D3048",
                text_color="#FFFFFF",
                command=lambda name=book: (
                    self._remove_library_file(name)
                ),
            ).grid(
                row=0,
                column=5,
                rowspan=2,
                padx=(3, 8),
            )

    def _assign_collection(self, book, value):
        if value == "New collection...":
            dialog = ctk.CTkInputDialog(
                text="Collection name:",
                title="New collection",
            )
            value = (dialog.get_input() or "").strip()

        collections = self.library_metadata.setdefault(
            "collections",
            {},
        )

        if not value or value == "Unassigned":
            collections.pop(book, None)
        else:
            collections[book] = value

        self._save_library_metadata()
        self._refresh_document_selector()
        self._populate_library_manager()

    def _remove_library_file(self, book):
        if not messagebox.askyesno(
            "Remove document",
            f"Remove {book} from the library?",
        ):
            return

        path = self.base_path / "library" / book
        try:
            path.unlink()
        except OSError as error:
            messagebox.showerror("Could not remove file", str(error))
            return

        self.library_metadata.get("collections", {}).pop(book, None)
        self.library_metadata.get("summaries", {}).pop(book, None)
        recent = self.library_metadata.get("recent_documents", [])
        if book in recent:
            recent.remove(book)

        self._save_library_metadata()
        self._cleanup_stale_library_data()
        self._refresh_document_selector()
        self._check_library_changes()
        self._populate_library_manager()

    def _request_document_summary(self, book):
        cached = self.library_metadata.get("summaries", {}).get(book)
        if cached:
            self._show_text_window(
                f"Summary · {book}",
                cached,
            )
            return

        self.status.configure(text=f"Summarizing {book}...")
        threading.Thread(
            target=self._document_summary_worker,
            args=(book,),
            daemon=True,
        ).start()

    def _document_summary_worker(self, book):
        try:
            summary = summarize_document(
                book,
                progress_callback=self._queue_progress,
            )
            self.result_queue.put(
                ("document_summary", summary, book, None)
            )
        except Exception as error:
            self.result_queue.put(
                ("feature_error", str(error), book, None)
            )

    def _request_suggested_questions(self, book):
        self.status.configure(
            text=f"Generating questions for {book}..."
        )
        threading.Thread(
            target=self._suggested_questions_worker,
            args=(book,),
            daemon=True,
        ).start()

    def _suggested_questions_worker(self, book):
        try:
            questions = suggest_questions(
                book,
                progress_callback=self._queue_progress,
            )
            self.result_queue.put(
                ("suggested_questions", questions, book, None)
            )
        except Exception as error:
            self.result_queue.put(
                ("feature_error", str(error), book, None)
            )

    def _show_text_window(self, title, text):
        window = ctk.CTkToplevel(self)
        window.title(title)
        window.geometry("700x500")
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(0, weight=1)

        box = ctk.CTkTextbox(window, wrap="word")
        box.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=14,
            pady=14,
        )
        box.insert("1.0", text)
        box.configure(state="disabled")

    def _show_questions_window(self, book, questions):
        window = ctk.CTkToplevel(self)
        window.title(f"Suggested questions · {book}")
        window.geometry("620x410")
        window.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            window,
            text=f"Questions for {book}",
            font=ctk.CTkFont(size=17, weight="bold"),
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=16,
            pady=(16, 8),
        )

        for index, question in enumerate(questions, start=1):
            ctk.CTkButton(
                window,
                text=question,
                anchor="w",
                height=38,
                command=lambda value=question: (
                    self._use_suggested_question(value, book, window)
                ),
            ).grid(
                row=index,
                column=0,
                sticky="ew",
                padx=16,
                pady=4,
            )

    def _use_suggested_question(self, question, book, window):
        self.document_menu.set(book)
        self.entry.delete("1.0", "end")
        self.entry.insert("1.0", question)
        window.destroy()
        self.entry.focus()

    def _show_source_preview(self, source):
        preview_window = ctk.CTkToplevel(self)
        preview_window.title("Source preview")
        self._apply_window_icon(preview_window)
        preview_window.geometry("720x470")
        preview_window.minsize(560, 380)
        preview_window.transient(self)
        preview_window.grab_set()
        preview_window.grid_columnconfigure(0, weight=1)
        preview_window.grid_rowconfigure(1, weight=1)

        book = str(source.get("book", "Unknown"))
        page = source.get("page", "?")
        file_type = str(source.get("file_type", "")).lower()
        location = (
            f"Page {page}" if file_type == "pdf"
            else f"Section {page}"
        )

        header = ctk.CTkFrame(
            preview_window,
            fg_color="transparent",
        )
        header.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=18,
            pady=(16, 8),
        )
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text=book,
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text=location,
            text_color=("#745F78", "#BFAFC2"),
            font=ctk.CTkFont(size=11),
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        preview_box = ctk.CTkTextbox(
            preview_window,
            wrap="word",
            font=ctk.CTkFont(size=13),
        )
        preview_box.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=18,
            pady=(0, 10),
        )
        preview_text = (
            source.get("preview")
            or "No preview text is available."
        )
        preview_box.insert("1.0", preview_text)
        self._highlight_preview_matches(
            preview_box,
            preview_text,
            self.last_question,
        )
        preview_box.configure(state="disabled")

        actions = ctk.CTkFrame(
            preview_window,
            fg_color="transparent",
        )
        actions.grid(
            row=2,
            column=0,
            sticky="e",
            padx=18,
            pady=(0, 16),
        )

        ctk.CTkButton(
            actions,
            text="Close",
            width=90,
            command=preview_window.destroy,
        ).grid(row=0, column=0, padx=(0, 8))

        ctk.CTkButton(
            actions,
            text="Open document",
            width=125,
            command=lambda: self._open_source(
                source.get("pdf", ""),
                source.get("page", "?"),
            ),
        ).grid(row=0, column=1)

    def _open_source(self, file_path, page):
        if not file_path:
            self.status.configure(text="Source file path is missing")
            return

        source = Path(file_path).resolve()

        if not source.exists():
            self.status.configure(text=f"Could not find: {source}")
            return

        try:
            if source.suffix.lower() == ".pdf":
                uri = source.as_uri()

                if str(page).isdigit():
                    uri = f"{uri}#page={int(page)}"

                edge_candidates = [
                    Path(os.environ.get("PROGRAMFILES(X86)", ""))
                    / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                    Path(os.environ.get("PROGRAMFILES", ""))
                    / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                    Path(os.environ.get("LOCALAPPDATA", ""))
                    / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                ]

                edge_path = next(
                    (path for path in edge_candidates if path.exists()),
                    None,
                )

                if edge_path is not None:
                    subprocess.Popen(
                        [str(edge_path), "--new-window", uri],
                        close_fds=True,
                    )
                else:
                    webbrowser.open(uri, new=2)

                self.status.configure(
                    text=f"Opened {source.name} at page {page}"
                )
            else:
                os.startfile(str(source))
                self.status.configure(text="Opened source document")

        except OSError as error:
            self.status.configure(text=f"Could not open source: {error}")

    def _on_close(self):
        if self._closing:
            return

        self._closing = True
        self.stop_event.set()

        try:
            self._stop_thinking_bubble()
        except Exception:
            pass

        try:
            active_chat = self._active_chat()
            active_chat["conversation"] = get_conversation()
        except Exception:
            pass

        try:
            self._save_chats()
        except Exception:
            pass

        try:
            self.settings["window_geometry"] = self.geometry()
            self._save_settings()
        except Exception:
            pass

        try:
            self._cleanup_stale_library_data()
        except Exception:
            pass

        try:
            self.status.configure(text="Closing Calypso...")
            self.update_idletasks()
        except Exception:
            pass

        self.after(120, self.destroy)


def start_gui():
    app = CalypsoGUI()
    app.mainloop()


if __name__ == "__main__":
    start_gui()