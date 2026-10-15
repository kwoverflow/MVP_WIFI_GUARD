"""Create a fresh local Compose configuration without printing or replacing secrets."""
import argparse
import os
from pathlib import Path
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("compose/.env"))
    parser.add_argument("--checkpoint", type=Path, required=True)
    args = parser.parse_args()
    checkpoint = args.checkpoint.resolve(strict=True)
    if not checkpoint.is_file():
        parser.error("checkpoint must be an existing file")
    values = {key: secrets.token_urlsafe(48) for key in
              ("PG_PASSWORD", "TSDB_PASSWORD", "MINIO_PASSWORD", "GRAFANA_PASSWORD", "JWT_SECRET")}
    values.update(ALLOW_FALL_SIMULATE="0", MODEL_CHECKPOINT_HOST=checkpoint.as_posix(),
                  WEB_HOST_BIND="127.0.0.1", FEATURE_BACKEND="reference")
    values["DATABASE_URL"] = f"postgresql+psycopg://wifiguard:{values['PG_PASSWORD']}@127.0.0.1:5432/wifiguard"
    values["DATABASE_URL_TEST"] = f"postgresql+psycopg://wifiguard:{values['PG_PASSWORD']}@127.0.0.1:5432/wifiguard_test"
    template = Path(__file__).resolve().parents[1] / "compose/.env.example"
    lines = template.read_text(encoding="utf-8").splitlines()
    rendered = "\n".join(f"{line.split('=', 1)[0]}={values[line.split('=', 1)[0]]}"
        if "=" in line and line.split("=", 1)[0] in values else line for line in lines) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.error("output already exists; existing deployment secrets are never replaced")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(rendered)
    print(f"Created configuration: {args.output.resolve()} (secret values hidden)")


if __name__ == "__main__":
    main()
