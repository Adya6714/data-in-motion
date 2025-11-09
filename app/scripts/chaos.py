import argparse
import time
import requests

DEFAULT_API = "http://localhost:8000"


def call(method: str, path: str, api: str):
    url = f"{api}{path}"
    resp = requests.request(method, url, timeout=5)
    resp.raise_for_status()
    return resp.json()


def scenario(api: str, endpoint: str, duration: int):
    print(f"Failing endpoint {endpoint} via {api} …")
    call("POST", f"/chaos/fail/{endpoint}", api)
    print("Waiting while endpoint is down …")
    time.sleep(duration)
    print("Recovering endpoint …")
    call("POST", f"/chaos/recover/{endpoint}", api)
    print("Chaos scenario complete.")


def main():
    parser = argparse.ArgumentParser(description="Chaos automation helper")
    parser.add_argument("--api", default=DEFAULT_API, help="API base URL (default: %(default)s)")
    parser.add_argument("--endpoint", default="aws", help="Endpoint name to fail/recover")
    parser.add_argument("--duration", type=int, default=30, help="Seconds to keep endpoint down")
    args = parser.parse_args()
    scenario(args.api, args.endpoint, args.duration)


if __name__ == "__main__":
    main()
