# Projects

Reference documentation for completed automation projects built with the AI Web-Auto platform.

Each file documents what was built, the website automated, key technical challenges solved, proven selectors, and reusable patterns — so future projects on similar sites can reuse what already works instead of starting from scratch.

## Projects

| Project | Website | What it does |
|---------|---------|--------------|
| [flow-extension](flow-extension.md) | Google Labs Flow (ImageFX) | Image/video generation with configurable settings and auto-download |
| [meta-ai-extension](meta-ai-extension.md) | Meta AI | Image generation with ratio selection and CDN URL download |
| [chatgpt-ui-to-api](chatgpt-ui-to-api.md) | ChatGPT | REST API bridge — send prompts via HTTP, get responses as JSON |
| [skool-down-course](skool-down-course.md) | Skool.com | Bulk course downloader — videos, HTML, screenshots, PDFs |
| [skool-level-up](skool-level-up.md) | Skool.com | Community engagement automation to unlock Level 2 |

## How to use these

Before building a new automation, check if a similar project exists here. Look for:
- Same website or framework (React, Radix UI, ProseMirror, etc.)
- Similar interaction patterns (contentEditable typing, dropdown selection, file download)
- Proven selectors that might still work or need minor updates
- Technical workarounds for known issues (CSP, hidden buttons, React state)
