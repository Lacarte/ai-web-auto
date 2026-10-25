import asyncio
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ai_web_auto_backend.automation_controller import BrowserAgent
from ai_web_auto_backend.core.models import Command

async def scan():
    agent = BrowserAgent()
    await agent.connect(timeout=25.0)

    script = """
    (() => {
        const allButtons = Array.from(document.querySelectorAll('button'));
        const downloadButtons = allButtons.filter(b => {
            const label = (b.getAttribute('aria-label') || b.title || b.textContent || '').trim();
            return /download/i.test(label);
        });
        const playButtons = allButtons.filter(b => {
            const label = (b.getAttribute('aria-label') || b.title || b.textContent || '').trim();
            return /play/i.test(label);
        });

        // Map download buttons to their track cards
        const tracks = downloadButtons.map((btn, idx) => {
            let row = btn.parentElement;
            // Traverse up until we have a distinct card/row container
            for (let i = 0; i < 8 && row && row !== document.body; i++) {
                if (row.querySelectorAll('button').length >= 3) break;
                row = row.parentElement;
            }
            const texts = Array.from(row ? row.querySelectorAll('h1,h2,h3,h4,h5,p,span') : [])
                .map(t => t.textContent.trim())
                .filter(t => t.length > 2 && t.length < 100);
            const title = texts.length > 0 ? texts[0] : ('Track_' + (idx + 1));
            return {
                index: idx,
                title: title,
                buttonTexts: Array.from(row.querySelectorAll('button')).map(b => (b.getAttribute('aria-label') || b.textContent || '').trim()).filter(Boolean),
                textPreview: row ? row.textContent.trim().replace(/\\s+/g, ' ').slice(0, 100) : ''
            };
        });

        return {
            url: location.href,
            title: document.title,
            downloadButtonCount: downloadButtons.length,
            playButtonCount: playButtons.length,
            tracks: tracks.slice(0, 10)
        };
    })()
    """
    r = await agent.server.send_command(Command(action='execute_script', params={'script': script}))
    print("ELEVENLABS TRACK SCAN RESULTS:")
    print(json.dumps(r.data.extracted_data.get('result', {}), indent=2))
    await agent.stop()

if __name__ == '__main__':
    asyncio.run(scan())

