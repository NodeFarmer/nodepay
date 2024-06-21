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

# Read NP_TOKEN from the configuration file
with open('config.txt', 'r') as f:
    NP_TOKEN = f.read().strip()

with open('proxy.txt', 'r') as f:
    all_proxies = f.read().splitlines()

WEBSOCKET_URL = "wss://nw.nodepay.ai:4576/websocket"
RETRY_INTERVAL = 60000  # in milliseconds
PING_INTERVAL = 10000  # in milliseconds, increased to reduce bandwidth usage

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
    with open("proxy.txt", "r+") as file:
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

async def connect_socket_proxy(proxy, token, reconnect_interval=RETRY_INTERVAL, ping_interval=PING_INTERVAL):
    if not is_valid_proxy(proxy):
        logger.error(f"Invalid proxy URL: {proxy}")
        remove_proxy_from_list(proxy)
        return None

    browser_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, proxy))
    logger.info(f"Browser ID: {browser_id}")

    # Prepend the scheme to the proxy string
    proxy_url = f"http://{proxy}"

    try:
        proxy_instance = Proxy.from_url(proxy_url)
        async with proxy_connect(WEBSOCKET_URL, proxy=proxy_instance) as websocket:
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
                await websocket.send(json.dumps(payload))

            async for message in websocket:
                data = json.loads(message)

                if data["action"] == "PONG":
                    await send_pong(data["id"])
                    await asyncio.sleep(ping_interval / 1000)  # Wait before sending ping
                    await send_ping(data["id"])

                elif data["action"] == "AUTH":
                    api_response = await call_api_info_async(token)
                    if api_response["code"] == 0 and api_response["data"]["uid"]:
                        user_info = api_response["data"]
                        auth_info = {
                            "user_id": user_info["uid"],
                            "browser_id": browser_id,
                            "user_agent": "Mozilla/5.0",
                            "timestamp": int(time.time()),
                            "device_type": "extension",
                            "version": "extension_version",
                            "token": token,
                            "origin_action": "AUTH",
                        }
                        await send_ping(data["id"], auth_info)
                    else:
                        logger.error("Failed to authenticate")

    except Exception as e:
        error_message = str(e)
        if any(phrase in error_message for phrase in [
            "sent 1011 (internal error) keepalive ping timeout; no close frame received",
            "500 Internal Server Error"
        ]):
            logger.info(f"Removing error proxy from the list: {proxy}")
            remove_proxy_from_list(proxy)
            return None
        else:
            logger.error(f"Connection error: {e}")
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

async def main():
    active_proxies = [proxy for proxy in all_proxies[:50] if is_valid_proxy(proxy)]
    tasks = {asyncio.create_task(connect_socket_proxy(proxy, NP_TOKEN)): proxy for proxy in active_proxies}

    while True:
        done, pending = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            failed_proxy = tasks[task]
            if task.result() is None:
                logger.info(f"Removing and replacing failed proxy: {failed_proxy}")
                active_proxies.remove(failed_proxy)
                if all_proxies:
                    new_proxy = all_proxies.pop(0)
                    if is_valid_proxy(new_proxy):
                        active_proxies.append(new_proxy)
                        new_task = asyncio.create_task(connect_socket_proxy(new_proxy, NP_TOKEN))
                        tasks[new_task] = new_proxy
            tasks.pop(task)

        for proxy in set(active_proxies) - set(tasks.values()):
            new_task = asyncio.create_task(connect_socket_proxy(proxy, NP_TOKEN))
            tasks[new_task] = proxy

        await asyncio.sleep(3)  # Prevent tight loop in case of rapid failures

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Program terminated by user.")
