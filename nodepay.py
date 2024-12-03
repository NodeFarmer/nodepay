import asyncio
import signal
import cloudscraper
import json
import time
import uuid
from loguru import logger
import os
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
PING_URL = "https://nw2.nodepay.org/api/network/ping"
AUTH_URL = "http://api.nodepay.ai/api/auth/session"
RETRY_INTERVAL = 60  # Retry interval for failed proxies in seconds
EXTENSION_VERSION = "2.2.7"
GITHUB_REPO = "NodeFarmer/nodepay"
CURRENT_VERSION = "1.4.0"
NODEPY_FILENAME = "nodepay.py"

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
    scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://app.nodepay.ai/",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "chrome-extension://lgmpfmgeabnnlemejacfljbmonaomfmm",
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    
    proxies = {
        'http': proxy_url,
        'https': proxy_url
    }
    
    response = scraper.post(
        AUTH_URL,
        headers=headers,
        json={},
        proxies=proxies
    )
    response.raise_for_status()
    return response.json()

def uuidv4():
    template = "10000000-1000-4000-8000-100000000000"
    return ''.join([
        hex(random.randint(0, 15) ^ (random.getrandbits(8) & (15 >> (int(c) // 4))))[2:]
        if c in '018' else c
        for c in template
    ])

async def send_ping(proxy_url, user_id, token):
    logger.info(proxy_url)
    browser_id = uuidv4()
    logger.info(browser_id)
    scraper = cloudscraper.create_scraper()
    while True:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://app.nodepay.ai/",
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": "chrome-extension://lgmpfmgeabnnlemejacfljbmonaomfmm",
            }
            headers['Authorization'] = f'Bearer {token}'
            payload = {
                "user_id": user_id,
                "browser_id": browser_id,
                "timestamp": int(time.time()),
                "version": EXTENSION_VERSION
            }
            proxies = {
                'http': proxy_url,
                'https': proxy_url
            }
            response = scraper.post(PING_URL, headers=headers, json=payload, proxies=proxies)
            response.raise_for_status()
            logger.debug(response.json())
            await asyncio.sleep(10)  # Wait for a while before the next action
        except Exception as e:
            logger.error(e)
            await asyncio.sleep(RETRY_INTERVAL)

# Main function to run the program
async def main():
    max_attempts = 10
    attempt = 0
    user_data = None

    while attempt < max_attempts and user_data is None:
        attempt += 1
        logger.info(f"Authentication attempt {attempt} of {max_attempts}")
        if NP_TOKEN != "":
            for proxy_string in all_proxies:
                first_proxy = format_proxy(proxy_string, proxy_type)
                try:
                    user_data = await call_api_info(NP_TOKEN, first_proxy)
                    break  # Exit loop if a valid proxy is found
                except Exception as e:
                    logger.error(f"Proxy {first_proxy} is invalid: {e}")
                    continue
        else:
            logger.error("You need to specify NP_TOKEN value inside token.txt")
            break

        if user_data is None:
            logger.warning("Authentication failed. Retrying...")
            await asyncio.sleep(1)

    if user_data:
        logger.debug(user_data)
        USER_ID = user_data['data']['uid']
        tasks = [asyncio.ensure_future(send_ping(format_proxy(proxy_string, proxy_type), USER_ID, NP_TOKEN)) for proxy_string in all_proxies]
        await asyncio.gather(*tasks)
    else:
        logger.error("Authentication failed after 10 attempts. Exiting.")

if __name__ == '__main__':
    asyncio.run(main())
