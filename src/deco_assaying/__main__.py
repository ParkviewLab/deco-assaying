"""Entrypoint: `python -m deco_assaying` -> uvicorn."""

import logging

import uvicorn

from deco_assaying.config import HOST, PORT


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    uvicorn.run("deco_assaying.app:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
