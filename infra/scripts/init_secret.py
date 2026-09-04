"""Create the database password once, without storing it in local files or logs."""
import json
import secrets
import subprocess

PROJECT = "commerce-agents-dev"
SECRET = "dagster-postgres-password"


def main():
    base = ["gcloud", "--project", PROJECT]
    result = subprocess.run(
        base + ["secrets", "versions", "list", SECRET, "--filter=state=ENABLED", "--format=json"],
        check=True, capture_output=True, text=True,
    )
    if json.loads(result.stdout):
        print("Password already initialized; not rotating.")
        return
    subprocess.run(
        base + ["secrets", "versions", "add", SECRET, "--data-file=-"],
        input=secrets.token_hex(32).encode(), check=True,
    )
    print("Password initialized in Secret Manager; value not logged.")


if __name__ == "__main__":
    main()
