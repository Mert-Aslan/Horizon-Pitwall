import sys


def run() -> int:
    try:
        from backend.launcher.app import main

        return main()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - last resort instead of the raw bootloader traceback window
        import logging
        import traceback

        text = traceback.format_exc()
        logging.getLogger("pitwall").critical("event=fatal\n%s", text)
        try:
            from backend import config
            from backend.launcher import winapi

            log = config.default_data_dir() / "logs"
            winapi.message_box(f"Horizon Pitwall stopped because of an unexpected error.\n\n{text.strip().splitlines()[-1]}\n\n"
                               f"Details are in the log folder:\n{log}")
            log.mkdir(parents=True, exist_ok=True)
            with (log / "crash.log").open("a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(run())
