/**
 * Offscreen document for DOM parsing operations that require a document context.
 * Used for HTML parsing, image manipulation, etc. that can't run in the service worker.
 */

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.channel !== 'offscreen') return;

  const { action, data } = msg;

  switch (action) {
    case 'parse_html': {
      const parser = new DOMParser();
      const doc = parser.parseFromString(data.html, 'text/html');
      const tables = Array.from(doc.querySelectorAll('table')).map((table) =>
        Array.from(table.rows).map((row) =>
          Array.from(row.cells).map((cell) => cell.textContent.trim())
        )
      );
      const links = Array.from(doc.querySelectorAll('a[href]')).map((a) => ({
        text: a.textContent.trim(),
        href: a.getAttribute('href'),
      }));
      sendResponse({ tables, links });
      break;
    }

    case 'image_to_blob': {
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement('canvas');
        canvas.width = img.width;
        canvas.height = img.height;
        canvas.getContext('2d').drawImage(img, 0, 0);
        sendResponse({ dataUrl: canvas.toDataURL('image/png') });
      };
      img.src = data.src;
      return true; // async
    }

    default:
      sendResponse({ error: `Unknown offscreen action: ${action}` });
  }
});
