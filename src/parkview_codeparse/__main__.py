"""Entrypoint: `python -m parkview_codeparse` -> uvicorn."""

import logging

import uvicorn

from parkview_codeparse.config import HOST, PORT


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    uvicorn.run("parkview_codeparse.app:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
