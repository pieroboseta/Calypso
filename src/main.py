import json
import os
import sys
from pathlib import Path


os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("TQDM_DISABLE", "1")


def _emit(event_type, **values):
    payload = {"type": event_type, **values}
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _run_rebuild_worker():
    try:
        from ingest import ingest_library
        from database import build_database

        def ingest_progress(percent, message):
            mapped = 5 + int(int(percent) * 0.35)
            _emit("progress", percent=mapped, message=message)

        def database_progress(percent, message):
            mapped = 40 + int(int(percent) * 0.55)
            _emit("progress", percent=mapped, message=message)

        ingest_library(progress_callback=ingest_progress)
        build_database(progress_callback=database_progress)
        _emit("progress", percent=96, message="Index completed")
        _emit("done")
        return 0

    except Exception as error:
        _emit("error", message=str(error))
        return 1


def _project_folder() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent.parent


def _round_window(window, radius=28):
    if sys.platform != "win32":
        return

    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        width = window.winfo_width()
        height = window.winfo_height()

        region = ctypes.windll.gdi32.CreateRoundRectRgn(
            0,
            0,
            width + 1,
            height + 1,
            radius,
            radius,
        )
        ctypes.windll.user32.SetWindowRgn(hwnd, region, True)
    except Exception:
        pass


def _start_with_splash():
    import tkinter as tk
    from PIL import Image, ImageTk

    settings_path = _project_folder() / "data" / "settings.json"
    saved_appearance = "Dark"

    try:
        if settings_path.exists():
            saved_settings = json.loads(
                settings_path.read_text(encoding="utf-8")
            )
            saved_appearance = str(
                saved_settings.get("appearance", "Dark")
            )
    except (OSError, json.JSONDecodeError):
        saved_appearance = "Dark"

    use_light_palette = saved_appearance.lower() == "light"

    if use_light_palette:
        palette = {
            "background": "#FFF9FC",
            "title": "#392E3E",
            "subtitle": "#806B83",
            "trough": "#EADCEB",
            "progress": "#DFA0D4",
            "percentage": "#6E5872",
            "status": "#8B748E",
        }
    else:
        palette = {
            "background": "#1C1621",
            "title": "#FFF7FD",
            "subtitle": "#BFAFC2",
            "trough": "#382B40",
            "progress": "#C78BCC",
            "percentage": "#E9DDEA",
            "status": "#A994AD",
        }

    splash = tk.Tk()
    splash.title("Calypso")
    splash.geometry("460x280")
    splash.resizable(False, False)
    splash.configure(bg=palette["background"])
    splash.overrideredirect(True)

    screen_width = splash.winfo_screenwidth()
    screen_height = splash.winfo_screenheight()
    x = int((screen_width - 460) / 2)
    y = int((screen_height - 280) / 2)
    splash.geometry(f"460x280+{x}+{y}")

    splash.update_idletasks()
    _round_window(splash, radius=34)

    logo_path = _project_folder() / "assets" / "logo.png"
    logo_image = None

    if logo_path.exists():
        try:
            source_logo = Image.open(logo_path).convert("RGBA")

            display_size = 92
            source_logo.thumbnail(
                (display_size, display_size),
                Image.Resampling.LANCZOS,
            )

            canvas = Image.new(
                "RGBA",
                (display_size, display_size),
                (0, 0, 0, 0),
            )
            x_offset = (display_size - source_logo.width) // 2
            y_offset = (display_size - source_logo.height) // 2
            canvas.alpha_composite(
                source_logo,
                (x_offset, y_offset),
            )

            logo_image = ImageTk.PhotoImage(canvas)
        except Exception:
            logo_image = None

    if logo_image is not None:
        logo_label = tk.Label(
            splash,
            image=logo_image,
            bg=palette["background"],
            borderwidth=0,
        )
        logo_label.image = logo_image
        logo_label.pack(pady=(16, 4))

    title = tk.Label(
        splash,
        text="Calypso",
        bg=palette["background"],
        fg=palette["title"],
        font=("Segoe UI", 22, "bold"),
    )
    title.pack(pady=(0, 0))

    subtitle = tk.Label(
        splash,
        text="Your private, source-grounded offline AI",
        bg=palette["background"],
        fg=palette["subtitle"],
        font=("Segoe UI", 10),
    )
    subtitle.pack(pady=(1, 16))

    progress_width = 350
    progress_height = 12
    progress_radius = 6

    progress = tk.Canvas(
        splash,
        width=progress_width,
        height=progress_height,
        bg=palette["background"],
        highlightthickness=0,
        borderwidth=0,
    )
    progress.pack()

    def rounded_rectangle(
        canvas,
        x1,
        y1,
        x2,
        y2,
        radius,
        **kwargs,
    ):
        radius = max(
            0,
            min(radius, (x2 - x1) / 2, (y2 - y1) / 2),
        )

        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]

        return canvas.create_polygon(
            points,
            smooth=True,
            splinesteps=24,
            **kwargs,
        )

    rounded_rectangle(
        progress,
        0,
        0,
        progress_width,
        progress_height,
        progress_radius,
        fill=palette["trough"],
        outline="",
    )

    progress_fill = None

    def set_progress(value):
        nonlocal progress_fill

        value = max(0.0, min(100.0, float(value)))
        fill_width = progress_width * value / 100

        if progress_fill is not None:
            progress.delete(progress_fill)
            progress_fill = None

        if fill_width <= 0:
            return

        progress_fill = rounded_rectangle(
            progress,
            0,
            0,
            max(progress_height, fill_width),
            progress_height,
            progress_radius,
            fill=palette["progress"],
            outline="",
        )

    percentage = tk.Label(
        splash,
        text="0%",
        bg=palette["background"],
        fg=palette["percentage"],
        font=("Segoe UI", 9, "bold"),
    )
    percentage.pack(pady=(7, 0))

    status = tk.Label(
        splash,
        text="Starting Calypso...",
        bg=palette["background"],
        fg=palette["status"],
        font=("Segoe UI", 9),
    )
    status.pack(pady=(2, 0))

    def show_stage(value, message):
        set_progress(value)
        percentage.configure(text=f"{int(value)}%")
        status.configure(text=message)
        splash.update_idletasks()
        splash.update()

    start_gui = None

    try:
        # Tkinter stays on the main thread. Heavy AI libraries and model
        # weights are also loaded on this same thread to avoid unsafe
        # PyTorch/Tkinter thread interactions on Windows.
        show_stage(18, "Loading interface...")
        from gui import start_gui

        show_stage(55, "Loading AI components...")
        from core.ai import preload_database

        show_stage(78, "Loading AI weights and library...")
        preload_database()

        show_stage(96, "Finishing startup...")
        splash.update_idletasks()

        show_stage(100, "Ready")
        splash.after(300, splash.destroy)
        splash.mainloop()

    except Exception as error:
        status.configure(
            text=f"Startup error: {error}",
            fg="#F08AA1",
            wraplength=400,
            justify="center",
        )
        percentage.configure(text="Error")
        splash.after(5000, splash.destroy)
        splash.mainloop()
        start_gui = None

    if start_gui is not None:
        start_gui()


if __name__ == "__main__":
    if "--rebuild-worker" in sys.argv:
        raise SystemExit(_run_rebuild_worker())

    _start_with_splash()