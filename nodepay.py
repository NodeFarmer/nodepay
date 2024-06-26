import asyncio
import signal
import requests
import json
import time
import uuid
from loguru import logger
from python_socks.async_.asyncio import Proxy
from websockets import connect as websocket_connect
from urllib.parse import urlparse
import os
import ssl
import sys
import random

# Determine the directory where the script is located
script_dir = os.path.dirname(os.path.abspath(__file__))

# Function to read a single-line token from a file
def read_single_line_file(file_path):
    with open(file_path, 'r') as f:
        return f.read().strip()

# Function to read multiple lines from a file
def read_lines_file(file_path):
    with open(file_path, 'r') as f:
        return f.read().splitlines()

# Read configuration values from files
NP_TOKEN = read_single_line_file(os.path.join(script_dir, 'token.txt'))
all_proxies = read_lines_file(os.path.join(script_dir, 'proxies.txt'))

# Constants
WEBSOCKET_URL = "wss://nw.nodepay.ai:4576/websocket"
SERVER_HOSTNAME = "nw.nodepay.ai"
RETRY_INTERVAL = 60  # Retry interval for failed proxies in seconds
PING_INTERVAL = 10  # Increased to reduce bandwidth usage
EXTENSION_VERSION = "2.1.9"
GITHUB_REPO = "NodeFarmer/nodepay"
CURRENT_VERSION = "1.0.2"
NODEPY_FILENAME = "nodepay.py"



# Function to download the latest version of the script
def download_latest_version():
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{NODEPY_FILENAME}"
    response = requests.get(url)
    response.raise_for_status()
    with open(os.path.join(script_dir, NODEPY_FILENAME), 'wb') as f:
        f.write(response.content)

# Function to check for updates and download if available
def check_for_update():
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        response = requests.get(url)
        response.raise_for_status()
        latest_release = response.json()
        latest_version = latest_release["tag_name"]

        if latest_version != CURRENT_VERSION:
            logger.info(f"New version available: {latest_version}. Current version: {CURRENT_VERSION}")
            download_latest_version()
            logger.info("Downloaded latest version of nodepay.py")
            return True
        else:
            logger.info("No new version available.")
            return False
    except Exception as e:
        logger.error(f"Error checking for update: {e}")
        return False

# Function to restart the script
def restart_script():
    python = sys.executable
    os.execl(python, python, *sys.argv)

def format_proxy(proxy_string):
    # Check and handle different formats
    if '@' in proxy_string:
        # Format: user:pwd@ip:port
        user_pwd, ip_port = proxy_string.split('@')
        user, pwd = user_pwd.split(':')
        ip, port = ip_port.split(':')
        formatted_proxy = f"http://{user}:{pwd}@{ip}:{port}"
    elif proxy_string.count(':') == 3:
        # Format: IP:PORT:USERNAME:PASSWORD
        ip, port, user, pwd = proxy_string.split(':')
        formatted_proxy = f"http://{user}:{pwd}@{ip}:{port}"
    elif proxy_string.count(':') == 1:
        # Format: IP:PORT (No user or password)
        ip, port = proxy_string.split(':')
        formatted_proxy = f"http://{ip}:{port}"
    else:
        raise ValueError("Invalid proxy string format")
    
    return formatted_proxy

async def call_api_info(token):
    logger.info("Getting UserID")
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    
    response = requests.post(
        "https://api.nodepay.ai/api/auth/session",
        headers=headers,
        json={}
    )
    response.raise_for_status()
    return response.json()

async def connect_to_wss(proxy_url, user_id, token):
    logger.info(proxy_url)
    browser_id = str(uuid.uuid3(uuid.NAMESPACE_DNS, proxy_url))
    logger.info(browser_id)
    while True:
        try:
            await asyncio.sleep(random.randint(1, 10) / 10)
            custom_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            }
            ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            proxy = Proxy.from_url(proxy_url)
            conn = await proxy.connect(dest_host=SERVER_HOSTNAME, dest_port=443)
            websocket = await websocket_connect(WEBSOCKET_URL, ssl=ssl_context, extra_headers=custom_headers, sock=conn)

            async def send_ping(guid, options={}):
                    send_message = json.dumps(
                        {"id": guid, "action": "PING", **options})
                    logger.debug([proxy_url,send_message])
                    try:
                        await websocket.send(send_message)
                    except Exception as e:
                        logger.error(e)
                        logger.error(proxy_url)
                    

            #await asyncio.sleep(1)
            #asyncio.create_task(send_ping(str(uuid.uuid4())))

            while True:
                response = await websocket.recv()
                message = json.loads(response)
                if message.get("action") == "AUTH":
                    auth_response = {
                            "user_id": user_id,
                            "browser_id": browser_id,
                            "user_agent": custom_headers['User-Agent'],
                            "timestamp": int(time.time()),
                            "device_type": "extension",
                            "version": EXTENSION_VERSION,
                            "token": token,
                            "origin_action": "AUTH"
                    }
                    await send_ping(message["id"], auth_response)

                elif message.get("action") == "PONG":
                    pong_response = {"id": message["id"], "origin_action": "PONG"}
                    logger.debug([proxy_url,pong_response])
                    await websocket.send(json.dumps(pong_response))
                    await asyncio.sleep(PING_INTERVAL)
                    await send_ping(message["id"])
        except Exception as e:
            if 'SSL: WRONG_VERSION_NUMBER' in str(e) or str(e) == "" :
                logger.info([proxy_url,"Server seems busy we keep trying to connect"])
            else:
                logger.error(e)
                logger.error(proxy_url)

# Main function to run the program
async def main():
    # Check for updates before starting
    if check_for_update():
        logger.info("Restarting script to apply new version...")
        restart_script()
    # Fetch USER_ID from the API
    if NP_TOKEN != "":
        user_data = await call_api_info(NP_TOKEN)
        logger.debug(user_data)
        if user_data:
            USER_ID = user_data['data']['uid']
            tasks = [asyncio.ensure_future(connect_to_wss(format_proxy(proxy_string), USER_ID, NP_TOKEN)) for proxy_string in all_proxies]
            await asyncio.gather(*tasks)
    else:
        logger.error("You need to specify NP_TOKEN value inside token.txt")

if __name__ == '__main__':
    asyncio.run(main())
