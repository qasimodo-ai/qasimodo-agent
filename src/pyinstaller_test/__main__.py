import asyncio
import argparse
import os
import sys
import platform
import subprocess
from pathlib import Path

from browser_use import Agent, Browser, ChatGoogle, ChatOpenAI

def find_bundled_chromium():
	"""Find bundled Chromium in PyInstaller bundle"""
	if getattr(sys, 'frozen', False):
		# Running in PyInstaller bundle
		bundle_dir = Path(sys._MEIPASS)
		playwright_dir = bundle_dir / "ms-playwright"
		if playwright_dir.exists():
			chromium_dirs = list(playwright_dir.glob("chromium-*"))
			if chromium_dirs:
				chromium_dir = chromium_dirs[0]

				# Find executable based on platform
				if platform.system() == "Windows":
					chromium_executable = chromium_dir / "chrome-win" / "chrome.exe"
				elif platform.system() == "Darwin":  # macOS
					chromium_executable = chromium_dir / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
				else:  # Linux
					chromium_executable = chromium_dir / "chrome-linux" / "chrome"

				if chromium_executable.exists():
					return str(chromium_executable)
	return None

def ensure_chromium_installed():
	"""Ensure Chromium is installed on macOS (auto-install if needed)"""
	if platform.system() != "Darwin":
		return  # Only needed on macOS

	# Check if Chromium is already installed
	home = Path.home()
	playwright_browsers = home / "Library" / "Caches" / "ms-playwright"

	if playwright_browsers.exists():
		chromium_dirs = list(playwright_browsers.glob("chromium-*"))
		if chromium_dirs:
			print(f"✓ Chromium already installed at {chromium_dirs[0]}")
			return

	# Chromium not found, install it
	print("⚠ Chromium not found on macOS. Installing...")
	print("This is a one-time operation and may take a few minutes.")

	try:
		result = subprocess.run(
			[sys.executable, "-m", "playwright", "install", "chromium"],
			check=True,
			capture_output=True,
			text=True
		)
		print("✓ Chromium installed successfully!")
		if result.stdout:
			print(result.stdout)
	except subprocess.CalledProcessError as e:
		print(f"✗ Failed to install Chromium: {e}")
		if e.stderr:
			print(e.stderr)
		sys.exit(1)
	except FileNotFoundError:
		print("✗ Playwright not found. Please install it manually with: pip install playwright")
		sys.exit(1)

async def main(chromium_path):
	agent = Agent(
		llm=ChatOpenAI(
			model="google/gemini-2.5-flash-preview-09-2025",
			api_key="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",
			base_url='https://openrouter.ai/api/v1',
		),
		task='go to amazon.com and buy pens to draw on the whiteboard',
		browser=Browser(
			headless=True,
			chromium_sandbox=True,
			executable_path=chromium_path
		)
	)
	await agent.run()

def health_check():
	"""Health check to verify bundle integrity"""
	print("=" * 60)
	print("Health Check")
	print("=" * 60)

	# Check Python version
	print(f"✓ Python version: {sys.version}")
	print(f"✓ Platform: {platform.system()} {platform.machine()}")

	# Check if frozen (bundled)
	if getattr(sys, 'frozen', False):
		print(f"✓ Running as PyInstaller bundle")
		print(f"  Bundle dir: {sys._MEIPASS}")
	else:
		print(f"✓ Running from source")

	# Check module imports
	try:
		import browser_use
		print(f"✓ browser_use imported")
	except Exception as e:
		print(f"✗ Failed to import browser_use: {e}")
		return False

	try:
		import playwright
		print(f"✓ playwright imported")
	except Exception as e:
		print(f"✗ Failed to import playwright: {e}")
		return False

	# Check Chromium
	chromium_path = find_bundled_chromium()
	if chromium_path:
		chromium_file = Path(chromium_path)
		if chromium_file.exists():
			print(f"✓ Chromium found: {chromium_path}")
			print(f"  Size: {chromium_file.stat().st_size / (1024*1024):.1f} MB")
		else:
			print(f"✗ Chromium path exists but file not found: {chromium_path}")
			return False
	else:
		print(f"⚠ Chromium not bundled (checking environment)")
		env_chromium = os.environ.get("CHROMIUM_PATH")
		if env_chromium:
			print(f"  CHROMIUM_PATH: {env_chromium}")
		else:
			print(f"  No CHROMIUM_PATH set")

	print("=" * 60)
	print("✓ Health check passed!")
	print("=" * 60)
	return True

def cli():
	parser = argparse.ArgumentParser(description="PyInstaller Test")
	parser.add_argument("--health", action="store_true", help="Run health check and exit")
	args = parser.parse_args()

	if args.health:
		success = health_check()
		sys.exit(0 if success else 1)

	# Ensure Chromium is installed on macOS
	ensure_chromium_installed()

	# Normal execution
	chromium_path = find_bundled_chromium() or os.environ.get("CHROMIUM_PATH")
	print(f"Using {chromium_path} as Chromium")
	asyncio.run(main(chromium_path))

if __name__ == '__main__':
	cli()
