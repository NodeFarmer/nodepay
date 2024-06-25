import asyncio
import signal
import requests
import json
import time
import uuid
import websockets
from loguru import logger
from websockets_proxy import Proxy, proxy_connect
from urllib.parse import urlparse
import os
import ssl
import subprocess
import sys

# Determine the directory where the script is located
script_dir = os.path.dirname(os.path.abspath(__file__))

# Read NP_TOKEN from the configuration file
config_path = os.path.join(script_dir, 'config.txt')
with open(config_path, 'r') as f:
    NP_TOKEN = f.read().strip()

# Read proxies from the proxy file
proxy_path = os.path.join(script_dir, 'proxy.txt')
with open(proxy_path, 'r') as f:
    all_proxies = f.read().splitlines()

# Read user agents from the user agent file
useragents_path = os.path.join(script_dir, 'useragents.txt')
with open(useragents_path, 'r') as f:
    user_agents = f.read().splitlines()

WEBSOCKET_URL = "wss://nw.nodepay.ai:4576/websocket"
RETRY_INTERVAL = 60  # in seconds, retry interval for failed proxies
PING_INTERVAL = 10  # in seconds, increased to reduce bandwidth usage
EXTENSION_VERSION = "2.1.9"

# Create SSL context allowing all TLS versions up to TLS 1.3
ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS)
ssl_context.minimum_version = ssl.TLSVersion.TLSv1
ssl_context.maximum_version = ssl.TLSVersion.TLSv1_3
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

GITHUB_REPO = "NodeFarmer/nodepay"
CURRENT_VERSION = "1.0.1"
NODEPY_FILENAME = "node.py"

def call_api_info(token):
    headers = {
        'Content-Type': 'application/json'
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'

    response = requests.post(
        "https://api.nodepay.ai/api/auth/session",
        headers=headers,
        json={}
    )
    response.raise_for_status()  # Raise an exception for HTTP errors
    return response.json()

# Fetch USER_ID from the API
user_data = call_api_info(NP_TOKEN)
USER_ID = user_data['data']['uid']

def remove_proxy_from_list(proxy):
    with open(proxy_path, "r+") as file:
        lines = file.readlines()
        file.seek(0)
        for line in lines:
            if line.strip() != proxy:
                file.write(line)
        file.truncate()

def is_valid_proxy(proxy):
    try:
        # Split the proxy string into its components
        parts = proxy.split(':')
        
        # Check if there are exactly 4 parts: ip, port, username, password
        if len(parts) != 4:
            return False
        
        ip, port, username, password = parts

        # Validate IP address format
        ip_parts = ip.split('.')
        if len(ip_parts) != 4 or not all(0 <= int(part) < 256 for part in ip_parts):
            return False

        # Validate port is a number between 1 and 65535
        if not port.isdigit() or not (1 <= int(port) <= 65535):
            return False

        # Validate username and password are not empty
        if not username or not password:
            return False

        return True

    except (ValueError, TypeError):
        return False

async def call_api_info_async(token):
    return {
        "code": 0,
        "data": {
            "uid": USER_ID,
        }
    }

async def connect_socket_proxy(proxy, user_agent, token, reconnect_interval=RETRY_INTERVAL, ping_interval=PING_INTERVAL):
    if not is_valid_proxy(proxy):
        logger.error(f"Invalid proxy URL: {proxy}")
        remove_proxy_from_list(proxy)
        return None

    browser_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, proxy))
    logger.info(f"Browser ID: {browser_id}")

    ip, port, username, password = proxy.split(':')
    proxy_url = f"http://{username}:{password}@{ip}:{port}"

    try:
        proxy_instance = Proxy.from_url(proxy_url)
        
        # Log request details
        logger.info(f"Connecting to WebSocket with proxy: {proxy_url}")
        
        async with proxy_connect(WEBSOCKET_URL, ssl=ssl_context, proxy=proxy_instance) as websocket:
            logger.info("Connected to WebSocket")

            async def send_ping(guid, options={}):
                payload = {
                    "id": guid,
                    "action": "PING",
                    **options,
                }
                await websocket.send(json.dumps(payload))

            async def send_pong(guid):
                payload = {
                    "id": guid,
                    "origin_action": "PONG",
                }
                logger.info(payload)
                await websocket.send(json.dumps(payload))

            async for message in websocket:
                logger.info(message)
                data = json.loads(message)

                if data["action"] == "PONG":
                    await send_pong(data["id"])
                    await asyncio.sleep(ping_interval)  # Wait before sending ping
                    await send_ping(data["id"])

                elif data["action"] == "AUTH":
                    api_response = await call_api_info_async(token)
                    if api_response["code"] == 0 and api_response["data"]["uid"]:
                        user_info = api_response["data"]
                        auth_info = {
                            "user_id": user_info["uid"],
                            "browser_id": browser_id,
                            "user_agent": user_agent,
                            "timestamp": int(time.time()),
                            "device_type": "extension",
                            "version": EXTENSION_VERSION,
                            "token": token,
                            "origin_action": "AUTH",
                        }
                        await send_ping(data["id"], auth_info)
                    else:
                        logger.error("Failed to authenticate")

    except Exception as e:
        # Log the detailed exception information
        logger.error(f"Connection error: {str(e)}")
        if hasattr(e, 'response') and e.response:
            logger.error(f"Response status: {e.response.status}")
            logger.error(f"Response body: {e.response.text}")
        if any(phrase in str(e) for phrase in [
            "sent 1011 (internal error) keepalive ping timeout; no close frame received",
            "500 Internal Server Error",
        ]):
            return None
        else:
            await asyncio.sleep(reconnect_interval)
            return proxy

async def shutdown(loop, signal=None):
    if signal:
        logger.info(f"Received exit signal {signal.name}...")

    logger.info("Napping for 3 seconds before shutdown...")
    await asyncio.sleep(3)
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    logger.info(f"Cancelling {len(tasks)} outstanding tasks")
    [task.cancel() for task in tasks]

    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("All tasks cancelled, stopping loop")
    loop.stop()

def download_latest_version():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{NODEPY_FILENAME}"
    response = requests.get(url)
    response.raise_for_status()
    with open(os.path.join(script_dir, NODEPY_FILENAME), 'wb') as f:
        f.write(response.content)

def check_for_update():
    try:
        download_latest_version()
        logger.info("Downloaded latest version of node.py")
        return True
    except Exception as e:
        logger.error(f"Error checking for update: {e}")
        return False

def restart_script():
    python = sys.executable
    os.execl(python, python, *sys.argv)

async def main():
    # Check for updates before starting
    if check_for_update():
        logger.info("Restarting script to apply new version...")
        restart_script()

    retry_times = {}
    active_proxies = [(proxy, user_agents[idx]) for idx, proxy in enumerate(all_proxies[:50]) if is_valid_proxy(proxy)]
    
    while True:
        if not active_proxies:
            logger.error("No valid proxies available.")
            await asyncio.sleep(RETRY_INTERVAL)
            active_proxies = [(proxy, user_agents[idx]) for idx, proxy in enumerate(all_proxies[:50]) if is_valid_proxy(proxy)]
            continue
        
        tasks = {asyncio.create_task(connect_socket_proxy(proxy, user_agent, NP_TOKEN)): proxy for proxy, user_agent in active_proxies}

        done, pending = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            failed_proxy = tasks[task]
            if task.result() is None:
                logger.info(f"Removing and replacing failed proxy: {failed_proxy}")
                retry_times[failed_proxy] = time.time() + RETRY_INTERVAL
                active_proxies = [(proxy, ua) for proxy, ua in active_proxies if proxy != failed_proxy]
            tasks.pop(task)

        current_time = time.time()
        for proxy, user_agent in set(active_proxies) - set(tasks.values()):
            if proxy not in retry_times:
                retry_times[proxy] = 0

            if current_time >= retry_times[proxy]:
                logger.info(f"Retrying proxy: {proxy} at {current_time}, scheduled retry at {retry_times[proxy]}")
                new_task = asyncio.create_task(connect_socket_proxy(proxy, user_agent, NP_TOKEN))
                tasks[new_task] = proxy
                retry_times[proxy] = current_time + RETRY_INTERVAL

        await asyncio.sleep(3)  # Prevent tight loop in case of rapid failures

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(loop, signal=s)))
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
        logger.info("Program terminated.")
