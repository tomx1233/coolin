#!/usr/bin/env python3
"""Coolin - GUI edition.

A small tkinter application (tkinter ships with the standard Windows Python
installer, so no extra GUI dependencies) that lets you insert one or more
audio files and converts them into OGG files with a rewritten declared
duration: Discord's audio player stops after ~2 seconds, VLC plays the full
song, and FMOD / Windows' default players refuse to play the result.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from coolin import __version__, pipeline

AUDIO_FILETYPES = [
    ("Audio files", "*.mp3 *.wav *.flac *.ogg *.oga *.opus *.m4a *.aac "
                     "*.wma *.ac3 *.mp4 *.mkv *.webm *.mov *.avi"),
    ("All files", "*.*"),
]

_DONE_SENTINEL = "__coolin_done__"
_PROGRESS_TICK = "__coolin_tick__"


class CoolinApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Coolin {__version__} - Discord 2-Second OGG Converter")
        self.geometry("720x540")
        self.minsize(600, 440)

        self.log_queue: "queue.Queue" = queue.Queue()
        self.worker: threading.Thread | None = None

        self._build_ui()
        self.after(100, self._drain_log_queue)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True)

        # -- file list ------------------------------------------------------
        files_frame = ttk.LabelFrame(root, text=" 1. Insert audio file(s) ")
        files_frame.pack(fill="both", expand=True, **pad)

        self.file_listbox = tk.Listbox(
            files_frame, selectmode="extended", activestyle="none"
        )
        self.file_listbox.pack(side="left", fill="both", expand=True,
                               padx=(8, 2), pady=8)
        list_scroll = ttk.Scrollbar(files_frame, orient="vertical",
                                    command=self.file_listbox.yview)
        list_scroll.pack(side="left", fill="y", pady=8)
        self.file_listbox.configure(yscrollcommand=list_scroll.set)

        buttons = ttk.Frame(files_frame)
        buttons.pack(side="left", fill="y", padx=(2, 8), pady=8)
        ttk.Button(buttons, text="Add files...",
                   command=self.on_add_files).pack(fill="x", pady=(0, 4))
        ttk.Button(buttons, text="Remove selected",
                   command=self.on_remove_selected).pack(fill="x", pady=4)
        ttk.Button(buttons, text="Clear",
                   command=self.on_clear_files).pack(fill="x", pady=(4, 0))

        # -- options ----------------------------------------------------------
        options_frame = ttk.LabelFrame(root, text=" 2. Options ")
        options_frame.pack(fill="x", **pad)
        options_frame.columnconfigure(1, weight=1)

        ttk.Label(options_frame, text="Output folder:").grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        self.output_dir_var = tk.StringVar()
        ttk.Entry(options_frame, textvariable=self.output_dir_var).grid(
            row=0, column=1, sticky="ew", padx=4, pady=(8, 4))
        ttk.Button(options_frame, text="Browse...",
                   command=self.on_browse_output).grid(
            row=0, column=2, padx=(4, 8), pady=(8, 4))

        self.same_folder_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            options_frame,
            text="Write next to each input file (ignores the folder above)",
            variable=self.same_folder_var,
        ).grid(row=1, column=1, columnspan=2, sticky="w", padx=4)

        ttk.Label(options_frame, text="Asset name (optional):").grid(
            row=2, column=0, sticky="w", padx=8, pady=(4, 4))
        self.asset_name_var = tk.StringVar()
        ttk.Entry(options_frame, textvariable=self.asset_name_var).grid(
            row=2, column=1, sticky="ew", padx=4, pady=(4, 4))
        ttk.Label(options_frame, text="max 50 chars (Roblox/Discord limit)").grid(
            row=2, column=2, sticky="w", padx=(4, 8), pady=(4, 4))

        ttk.Label(options_frame, text="Method:").grid(
            row=3, column=0, sticky="w", padx=8, pady=(4, 4))
        self.method_var = tk.StringVar(value=pipeline.METHOD_INVERT)
        ttk.Combobox(options_frame, textvariable=self.method_var, width=32,
                     values=(
                         pipeline.METHOD_INVERT,
                         pipeline.METHOD_SPEED,
                         pipeline.METHOD_INVERT_SPEED,
                         pipeline.METHOD_MULTISTREAM,
                         pipeline.METHOD_SPOOF,
                     ),
                     state="readonly").grid(row=3, column=1, sticky="w", padx=4, pady=(4, 4))
        ttk.Label(options_frame, text="spoof = Roblox sees a short song; full song restored in game").grid(
            row=3, column=2, sticky="w", padx=(4, 8), pady=(4, 4))

        ttk.Label(options_frame, text="Duration / Limit (max 6:59):").grid(
            row=4, column=0, sticky="w", padx=8, pady=(4, 8))
        controls = ttk.Frame(options_frame)
        controls.grid(row=4, column=1, columnspan=2, sticky="w",
                      padx=4, pady=(4, 8))
        self.seconds_var = tk.StringVar(value=str(pipeline.DEFAULT_FAKE_SECONDS))
        ttk.Spinbox(controls, from_=0.1, to=pipeline.MAX_SECONDS, increment=0.5, width=8,
                    textvariable=self.seconds_var).pack(side="left")
        ttk.Label(controls, text="   Codec:").pack(side="left", padx=(16, 4))
        self.codec_var = tk.StringVar(value="auto")
        ttk.Combobox(controls, textvariable=self.codec_var, width=8,
                     values=("auto", "opus", "vorbis"),
                     state="readonly").pack(side="left")

        # -- convert -----------------------------------------------------------
        action_frame = ttk.Frame(root)
        action_frame.pack(fill="x", **pad)
        self.convert_button = ttk.Button(
            action_frame, text="Convert to Discord OGG", command=self.on_convert
        )
        self.convert_button.pack(side="left")
        self.progress = ttk.Progressbar(action_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(10, 0))

        # -- log -----------------------------------------------------------------
        self.log_text = ScrolledText(root, height=10, state="disabled",
                                     font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, **pad)
        self.log(
            "Insert one or more audio files, pick a Method, and press Convert.\n"
            "invert: silent in mono previews  |  speed: physically short file, restored in game\n"
            "spoof: Roblox sees a short song, full song inside  |  multistream: Discord stops early, VLC plays all"
        )

        # -- status bar -------------------------------------------------------------
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(root, textvariable=self.status_var, anchor="w",
                  relief="sunken").pack(fill="x", side="bottom")

    # ------------------------------------------------------------- helpers
    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _drain_log_queue(self) -> None:
        """Move worker output into the UI (called on the main thread)."""
        worker_finished = False
        try:
            while True:
                item = self.log_queue.get_nowait()
                if item == _DONE_SENTINEL:
                    worker_finished = True
                elif item == _PROGRESS_TICK:
                    self.progress.configure(value=self.progress["value"] + 1)
                else:
                    self.log(str(item))
        except queue.Empty:
            pass
        if worker_finished and self.worker is not None:
            self.worker.join(timeout=1)
            self.worker = None
            self.convert_button.configure(state="normal")
        self.after(100, self._drain_log_queue)

    def _files(self):
        return list(self.file_listbox.get(0, "end"))

    # -------------------------------------------------------------- events
    def on_add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Insert audio files", filetypes=AUDIO_FILETYPES
        )
        existing = set(self._files())
        for path in paths:
            if path not in existing:
                self.file_listbox.insert("end", path)
                existing.add(path)

    def on_remove_selected(self) -> None:
        for index in reversed(self.file_listbox.curselection()):
            self.file_listbox.delete(index)

    def on_clear_files(self) -> None:
        self.file_listbox.delete(0, "end")

    def on_browse_output(self) -> None:
        chosen = filedialog.askdirectory(title="Choose output folder")
        if chosen:
            self.output_dir_var.set(chosen)
            self.same_folder_var.set(False)

    def on_convert(self) -> None:
        files = self._files()
        if not files:
            messagebox.showwarning("Coolin", "Insert at least one audio file first.")
            return
        try:
            seconds = pipeline.parse_duration(self.seconds_var.get())
        except ValueError as exc:
            messagebox.showerror(
                "Coolin",
                f"Invalid duration: {exc}\n"
                f"Duration must be greater than zero and at most 6 minutes 59 seconds ({pipeline.MAX_SECONDS}s)."
            )
            return

        raw_asset_name = self.asset_name_var.get().strip()
        if raw_asset_name and len(raw_asset_name) > pipeline.MAX_ASSET_NAME_LENGTH:
            messagebox.showerror(
                "Coolin",
                f"Asset name length is invalid ({len(raw_asset_name)} characters).\n"
                f"Asset name must not exceed {pipeline.MAX_ASSET_NAME_LENGTH} characters."
            )
            return

        self.convert_button.configure(state="disabled")
        self.progress.configure(maximum=len(files), value=0)
        self.status_var.set("Converting...")
        self.worker = threading.Thread(
            target=self._worker,
            args=(files, seconds, self.codec_var.get(),
                  self.same_folder_var.get(), self.output_dir_var.get().strip(),
                  raw_asset_name, self.method_var.get()),
            daemon=True,
        )
        self.worker.start()

    # -------------------------------------------------------------- worker
    def _worker(self, files, seconds, codec, same_folder, output_dir, asset_name, method) -> None:
        succeeded = 0
        for index, input_path in enumerate(files, 1):
            self.log_queue.put(
                f"=== [{index}/{len(files)}] {os.path.basename(input_path)} ==="
            )
            output_path = None
            # If a custom asset name is specified and converting a single file
            single_name = asset_name if (asset_name and len(files) == 1) else None
            if not same_folder and output_dir:
                output_path = pipeline.default_output_path(
                    input_path, output_dir=output_dir, custom_name=single_name
                )
            elif single_name:
                output_path = pipeline.default_output_path(
                    input_path, custom_name=single_name
                )
            try:
                result = pipeline.craft(
                    input_path,
                    output_path=output_path,
                    fake_seconds=seconds,
                    codec=codec,
                    method=method,
                    asset_name=single_name,
                    log=self.log_queue.put,
                )
                succeeded += 1
                self.log_queue.put(f"      OK: {result.output_path}")
            except Exception as exc:
                self.log_queue.put(f"      FAILED: {exc}")
            self.log_queue.put(_PROGRESS_TICK)
        self.log_queue.put(f"Done: {succeeded}/{len(files)} file(s) converted.")
        self.log_queue.put(_DONE_SENTINEL)


def main() -> None:
    app = CoolinApp()
    app.mainloop()


if __name__ == "__main__":
    main()
