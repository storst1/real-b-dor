import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Сезон · локальная футбольная статистика")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    uvicorn.run("season_ledger.web:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
