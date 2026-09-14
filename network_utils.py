import subprocess
import requests
import socket


def ping_host(host: str) -> str:
    """Pings a host and reports whether it's reachable."""
    try:
        result = subprocess.run(["ping", "-n", "2", host], capture_output=True, text=True, timeout=8)
        if "TTL=" in result.stdout or "ttl=" in result.stdout.lower():
            return f"{host} is reachable."
        return f"{host} did not respond to ping."
    except Exception as e:
        return f"Couldn't ping {host}: {e}"


def get_my_ip() -> str:
    """Gets the public IP address of this machine."""
    try:
        return "Your public IP is " + requests.get("https://api.ipify.org", timeout=6).text
    except Exception as e:
        return f"Couldn't fetch public IP: {e}"


def get_local_ip() -> str:
    """Gets the local network IP address of this machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return f"Your local IP is {s.getsockname()[0]}."
    finally:
        s.close()


def is_website_up(url: str) -> str:
    """Checks if a website is currently reachable."""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        r = requests.get(url, timeout=6)
        return f"{url} is up (status {r.status_code})."
    except requests.RequestException:
        return f"{url} appears to be down or unreachable."


def check_internet_speed() -> str:
    """Runs a quick internet speed test — can take up to 20-30 seconds."""
    try:
        import speedtest
        st = speedtest.Speedtest()
        st.get_best_server()
        down = st.download() / 1_000_000
        up = st.upload() / 1_000_000
        return f"Download: {down:.1f} Mbps, Upload: {up:.1f} Mbps."
    except Exception as e:
        return f"Couldn't run a speed test: {e}"