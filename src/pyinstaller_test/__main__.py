import asyncio
import os
import sys
import platform
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

def cli():
	# Try bundled chromium first, then CHROMIUM_PATH
	chromium_path = find_bundled_chromium() or os.environ.get("CHROMIUM_PATH")
	print(f"Using {chromium_path} as Chromium")
	asyncio.run(main(chromium_path))

if __name__ == '__main__':
	cli()
