"""python -m evidencekg.app --config /absolute/private/config.json"""

import argparse

from .api import create_app
from .config import AppConfig


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Local Graf workspace server")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = AppConfig.load(args.config)
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        proxy_headers=False,
        access_log=False,
        server_header=False,
        timeout_graceful_shutdown=15,
    )


if __name__ == "__main__":
    main()
