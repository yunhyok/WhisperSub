"""Entry point.  GUI:  WhisperSub.exe [files...]      CLI:  whispersub-cli.exe FILE... [options]"""
from __future__ import annotations

import argparse
import logging
import sys

from . import APP_NAME, __version__
from .runtime import setup_dll_paths, setup_logging


def cli(argv: list[str]) -> int:
    from . import pipeline as pl
    ap = argparse.ArgumentParser(prog="whispersub-cli", description=f"{APP_NAME} {__version__} — media → SRT")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--model", default="large-v3", choices=pl.MODEL_CHOICES)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--lang", default="ja")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--max-dur", type=float, default=10.0)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--no-tsv", action="store_true")
    ap.add_argument("--translate", action="store_true")
    ap.add_argument("--llm-url", default="http://localhost:1234/v1")
    ap.add_argument("--llm-model", default="")
    ap.add_argument("--llm-key", default="")
    ap.add_argument("--target", default="ko")
    ap.add_argument("--apply", metavar="TSV", help="apply a translation TSV to the first FILE and exit")
    ap.add_argument("--start", type=float)
    ap.add_argument("--end", type=float)
    a = ap.parse_args(argv)

    st = pl.Settings(model=a.model, device=a.device, language=a.lang, fast=a.fast, max_dur=a.max_dur,
                     overwrite=a.overwrite, write_tsv=not a.no_tsv, translate=a.translate, llm_url=a.llm_url,
                     llm_model=a.llm_model, llm_api_key=a.llm_key, target_lang=a.target)

    def out(msg):
        print(msg, flush=True)
        logging.getLogger("whispersub.cli").info(msg)

    if a.apply:
        pl.apply_translation(a.files[0], a.apply, st, out)
        return 0
    tr = pl.Transcriber()
    rc = 0
    for f in a.files:
        try:
            pl.process_file(f, st, tr, logger=out,
                            progress=lambda stage, v: None, start=a.start, end=a.end)
        except Exception as e:  # noqa: BLE001
            out(f"오류: {f}: {type(e).__name__}: {e}")
            rc = 1
    return rc


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    setup_logging()
    added = setup_dll_paths()
    logging.getLogger("whispersub").info("%s %s start; dll dirs: %s", APP_NAME, __version__, added)
    argv = sys.argv[1:]
    if argv and argv[0] == "--cli":
        return cli(argv[1:])
    force_gui = "--gui" in argv
    if force_gui:
        argv = [a for a in argv if a != "--gui"]
    if not force_gui and (sys.executable.lower().endswith("whispersub-cli.exe") or "--help" in argv or "-h" in argv):
        return cli(argv)
    try:
        from .gui import run_gui
        return run_gui([sys.argv[0]] + argv)
    except Exception:  # noqa: BLE001 - make crashes diagnosable from the log file in windowed mode
        import traceback
        logging.getLogger("whispersub").error("GUI crashed:\n%s", traceback.format_exc())
        raise


if __name__ == "__main__":
    sys.exit(main())
