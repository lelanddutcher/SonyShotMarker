#!/usr/bin/env python3
"""Shot Mark Embedder for Windows.

Tkinter shell around the same Python shot-mark engine used by the CLI tools. It is
intentionally dependency-light: the embed path is pure Python and never touches the
original clips. tkinterdnd2 is optional; when packaged with PyInstaller the drop zone
accepts files, and without it the Add Clips button still works.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path

try:
    from tkinter import BOTH, BOTTOM, DISABLED, END, LEFT, NORMAL, RIGHT, TOP, X, filedialog, messagebox
    import tkinter as tk
    from tkinter import ttk
except ModuleNotFoundError:  # Linux CI images may omit python3-tk; Windows packaging must include it.
    BOTH = BOTTOM = DISABLED = END = LEFT = NORMAL = RIGHT = TOP = X = ""  # type: ignore[assignment]
    filedialog = messagebox = None  # type: ignore[assignment]
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
TOOLS = ROOT / "tools"
BRANDING = ROOT / "branding" / "cat sticking tongue out.png"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import embed_batch  # type: ignore[import-not-found]  # noqa: E402

try:  # optional, packaged in the Windows release build
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
except Exception:  # pragma: no cover - exercised only when dependency absent
    DND_FILES = None
    TkinterDnD = None

APP_TITLE = "Shot Mark Embedder"
OUT_FOLDER_NAME = embed_batch.OUT_FOLDER_NAME
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
TONGUE = "#f7578c"
INK = "#1f1f1f"
INK_SOFT = "#666666"
HAIRLINE = "#8c8c8c"


def resource_path(path: Path) -> Path:
    return Path(getattr(sys, "_MEIPASS", ROOT)) / path


def parse_drop_files(raw: str) -> list[str]:
    """Parse Tk DND's Windows-friendly file-list format."""
    root = tk.Tcl()
    root.withdraw()
    try:
        return [str(Path(p)) for p in root.tk.splitlist(raw)]
    finally:
        root.destroy()


class ShotMarkApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.files: list[Path] = []
        self.output_dir: Path | None = None
        self.running = False
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cat_image: tk.PhotoImage | None = None

        root.title(APP_TITLE)
        root.geometry("640x580")
        root.minsize(640, 580)
        root.configure(bg="#f6f6f6")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.build_ui()
        self.poll_events()

    def build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Pink.TButton", foreground="white", background=TONGUE, borderwidth=0, focusthickness=0)
        style.map("Pink.TButton", background=[("disabled", "#b7b7b7"), ("active", "#e6497d")])
        style.configure("Plain.TButton", padding=(12, 7))

        frame = tk.Frame(self.root, bg="#f6f6f6", padx=28, pady=24)
        frame.pack(fill=BOTH, expand=True)

        tk.Label(
            frame,
            text="SHOT MARK EMBEDDER",
            bg="#f6f6f6",
            fg=INK,
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            frame,
            text="Drop already-offloaded Sony clips → a “footage embedded markers” folder for Premiere.",
            bg="#f6f6f6",
            fg=INK_SOFT,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(0, 18))

        self.drop = tk.Frame(frame, bg="#ffffff", highlightbackground=HAIRLINE, highlightthickness=2, height=148)
        self.drop.pack(fill=X)
        self.drop.pack_propagate(False)
        self.drop_label = tk.Label(
            self.drop,
            text="Drag your Sony footage here\n(or use Add Clips…)",
            bg="#ffffff",
            fg=INK_SOFT,
            justify="center",
            font=("Segoe UI", 11),
        )
        self.drop_label.pack(expand=True)

        if TkinterDnD is not None and DND_FILES is not None:
            self.drop.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self.drop.dnd_bind("<<Drop>>", self.on_drop)  # type: ignore[attr-defined]
            self.drop_label.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self.drop_label.dnd_bind("<<Drop>>", self.on_drop)  # type: ignore[attr-defined]

        buttons = tk.Frame(frame, bg="#f6f6f6")
        buttons.pack(fill=X, pady=(14, 0))
        ttk.Button(buttons, text="Add Clips…", style="Plain.TButton", command=self.add_clips).pack(side=LEFT)
        ttk.Button(buttons, text="Clear", style="Plain.TButton", command=self.clear_files).pack(side=LEFT, padx=(8, 0))
        ttk.Button(buttons, text="Output…", style="Plain.TButton", command=self.choose_output).pack(side=LEFT, padx=(18, 0))
        self.output_label = tk.Label(buttons, text="no output folder chosen", bg="#f6f6f6", fg=INK_SOFT, anchor="w")
        self.output_label.pack(side=LEFT, fill=X, expand=True, padx=(10, 0))

        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=1.0, value=0.0)
        self.progress.pack(fill=X, pady=(24, 6))
        self.status = tk.Label(frame, text="", bg="#f6f6f6", fg=INK, anchor="w", font=("Consolas", 9))
        self.status.pack(fill=X)

        log_frame = tk.Frame(frame, bg="#f6f6f6")
        log_frame.pack(fill=BOTH, expand=True, pady=(10, 12))
        self.log = tk.Text(log_frame, height=8, wrap="word", bg="#ffffff", fg=INK, relief="flat", padx=10, pady=8)
        self.log.pack(fill=BOTH, expand=True)

        bottom = tk.Frame(frame, bg="#f6f6f6")
        bottom.pack(side=BOTTOM, fill=X)
        self.cat_label = tk.Label(bottom, bg="#f6f6f6")
        cat = resource_path(Path("branding") / "cat sticking tongue out.png")
        if cat.exists():
            try:
                self.cat_image = tk.PhotoImage(file=str(cat))
                # Subsample roughly to Swift UI's small corner-cat scale.
                max_dim = max(self.cat_image.width(), self.cat_image.height())
                factor = max(1, max_dim // 168)
                self.cat_image = self.cat_image.subsample(factor)
                self.cat_label.configure(image=self.cat_image)
            except tk.TclError:
                self.cat_label.configure(text="🐈", fg=TONGUE, font=("Segoe UI Emoji", 32))
        self.cat_label.pack(side=LEFT)

        self.run_button = ttk.Button(bottom, text="Embed Markers", style="Pink.TButton", command=self.run)
        self.run_button.pack(side=RIGHT, ipadx=14, ipady=7)
        self.update_state()

    def on_drop(self, event) -> None:  # type: ignore[no-untyped-def]
        self.add_paths(parse_drop_files(event.data))

    def add_clips(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose Sony clips",
            filetypes=[("Video clips", "*.mp4 *.MP4 *.mov *.MOV *.m4v *.M4V"), ("All files", "*.*")],
        )
        self.add_paths(paths)

    def add_paths(self, paths) -> None:  # type: ignore[no-untyped-def]
        added = 0
        known = {p.resolve() for p in self.files if p.exists()}
        for raw in paths:
            p = Path(raw)
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS and p.resolve() not in known:
                self.files.append(p)
                known.add(p.resolve())
                added += 1
        if added == 0 and paths:
            self.write_log("no supported .MP4/.MOV/.M4V clips added")
        self.update_state()

    def clear_files(self) -> None:
        if self.running:
            return
        self.files.clear()
        self.progress.configure(value=0.0)
        self.status.configure(text="")
        self.update_state()

    def choose_output(self) -> None:
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.output_dir = Path(path)
            self.output_label.configure(text=str(self.output_dir))
            self.update_state()

    def can_run(self) -> bool:
        return bool(self.files and self.output_dir and not self.running)

    def update_state(self) -> None:
        if not self.files:
            text = "Drag your Sony footage here\n(or use Add Clips…)"
        else:
            shown = ", ".join(p.name for p in self.files[:3])
            if len(self.files) > 3:
                shown += f" +{len(self.files) - 3}"
            text = f"{len(self.files)} file{'s' if len(self.files) != 1 else ''} · {shown}"
        self.drop_label.configure(text=text)
        self.run_button.configure(state=NORMAL if self.can_run() else DISABLED)

    def run(self) -> None:
        if not self.can_run() or self.output_dir is None:
            return
        self.running = True
        self.progress.configure(value=0.0)
        self.status.configure(text="Starting…")
        self.log.delete("1.0", END)
        self.run_button.configure(text="Embedding…", state=DISABLED)
        files = list(self.files)
        out = self.output_dir
        worker = threading.Thread(target=self.worker, args=(files, out), daemon=True)
        worker.start()

    def worker(self, files: list[Path], out: Path) -> None:
        dest_dir = out / OUT_FOLDER_NAME
        dest_dir.mkdir(parents=True, exist_ok=True)
        total = len(files)
        embedded = 0
        for index, src in enumerate(files, start=1):
            self.events.put(("status", f"Embedding {src.name}…  ({index}/{total})"))
            rec, msg = embed_batch.process_one(str(src), str(dest_dir))
            if rec["status"] == "embedded":
                embedded += 1
            self.events.put(("log", msg))
            self.events.put(("progress", index / total))
        self.events.put(("done", (embedded, total, dest_dir)))

    def poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status.configure(text=str(payload))
                elif kind == "log":
                    self.write_log(str(payload))
                elif kind == "progress":
                    self.progress.configure(value=float(payload))  # type: ignore[arg-type]
                elif kind == "done":
                    embedded, total, dest = payload  # type: ignore[misc]
                    self.running = False
                    self.status.configure(text=f"Done — {embedded}/{total} embedded into “{OUT_FOLDER_NAME}”.")
                    self.run_button.configure(text="Embed Markers")
                    self.update_state()
                    if embedded:
                        messagebox.showinfo(APP_TITLE, f"{embedded}/{total} embedded.\n\nOutput:\n{dest}")
                    else:
                        messagebox.showwarning(APP_TITLE, "No clips were embedded. Check the log for skipped/error details.")
        except queue.Empty:
            pass
        self.root.after(80, self.poll_events)

    def write_log(self, line: str) -> None:
        self.log.insert(END, line + "\n")
        self.log.see(END)

    def on_close(self) -> None:
        if self.running and not messagebox.askyesno(APP_TITLE, "Embedding is still running. Close anyway?"):
            return
        self.root.destroy()


def smoke() -> int:
    """CI/package smoke that avoids opening a window."""
    required = [ROOT / "tools" / "sony_shotmark.py", ROOT / "tools" / "embed_batch.py"]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        print("missing bundled files: " + ", ".join(missing), file=sys.stderr)
        return 1
    print(f"{APP_TITLE} smoke ok; tkinter={'yes' if tk is not None else 'no'}; dnd={'yes' if TkinterDnD is not None else 'no'}; root={ROOT}")
    return 0


def main() -> None:
    if "--smoke" in sys.argv:
        raise SystemExit(smoke())
    if tk is None or ttk is None:
        raise SystemExit("tkinter is required to launch the Windows app")
    root_cls = TkinterDnD.Tk if TkinterDnD is not None else tk.Tk
    root = root_cls()
    app = ShotMarkApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
