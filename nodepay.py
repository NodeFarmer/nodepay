import asyncio
import signal
import requests
import json
import time
import uuid
from loguru import logger
from python_socks.async_.asyncio import Proxy
from urllib.parse import urlparse
import os
import ssl
import sys
import random

# Determine the directory where the script is located
script_dir = os.path.dirname(os.path.abspath(__file__))

# Function to read a single-line token from a file
def read_single_line_file(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return f.read().strip()
    return None

# Function to read multiple lines from a file
def read_lines_file(file_path):
    with open(file_path, 'r') as f:
        return f.read().splitlines()

# Function to filter out empty lines
def filter_non_empty_lines(lines):
    return [line for line in lines if line.strip()]

# Read configuration values from files
NP_TOKEN = read_single_line_file(os.path.join(script_dir, 'token.txt'))
all_proxies = filter_non_empty_lines(read_lines_file(os.path.join(script_dir, 'proxies.txt')))
proxy_type = read_single_line_file(os.path.join(script_dir, 'proxy-config.txt')) or 'http'

# Constants
HTTPS_URL = "https://nw.nodepay.org/api/network/ping"
RETRY_INTERVAL = 60  # Retry interval for failed proxies in seconds
EXTENSION_VERSION = "2.2.7"
GITHUB_REPO = "NodeFarmer/nodepay"
CURRENT_VERSION = "1.2.2"
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

def format_proxy(proxy_string, proxy_type):
    prefix = ''
    if proxy_type in ['http', 'https']:
        prefix = 'http://'
    elif proxy_type in ['socks', 'socks5']:
        prefix = 'socks5://'
    else:
        raise ValueError("Invalid proxy type")
    
    if '@' in proxy_string:
        # Format: user:pwd@ip:port
        user_pwd, ip_port = proxy_string.split('@')
        user, pwd = user_pwd.split(':')
        ip, port = ip_port.split(':')
        formatted_proxy = f"{prefix}{user}:{pwd}@{ip}:{port}"
    elif proxy_string.count(':') == 3:
        # Format: IP:PORT:USERNAME:PASSWORD
        ip, port, user, pwd = proxy_string.split(':')
        formatted_proxy = f"{prefix}{user}:{pwd}@{ip}:{port}"
    elif proxy_string.count(':') == 1:
        # Format: IP:PORT (No user or password)
        ip, port = proxy_string.split(':')
        formatted_proxy = f"{prefix}{ip}:{port}"
    else:
        raise ValueError("Invalid proxy string format")
    
    return formatted_proxy

async def call_api_info(token, proxy_url):
    logger.info("Getting UserID")
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    
    proxy_dict = {
        'http': proxy_url,
        'https': proxy_url
    }
    
    response = requests.post(
        "https://api.nodepay.org/api/auth/session",
        headers=headers,
        json={},
        proxies=proxy_dict
    )
    response.raise_for_status()
    return response.json()
def uuidv4():
    return '10000000-1000-4000-8000-100000000000'.replace('0', lambda _: f'{os.urandom(1)[0] & 0xf:x}').replace('1', lambda _: f'{(os.urandom(1)[0] & 0xf) | 0x8:x}').replace('4', lambda _: '4')
    
async def send_ping(proxy_url, user_id, token):
    logger.info(proxy_url)
    browser_id = _uuidv4()
    logger.info(browser_id)
    while True:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                'Content-Type': 'application/json'
            }
            headers['Authorization'] = f'Bearer {token}'
            payload = {
                "user_id": user_id,
                "browser_id": browser_id,
                "timestamp": int(time.time()),
                "version": EXTENSION_VERSION
            }
            proxy_dict = {
                'http': proxy_url,
                'https': proxy_url
            }
            response = requests.post(HTTPS_URL, headers=headers, json=payload, proxies=proxy_dict)
            response.raise_for_status()
            logger.debug(response.json())
            await asyncio.sleep(10)  # Wait for a while before the next action
        except Exception as e:
            logger.error(e)
            await asyncio.sleep(RETRY_INTERVAL)

# Main function to run the program
async def main():
    # Check for updates before starting
    if check_for_update():
        logger.info("Restarting script to apply new version...")
        restart_script()
    # Fetch USER_ID from the API using the first valid proxy in the list
    if NP_TOKEN != "":
        user_data = None
        for proxy_string in all_proxies:
            first_proxy = format_proxy(proxy_string, proxy_type)
            try:
                user_data = await call_api_info(NP_TOKEN, first_proxy)
                break  # Exit loop if a valid proxy is found
            except Exception as e:
                logger.error(f"Proxy {first_proxy} is invalid: {e}")
                continue  # Ignore invalid proxy and try the next one
        
        if user_data:
            logger.debug(user_data)
            USER_ID = user_data['data']['uid']
            tasks = [asyncio.ensure_future(send_ping(format_proxy(proxy_string, proxy_type), USER_ID, NP_TOKEN)) for proxy_string in all_proxies]
            await asyncio.gather(*tasks)
        else:
            logger.error("No valid proxy found.")
    else:
        logger.error("You need to specify NP_TOKEN value inside token.txt")

if __name__ == '__main__':
    asyncio.run(main())
