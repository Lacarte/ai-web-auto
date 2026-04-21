const BACKEND = 'http://localhost:9876';
const $ = (id) => document.getElementById(id);

let scanData = null;
let running = false;
let paused = false;

// ============================================================
// STATE PERSISTENCE via chrome.storage.local
// ============================================================

async function saveState() {
  await chrome.storage.local.set({
    sdcScanData: scanData,
    sdcRunning: running,
    sdcPaused: paused
  });
}

async function loadState() {
  const data = await chrome.storage.local.get(['sdcScanData', 'sdcRunning', 'sdcPaused']);
  scanData = data.sdcScanData || null;
  running = data.sdcRunning || false;
  paused = data.sdcPaused || false;
}

// ============================================================
// UI HELPERS
// ============================================================

async function checkBackend() {
  const dot = $('backendDot');
  const label = $('backendLabel');
  try {
    const r = await fetch(`${BACKEND}/health`, { signal: AbortSignal.timeout(3000) });
    if (r.ok) {
      dot.className = 'dot connected';
      label.textContent = 'Python backend connected';
      return true;
    }
  } catch {}
  dot.className = 'dot error';
  label.textContent = 'Backend offline - run runner.bat';
  return false;
}

function setStatus(text) {
  $('status').innerHTML = `<div class="status-line">${text}</div>`;
}

function setProgress(pct) {
  $('progressBar').style.display = 'block';
  $('progressFill').style.width = `${pct}%`;
}

function fmtGB(bytes) {
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
}

function updateDiskWarning(data) {
  const el = $('diskWarning');
  if (!data || !data.diskPaused) { el.style.display = 'none'; return; }
  const free = fmtGB(data.diskFreeBytes || 0);
  const need = fmtGB(data.diskMinBytes || 0);
  const path = data.diskPath ? ` (${data.diskPath})` : '';
  el.style.display = 'block';
  el.innerHTML =
    '<strong>Disk full &mdash; download paused</strong>' +
    `Only ${free} free on the output disk${path}. Need at least ${need}. ` +
    'Free up space and the download will resume automatically.';
}

function showModuleList(modules) {
  const list = $('courseList');
  list.style.display = 'block';
  // If single module with lessons (module page scan), show lessons instead
  if (modules.length === 1 && modules[0].lessons?.length) {
    const mod = modules[0];
    list.innerHTML = `<div class="item" style="font-weight:bold;opacity:0.7">${mod.title}</div>` +
      mod.lessons.map((l, i) =>
        `<div class="item" data-idx="${i}">${i + 1}. ${l.title}</div>`
      ).join('');
  } else {
    list.innerHTML = modules.map((m, i) =>
      `<div class="item" data-idx="${i}">${i + 1}. ${m.title}</div>`
    ).join('');
  }
}

function populateStartFrom(modules) {
  const select = $('startFrom');
  // If single module with lessons, populate with lesson names
  if (modules.length === 1 && modules[0].lessons?.length) {
    select.innerHTML = modules[0].lessons.map((l, i) =>
      `<option value="${i + 1}">${i + 1}. ${l.title}</option>`
    ).join('');
  } else {
    select.innerHTML = modules.map((m, i) =>
      `<option value="${i + 1}">${i + 1}. ${m.title}</option>`
    ).join('');
  }
}

function showDownloadingUI() {
  $('btnStart').style.display = 'none';
  $('btnScan').style.display = 'none';
  $('runningControls').style.display = 'flex';
  $('pausedControls').style.display = 'none';
  $('progressBar').style.display = 'block';
}

function showPausedUI() {
  $('btnStart').style.display = 'none';
  $('btnScan').style.display = 'none';
  $('runningControls').style.display = 'none';
  $('pausedControls').style.display = 'flex';
  $('progressBar').style.display = 'block';
}

function showScanResultUI() {
  $('btnStart').style.display = 'block';
  $('btnScan').style.display = 'block';
  $('btnScan').disabled = false;
  $('runningControls').style.display = 'none';
  $('pausedControls').style.display = 'none';
}

function showIdleUI() {
  $('btnStart').style.display = 'none';
  $('btnScan').style.display = 'block';
  $('btnScan').disabled = false;
  $('runningControls').style.display = 'none';
  $('pausedControls').style.display = 'none';
  $('progressBar').style.display = 'none';
}

// ============================================================
// INIT - restore state on popup open
// ============================================================

async function init() {
  const backendOk = await checkBackend();

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.url?.includes('skool.com/') && tab.url.includes('/classroom')) {
    $('courseUrl').value = tab.url;
  }

  // Restore previous state
  await loadState();

  if (scanData?.modules?.length) {
    showModuleList(scanData.modules);
    populateStartFrom(scanData.modules);
    const skipped = scanData.skippedModules?.length ? ` (${scanData.skippedModules.length} locked skipped)` : '';
    setStatus(`Found ${scanData.modules.length} modules for "${scanData.courseSiteName}"${skipped}.`);
    showScanResultUI();
  }

  // If a download was running or paused, restore UI
  if (running && paused && backendOk) {
    showPausedUI();
    if (scanData?.modules) showModuleList(scanData.modules);
    setStatus('Paused - click Resume to continue.');
  } else if (running && backendOk) {
    showDownloadingUI();
    if (scanData?.modules) showModuleList(scanData.modules);
    pollProgress();
  } else if (running) {
    // Backend offline but was running - reset
    running = false;
    paused = false;
    await saveState();
  }
}

// ============================================================
// SCAN
// ============================================================

$('btnScan').addEventListener('click', async () => {
  const ok = await checkBackend();
  if (!ok) { setStatus('Backend not running. Start runner.bat first.'); return; }

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url?.includes('skool.com')) {
    setStatus('Navigate to a Skool classroom page first.');
    return;
  }

  setStatus('Refreshing page...');
  $('btnScan').disabled = true;

  // Reload the page to get fresh __NEXT_DATA__ (client-side nav makes it stale)
  await chrome.tabs.reload(tab.id);
  await new Promise(resolve => {
    const onUpdate = (id, info) => {
      if (id === tab.id && info.status === 'complete') {
        chrome.tabs.onUpdated.removeListener(onUpdate);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(onUpdate);
    setTimeout(() => { chrome.tabs.onUpdated.removeListener(onUpdate); resolve(); }, 15000);
  });
  await new Promise(r => setTimeout(r, 2000));

  setStatus('Scanning...');

  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: 'MAIN',
      func: () => {
        try {
          // Page was just reloaded, so script tag has fresh SSR data
          const el = document.getElementById('__NEXT_DATA__');
          if (!el) return { success: false, error: 'No __NEXT_DATA__ found on this page' };
          const nd = JSON.parse(el.textContent);
          const pp = nd.props?.pageProps;
          if (!pp) return { success: false, error: 'No pageProps in __NEXT_DATA__' };

          const pathParts = window.location.pathname.split('/').filter(Boolean);
          const isModulePage = pathParts.length >= 3 && pathParts[1] === 'classroom' && pathParts[2];
          const groupName = pp.currentGroup?.name || window.location.pathname.split('/')[1] || '';
          const groupId = pp.currentGroup?.id || '';

          const allModules = [];
          const skippedModules = [];

          if (isModulePage && pp.course?.children) {
            // On a module page — extract lessons from course.children
            const children = pp.course.children;
            const courseSlug = pp.course?.name || pathParts[2];
            // Title: try metadata.title, then document title (before " · "), then slug
            const courseTitle = pp.course?.metadata?.title
              || document.title.split(' · ')[0].split(' - ')[0]
              || courseSlug;
            allModules.push({
              id: pp.course?.id || courseSlug,
              slug: courseSlug,
              title: courseTitle,
              url: window.location.href.split('?')[0],
              lessons: children.map(c => {
                const m = c.course?.metadata || {};
                const title = m.title || c.course?.name || '?';
                return { id: c.course?.id, title };
              }).filter(l => l.title !== '?')
            });
          } else {
            // On classroom root — use allCourses
            const allCourses = pp.allCourses || [];
            if (!allCourses.length) return { success: false, error: 'No courses found. Navigate to a Skool classroom page (/classroom).' };

            for (const c of allCourses) {
              const meta = c.metadata || {};
              if (!meta.hasAccess && meta.minAccessLevel != null) {
                skippedModules.push(meta.title || c.name);
                continue;
              }
              allModules.push({
                id: c.id, slug: c.name,
                title: meta.title || c.name,
                url: `https://www.skool.com/${groupName}/classroom/${c.name}`
              });
            }
          }

          if (!allModules.length) return { success: false, error: 'No accessible modules found on this page.' };

          const courseSiteName = pp.currentGroup?.metadata?.name
            || document.title.replace(' \u00b7 ', ' - ').split(' - ')[0] || 'SkoolCourse';

          return {
            success: true, courseSiteName, groupName, groupId,
            modules: allModules, skippedModules,
            courses: [], totalModules: allModules.length
          };
        } catch (err) {
          return { success: false, error: err.message };
        }
      }
    });

    if (!result?.success) {
      setStatus(`Scan failed: ${result?.error || 'No response'}`);
      $('btnScan').disabled = false;
      return;
    }

    scanData = result;
    await saveState();

    // Inject content script for download phase
    try { await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] }); } catch {}

    const skipped = result.skippedModules?.length ? ` (${result.skippedModules.length} locked skipped)` : '';
    setStatus(`Found ${result.modules.length} modules for "${result.courseSiteName}"${skipped}.`);

    showModuleList(result.modules);
    populateStartFrom(result.modules);
    showScanResultUI();
    $('btnScan').disabled = false;
  } catch (err) {
    setStatus(`Scan error: ${err.message}`);
    $('btnScan').disabled = false;
  }
});

// ============================================================
// START DOWNLOAD
// ============================================================

$('btnStart').addEventListener('click', async () => {
  if (!scanData) return;
  const ok = await checkBackend();
  if (!ok) { setStatus('Backend not running.'); return; }

  running = true;
  await saveState();
  showDownloadingUI();

  const startIdx = parseInt($('startFrom').value) || 1;
  const startModule = scanData.modules[startIdx - 1];
  setStatus(`Starting from: ${startModule?.title || `#${startIdx}`}`);

  const opts = {
    html: $('optHtml').checked,
    video: $('optVideo').checked,
    screenshot: $('optScreenshot').checked,
    markComplete: $('optMark').checked,
    community: $('optCommunity').checked,
    startFrom: startIdx
  };

  // Init backend session
  try {
    const payload = {
      courseName: scanData.courseSiteName || 'SkoolCourse',
      modules: scanData.modules.map(m => ({ id: m.id, slug: m.slug, title: m.title, url: m.url })),
      courses: [],
      options: opts
    };
    const res = await fetch(`${BACKEND}/init-session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const text = await res.text();
    let initData;
    try { initData = JSON.parse(text); } catch {
      setStatus(`Backend returned invalid response: ${text.substring(0, 80)}`);
      running = false; await saveState();
      return;
    }
    if (!initData.success) {
      setStatus(`Backend init failed: ${initData.error}`);
      running = false; await saveState();
      return;
    }
  } catch (err) {
    setStatus(`Backend error: ${err.message}`);
    running = false; await saveState();
    return;
  }

  // Tell SERVICE WORKER to orchestrate the download
  try {
    const swResponse = await chrome.runtime.sendMessage({
      action: 'startDownload',
      scanData: scanData,
      options: opts
    });
    console.log('[SDC popup] SW response:', swResponse);
    if (!swResponse || !swResponse.success) {
      setStatus('Service worker did not respond. Reload extension.');
      running = false;
      await saveState();
      return;
    }
    setStatus('Download started - processing modules...');
  } catch (err) {
    console.error('[SDC popup] sendMessage error:', err);
    setStatus('Failed to start: ' + err.message + '. Reload extension.');
    running = false;
    await saveState();
    return;
  }

  pollProgress();
});

// ============================================================
// PAUSE / RESUME / STOP
// ============================================================

$('btnPause').addEventListener('click', async () => {
  paused = true;
  await saveState();
  chrome.runtime.sendMessage({ action: 'pauseDownload' });
  setStatus('Pausing after current lesson...');
  showPausedUI();
});

$('btnResume').addEventListener('click', async () => {
  paused = false;
  await saveState();
  chrome.runtime.sendMessage({ action: 'resumeDownload' });
  setStatus('Resuming download...');
  showDownloadingUI();
  pollProgress();
});

async function handleStop() {
  running = false;
  paused = false;
  await saveState();
  chrome.runtime.sendMessage({ action: 'stopDownload' });
  setStatus('Stopping after current lesson...');
  showIdleUI();
  if (scanData?.modules?.length) {
    showScanResultUI();
  }
}

$('btnStop').addEventListener('click', handleStop);
$('btnStopPaused').addEventListener('click', handleStop);

// ============================================================
// PROGRESS POLLING
// ============================================================

async function pollProgress() {
  // Wait before first poll so buttons are visible and backend has time to start
  await new Promise(r => setTimeout(r, 3000));
  while (running && !paused) {
    try {
      const r = await fetch(`${BACKEND}/progress`);
      const data = await r.json();
      const total = data.total || 0;

      updateDiskWarning(data);

      if (total > 0 && data.completed >= total) {
        // Check if community download is still running
        if (data.currentCourse?.startsWith('Community:') || data.currentCourse?.includes('community')) {
          setStatus(`${data.completed} lessons done. ${data.currentCourse}`);
          await new Promise(r => setTimeout(r, 2000));
          continue;
        }
        running = false;
        paused = false;
        await saveState();
        setProgress(100);
        setStatus(`All done! ${data.completed} lessons downloaded.`);
        showIdleUI();
        break;
      }

      if (total > 0) {
        const pct = Math.round((data.completed / total) * 100);
        setProgress(pct);
        const suffix = data.diskPaused ? ' - PAUSED (disk full)' : '';
        setStatus(`${data.completed}/${total} lessons (${pct}%) - ${data.currentCourse || '...'}${suffix}`);
      } else {
        setStatus(`Scanning modules... ${data.currentCourse || ''}`);
      }

      // Update module list highlighting
      const items = document.querySelectorAll('.course-list .item');
      items.forEach((item, idx) => {
        if (idx < data.completed) item.className = 'item done';
        else if (idx === data.completed) item.className = 'item active';
        else item.className = 'item';
      });
    } catch {}
    await new Promise(r => setTimeout(r, 2000));
  }
}

init();
