// SKOOL-DOWN-COURSE - Service Worker
// Orchestrates: navigate tab -> wait -> extract via executeScript -> send to backend

const BACKEND = 'http://localhost:9876';

// Keepalive via alarms
chrome.alarms.create('keepalive', { periodInMinutes: 0.25 });
chrome.alarms.onAlarm.addListener(function() {
  if (downloadRunning) {
    console.log('[SDC] keepalive tick - download running');
  }
});

var downloadRunning = false;
var stopRequested = false;
var pauseRequested = false;
var pauseResolver = null;

// Offscreen document keepalive - prevents SW termination during download
async function ensureOffscreen() {
  try {
    var contexts = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
    if (!contexts || contexts.length === 0) {
      await chrome.offscreen.createDocument({
        url: 'offscreen.html',
        reasons: ['WORKERS'],
        justification: 'Keep service worker alive during download'
      });
      console.log('[SDC] Offscreen document created');
    }
  } catch(err) {
    console.log('[SDC] Offscreen error (ok):', err.message);
  }
}

async function closeOffscreen() {
  try {
    await chrome.offscreen.closeDocument();
    console.log('[SDC] Offscreen document closed');
  } catch(err) {}
}

// ============================================================
// MESSAGE HANDLER
// ============================================================

chrome.runtime.onMessage.addListener(function(msg, sender, sendResponse) {
  if (msg.action === 'keepalive') {
    sendResponse({ alive: true });
    return false;
  }

  if (msg.action === 'startDownload') {
    downloadRunning = true;
    stopRequested = false;
    pauseRequested = false;
    pauseResolver = null;
    console.log('[SDC SW] Received startDownload, modules:', msg.scanData ? msg.scanData.modules.length : 'NONE');
    // Create offscreen doc to keep SW alive, then start download
    ensureOffscreen().then(function() {
      return runDownload(msg.scanData, msg.options);
    }).catch(function(err) {
      console.error('[SDC SW] runDownload crashed:', err);
    }).finally(function() {
      downloadRunning = false;
      closeOffscreen();
    });
    sendResponse({ success: true });
    return true;
  }

  if (msg.action === 'stopDownload') {
    stopRequested = true;
    pauseRequested = false;
    downloadRunning = false;
    // If paused, resolve the wait so the loop can exit
    if (pauseResolver) { pauseResolver(); pauseResolver = null; }
    sendResponse({ success: true });
    return false;
  }

  if (msg.action === 'pauseDownload') {
    pauseRequested = true;
    console.log('[SDC SW] Pause requested');
    sendResponse({ success: true });
    return false;
  }

  if (msg.action === 'resumeDownload') {
    pauseRequested = false;
    console.log('[SDC SW] Resume requested');
    if (pauseResolver) { pauseResolver(); pauseResolver = null; }
    sendResponse({ success: true });
    return false;
  }

  if (msg.action === 'startCommunityDownload') {
    downloadRunning = true;
    stopRequested = false;
    pauseRequested = false;
    pauseResolver = null;
    ensureOffscreen().then(function() {
      return runCommunityDownload(msg.groupUrl, msg.maxPosts || 50);
    }).catch(function(err) {
      console.error('[SDC SW] Community download crashed:', err);
    }).finally(function() {
      downloadRunning = false;
      closeOffscreen();
    });
    sendResponse({ success: true });
    return true;
  }

  if (msg.action === 'captureFullPage') {
    captureScreenshot(sender.tab ? sender.tab.id : null).then(sendResponse);
    return true;
  }

  if (msg.action === 'downloadResource') {
    chrome.downloads.download({
      url: msg.url,
      filename: msg.subfolder ? msg.subfolder + '/' + msg.filename : msg.filename,
      conflictAction: 'uniquify'
    }, function(id) {
      sendResponse({ success: !chrome.runtime.lastError, downloadId: id });
    });
    return true;
  }

  if (msg.action === 'captureHtml') {
    // Forward to content script on the tab
    return false;
  }
});

// ============================================================
// MAIN DOWNLOAD LOOP
// ============================================================

async function runDownload(scanData, options) {
  await postBackend('/course-progress', {
    courseIndex: -1, courseName: 'runDownload() entered', status: 'debug'
  });

  if (!scanData) {
    await postBackend('/course-progress', {
      courseIndex: -1, courseName: 'ERROR: scanData missing', status: 'error'
    });
    return;
  }

  var groupName = scanData.groupName || '';

  // If ONLY community is requested (no html/video/screenshot), skip course download
  var wantsCourses = options.html || options.video || options.screenshot;
  if (!wantsCourses && options.community && groupName) {
    var groupUrl = 'https://www.skool.com/' + groupName;
    console.log('[SDC] Community-only mode: ' + groupUrl);
    await runCommunityDownload(groupUrl);
    downloadRunning = false;
    await postBackend('/download-complete', {});
    return;
  }

  if (!scanData.modules) {
    await postBackend('/course-progress', {
      courseIndex: -1, courseName: 'ERROR: no modules found', status: 'error'
    });
    return;
  }

  var modules = scanData.modules;
  var groupId = scanData.groupId || '';
  var startFrom = (options.startFrom || 1) - 1;

  var tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tabs || !tabs[0]) {
    await postBackend('/course-progress', {
      courseIndex: -1, courseName: 'ERROR: no active tab found', status: 'error'
    });
    return;
  }
  var tabId = tabs[0].id;

  await postBackend('/course-progress', {
    courseIndex: -1,
    courseName: 'Starting: ' + modules.length + ' modules, tab=' + tabId + ', group=' + groupName,
    status: 'debug'
  });

  console.log('[SDC] Starting download. Modules: ' + modules.length + ', tab: ' + tabId);
  var globalLesson = 0;

  try {
    for (var mi = 0; mi < modules.length; mi++) {
      if (stopRequested) break;

      var mod = modules[mi];
      console.log('[SDC] Module ' + (mi + 1) + '/' + modules.length + ': ' + mod.title);

      // Navigate to module page (skip if already there)
      var currentTab = await chrome.tabs.get(tabId);
      var currentBase = (currentTab.url || '').split('?')[0];
      var targetBase = (mod.url || '').split('?')[0];
      if (currentBase !== targetBase) {
        await goToPage(tabId, mod.url);
      } else {
        console.log('[SDC] Already on module page, skipping navigation');
        await sleep(1000);
      }

      // Extract lessons
      var lessons = await extractLessons(tabId, mod.slug, groupName);
      console.log('[SDC] Found ' + lessons.length + ' lessons in ' + mod.title);

      await postBackend('/course-progress', {
        courseIndex: mi,
        courseName: '[DEBUG] extractLessons returned ' + lessons.length + ' for ' + mod.title + ' (slug=' + mod.slug + ', group=' + groupName + ')',
        status: 'debug'
      });

      if (lessons.length === 0) continue;

      // Tell backend about this module's lessons
      await postBackend('/course-progress', {
        courseIndex: mi,
        courseName: '[Module] ' + mod.title,
        status: 'module_start',
        lessonCount: lessons.length
      });

      // Process each lesson
      for (var li = 0; li < lessons.length; li++) {
        if (stopRequested) break;
        if (globalLesson < startFrom) { globalLesson++; continue; }

        var lesson = lessons[li];
        console.log('[SDC] Lesson ' + (globalLesson + 1) + ': ' + lesson.name);

        await postBackend('/course-progress', {
          courseIndex: globalLesson,
          courseName: lesson.name,
          status: 'processing'
        });

        // Navigate to lesson page
        var lessonUrl = 'https://www.skool.com' + lesson.href;
        await goToPage(tabId, lessonUrl);

        // Extract page metadata
        var meta = await extractMeta(tabId, lesson.name, groupId);

        // Capture HTML
        var html = null;
        if (options.html) {
          html = await captureHtml(tabId);
        }

        // Capture screenshot
        var screenshot = null;
        if (options.screenshot) {
          var ss = await captureScreenshot(tabId);
          screenshot = ss ? ss.dataUrl : null;
        }

        // Extract video info
        var videoInfo = null;
        if (options.video && (meta.videoUrl || meta.videoId)) {
          videoInfo = {
            url: meta.videoUrl || '',
            stream: meta.videoStream || '',
            type: meta.videoType || '',
            title: lesson.name,
            thumbnail: meta.videoThumbnail || '',
            durationMs: meta.videoDurationMs || 0,
            videoId: meta.videoId || null
          };

          // For external videos, get iframe embed URL
          if (meta.videoType === 'YouTube' || meta.videoType === 'Loom' ||
              meta.videoType === 'Vimeo' || meta.videoType === 'Wistia') {
            videoInfo.embedUrl = await getVideoIframe(tabId);
          }

          // For Skool native, click play and get M3U8
          if (meta.videoType === 'Skool' || (!meta.videoUrl && meta.videoId)) {
            var muxData = await clickAndGetM3U8(tabId);
            if (muxData) {
              videoInfo.m3u8Url = muxData.m3u8Url;
              videoInfo.playbackId = muxData.playbackId;
              videoInfo.customDomain = muxData.customDomain;
            }
          }
        }

        // Download resource attachments via chrome.downloads
        if (meta.resources && meta.resources.length > 0) {
          var safeMod = sanitize(mod.title);
          var safeLes = sanitize(lesson.name);
          for (var ri = 0; ri < meta.resources.length; ri++) {
            var res = meta.resources[ri];
            if (res.downloadUrl) {
              chrome.downloads.download({
                url: res.downloadUrl,
                filename: safeMod + '/' + safeLes + '/' + (res.fileName || res.title || 'resource'),
                conflictAction: 'uniquify'
              });
              await sleep(500);
            }
          }
        }

        // Send everything to backend
        await postBackend('/course-data', {
          courseIndex: globalLesson,
          moduleIndex: mi,
          lessonIndex: li,
          courseName: sanitize(lesson.name),
          moduleName: sanitize(mod.title),
          meta: meta,
          html: html,
          screenshotDataUrl: screenshot,
          videoInfo: videoInfo
        });

        console.log('[SDC] Lesson done: ' + lesson.name);

        // Mark complete
        if (options.markComplete) {
          await markComplete(tabId);
        }

        globalLesson++;

        // Check for pause between lessons
        if (pauseRequested && !stopRequested) {
          console.log('[SDC] Paused after lesson ' + globalLesson);
          await postBackend('/course-progress', {
            courseIndex: globalLesson - 1, courseName: 'Paused', status: 'paused'
          });
          await new Promise(function(resolve) { pauseResolver = resolve; });
          console.log('[SDC] Resumed');
        }

        await sleep(2000);
      }
    }
  } catch (err) {
    console.error('[SDC] DOWNLOAD ERROR:', err.message, err.stack);
    await postBackend('/course-progress', {
      courseIndex: -1, courseName: 'ERROR: ' + err.message, status: 'error'
    });
  }

  // Community posts download (after course lessons)
  if (options.community && !stopRequested && groupName) {
    var groupUrl = 'https://www.skool.com/' + groupName;
    console.log('[SDC] Starting community download: ' + groupUrl);
    await runCommunityDownload(groupUrl);
  }

  downloadRunning = false;
  await postBackend('/download-complete', {});
  console.log('[SDC] Download complete');
}

// ============================================================
// PAGE NAVIGATION - simple: update tab URL + fixed wait
// ============================================================

async function goToPage(tabId, url) {
  console.log('[SDC] Navigate: ' + url);
  await chrome.tabs.update(tabId, { url: url });
  // Wait for load via onUpdated
  await new Promise(function(resolve) {
    var resolved = false;
    function onUpdate(id, info) {
      if (id === tabId && info.status === 'complete' && !resolved) {
        resolved = true;
        chrome.tabs.onUpdated.removeListener(onUpdate);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(onUpdate);
    // Timeout fallback 20s
    setTimeout(function() {
      if (!resolved) {
        resolved = true;
        chrome.tabs.onUpdated.removeListener(onUpdate);
        resolve();
      }
    }, 20000);
  });
  // Extra wait for Next.js hydration + __NEXT_DATA__
  await sleep(3000);
}

// ============================================================
// DATA EXTRACTION via chrome.scripting.executeScript
// All functions use ES5 to avoid issues with older Chrome
// ============================================================

async function extractLessons(tabId, moduleSlug, groupName) {
  try {
    var results = await chrome.scripting.executeScript({
      target: { tabId: tabId },
      world: 'MAIN',
      func: function(slug, gName) {
        try {
          var pp = null;
          if (window.__NEXT_DATA__ && window.__NEXT_DATA__.props && window.__NEXT_DATA__.props.pageProps) {
            pp = window.__NEXT_DATA__.props.pageProps;
          } else {
            var el = document.getElementById('__NEXT_DATA__');
            if (!el) return [];
            var nd = JSON.parse(el.textContent);
            pp = nd.props && nd.props.pageProps;
          }
          if (!pp || !pp.course || !pp.course.children) return [];

          var lessons = [];
          var children = pp.course.children;
          for (var i = 0; i < children.length; i++) {
            var child = children[i];
            if (!child.course) continue;
            var meta = child.course.metadata || {};

            if (child.children && child.children.length > 0) {
              // Nested sections
              for (var j = 0; j < child.children.length; j++) {
                var sub = child.children[j];
                if (!sub.course) continue;
                var sm = sub.course.metadata || {};
                if (sm.hasAccess === false) continue;
                lessons.push({
                  id: sub.course.id, name: sm.title || sub.course.name,
                  href: '/' + gName + '/classroom/' + slug + '?md=' + sub.course.id,
                  videoLink: sm.videoLink || null, videoId: sm.videoId || null,
                  videoStream: sm.videoStream || null
                });
              }
            } else {
              // Flat
              if (meta.hasAccess === false) continue;
              lessons.push({
                id: child.course.id, name: meta.title || child.course.name,
                href: '/' + gName + '/classroom/' + slug + '?md=' + child.course.id,
                videoLink: meta.videoLink || null, videoId: meta.videoId || null,
                videoStream: meta.videoStream || null
              });
            }
          }
          return lessons;
        } catch(e) { return []; }
      },
      args: [moduleSlug, groupName]
    });
    return (results && results[0] && results[0].result) || [];
  } catch(err) {
    console.error('[SDC] extractLessons error:', err.message);
    return [];
  }
}

async function extractMeta(tabId, lessonName, groupId) {
  try {
    var results = await chrome.scripting.executeScript({
      target: { tabId: tabId },
      world: 'MAIN',
      func: function(lName, gId) {
        try {
          var pp = null;
          if (window.__NEXT_DATA__ && window.__NEXT_DATA__.props && window.__NEXT_DATA__.props.pageProps) {
            pp = window.__NEXT_DATA__.props.pageProps;
          } else {
            var el = document.getElementById('__NEXT_DATA__');
            if (!el) return { title: lName, pageUrl: location.href };
            var nd = JSON.parse(el.textContent);
            pp = nd.props && nd.props.pageProps;
          }
          var selMod = pp ? pp.selectedModule : null;
          var meta = { title: lName, videoUrl: '', videoStream: '', videoType: '',
            videoDurationMs: 0, videoThumbnail: '', videoId: null, resources: [],
            description: '', pageUrl: location.href };

          function find(children, id) {
            if (!children) return null;
            for (var i = 0; i < children.length; i++) {
              if (children[i].course && children[i].course.id === id) return children[i].course;
              if (children[i].children) { var f = find(children[i].children, id); if (f) return f; }
            }
            return null;
          }

          var mod = (pp && pp.course) ? find(pp.course.children, selMod) : null;
          if (mod && mod.metadata) {
            var m = mod.metadata;
            meta.videoUrl = m.videoLink || '';
            meta.videoStream = m.videoStream || '';
            meta.videoId = m.videoId || null;
            meta.videoDurationMs = m.videoLenMs || 0;
            meta.videoThumbnail = m.videoThumbnail || '';
            meta.description = m.desc || m.description || '';

            var vl = (meta.videoUrl || '').toLowerCase();
            if (vl.indexOf('youtube') !== -1 || vl.indexOf('youtu.be') !== -1) meta.videoType = 'YouTube';
            else if (vl.indexOf('loom') !== -1) meta.videoType = 'Loom';
            else if (vl.indexOf('vimeo') !== -1) meta.videoType = 'Vimeo';
            else if (vl.indexOf('wistia') !== -1) meta.videoType = 'Wistia';
            else if (meta.videoId) meta.videoType = 'Skool';

            if (m.resources) {
              try {
                var res = typeof m.resources === 'string' ? JSON.parse(m.resources) : m.resources;
                if (Array.isArray(res)) {
                  meta.resources = [];
                  for (var i = 0; i < res.length; i++) {
                    meta.resources.push({
                      title: res[i].title || 'Untitled',
                      fileName: res[i].file_name || null,
                      contentType: res[i].file_content_type || null,
                      downloadUrl: (res[i].file_id && gId) ? 'https://assets.skool.com/f/' + gId + '/' + res[i].file_id : null
                    });
                  }
                }
              } catch(e) {}
            }
          }
          return meta;
        } catch(e) { return { title: lName, error: e.message, pageUrl: location.href }; }
      },
      args: [lessonName, groupId]
    });
    return (results && results[0] && results[0].result) || { title: lessonName };
  } catch(err) {
    console.error('[SDC] extractMeta error:', err.message);
    return { title: lessonName, error: err.message };
  }
}

async function captureHtml(tabId) {
  try {
    // Inject content script then ask it to capture HTML
    await chrome.scripting.executeScript({ target: { tabId: tabId }, files: ['content.js'] });
    await sleep(500);
    var response = await chrome.tabs.sendMessage(tabId, { action: 'captureHtml' });
    return response ? response.html : null;
  } catch(err) {
    console.error('[SDC] captureHtml error:', err.message);
    return null;
  }
}

async function getVideoIframe(tabId) {
  try {
    var results = await chrome.scripting.executeScript({
      target: { tabId: tabId },
      func: function() {
        var iframes = document.querySelectorAll('iframe');
        for (var i = 0; i < iframes.length; i++) {
          var src = iframes[i].src || '';
          if (src.indexOf('youtube.com') !== -1 || src.indexOf('loom.com') !== -1 ||
              src.indexOf('vimeo.com') !== -1 || src.indexOf('wistia') !== -1) return src;
        }
        return '';
      }
    });
    return (results && results[0]) ? results[0].result : '';
  } catch(err) { return ''; }
}

async function clickAndGetM3U8(tabId) {
  // Find play button position
  try {
    var posResults = await chrome.scripting.executeScript({
      target: { tabId: tabId },
      func: function() {
        var dur = document.querySelector('[class*="VideoDuration"]');
        if (dur) {
          var el = dur;
          for (var i = 0; i < 10; i++) {
            el = el.parentElement;
            if (!el) break;
            var r = el.getBoundingClientRect();
            if (r.width > 300 && r.height > 150)
              return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
          }
        }
        var cover = document.querySelector('[class*="CoverImage"], [class*="VideoPlayer"]');
        if (cover) {
          var r2 = cover.getBoundingClientRect();
          return { x: Math.round(r2.x + r2.width / 2), y: Math.round(r2.y + r2.height / 2) };
        }
        return null;
      }
    });

    var pos = posResults && posResults[0] ? posResults[0].result : null;
    if (!pos) return null;

    // CDP click to trigger player
    await cdpClick(tabId, pos.x, pos.y);
    await sleep(4000);

    // Poll for mux-player + M3U8
    for (var attempt = 0; attempt < 12; attempt++) {
      await sleep(2000);
      var checkResults = await chrome.scripting.executeScript({
        target: { tabId: tabId },
        func: function() {
          var mux = document.querySelector('mux-player');
          if (!mux) return null;
          var urls = [];
          var entries = performance.getEntries();
          for (var i = 0; i < entries.length; i++) {
            if (entries[i].name.indexOf('.m3u8') !== -1) urls.push(entries[i].name);
          }
          return {
            playbackId: mux.getAttribute('playback-id'),
            customDomain: mux.getAttribute('custom-domain') || 'video.skool.com',
            m3u8Url: urls.length > 0 ? urls[0] : ''
          };
        }
      });
      var data = checkResults && checkResults[0] ? checkResults[0].result : null;
      if (data && data.m3u8Url) return data;
      if (data && data.playbackId) {
        data.m3u8Url = 'https://' + data.customDomain + '/' + data.playbackId + '.m3u8';
        return data;
      }
    }
  } catch(err) {
    console.error('[SDC] clickAndGetM3U8 error:', err.message);
  }
  return null;
}

async function markComplete(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tabId },
      func: function() {
        var btns = document.querySelectorAll('button');
        for (var i = 0; i < btns.length; i++) {
          var svg = btns[i].querySelector('svg');
          if (!svg) continue;
          var vb = svg.getAttribute('viewBox');
          if (vb === '0 0 40 40') return; // already done
          if (vb === '0 0 24 24' && svg.querySelectorAll('path').length >= 2) {
            btns[i].click();
            return;
          }
        }
      }
    });
    await sleep(2000);
  } catch(err) {}
}

// ============================================================
// HELPERS
// ============================================================

function sleep(ms) { return new Promise(function(r) { setTimeout(r, ms); }); }

function sanitize(name) {
  return (name || '')
    .replace(/[\u{1F000}-\u{1FFFF}|\u{2600}-\u{27BF}|\u{FE00}-\u{FE0F}|\u{200D}|\u{20E3}|\u{E0020}-\u{E007F}]/gu, '')
    .replace(/[<>:"/\\|?*]/g, '_').replace(/\s+/g, ' ').trim().substring(0, 100);
}

async function postBackend(path, data) {
  try {
    await fetch(BACKEND + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
  } catch(err) {
    console.error('[SDC] Backend POST failed:', path, err.message);
  }
}

async function captureScreenshot(tabId) {
  if (!tabId) {
    var tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    tabId = tabs[0] ? tabs[0].id : null;
  }
  if (!tabId) return null;
  try {
    var dataUrl = await chrome.tabs.captureVisibleTab(null, { format: 'png' });
    return { dataUrl: dataUrl };
  } catch(err) {
    console.error('[SDC] Screenshot error:', err.message);
    return null;
  }
}

async function cdpClick(tabId, x, y) {
  try {
    await chrome.debugger.attach({ tabId: tabId }, '1.3');
    var opts = { x: x, y: y, button: 'left', clickCount: 1 };
    await chrome.debugger.sendCommand({ tabId: tabId }, 'Input.dispatchMouseEvent',
      Object.assign({ type: 'mousePressed' }, opts));
    await chrome.debugger.sendCommand({ tabId: tabId }, 'Input.dispatchMouseEvent',
      Object.assign({ type: 'mouseReleased' }, opts));
    await chrome.debugger.detach({ tabId: tabId });
  } catch(err) {
    console.error('[SDC] cdpClick error:', err.message);
    try { await chrome.debugger.detach({ tabId: tabId }); } catch(e) {}
  }
}

// ============================================================
// COMMUNITY POSTS DOWNLOAD
// ============================================================

async function runCommunityDownload(groupUrl, maxPosts) {
  maxPosts = maxPosts || 9999; // default: all pages
  var tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tabs || !tabs[0]) return;
  var tabId = tabs[0].id;

  await postBackend('/course-progress', {
    courseIndex: -1, courseName: 'Starting community download (all pages)', status: 'community'
  });

  // Step 1: Collect post summaries from ALL listing pages
  var allPosts = [];
  var page = 1;
  var seenIds = {};
  while (allPosts.length < maxPosts) {
    if (stopRequested) break;
    var pageUrl = groupUrl + (page > 1 ? '?p=' + page : '');
    console.log('[SDC] Community page ' + page + ': ' + pageUrl);
    await goToPage(tabId, pageUrl);

    var pageData = await chrome.scripting.executeScript({
      target: { tabId: tabId }, world: 'MAIN',
      func: function() {
        try {
          var pp = (window.__NEXT_DATA__ || JSON.parse(document.getElementById('__NEXT_DATA__').textContent)).props.pageProps;
          var trees = pp.postTrees || [];
          return { total: pp.total || 0, posts: trees.map(function(t) {
            var p = t.post || {};
            var m = p.metadata || {};
            var u = p.user || {};
            return {
              id: p.id, name: p.name, title: m.title || '',
              content: m.content || '', upvotes: m.upvotes || 0,
              commentCount: m.comments || 0, pinned: m.pinned || 0,
              imagePreview: m.imagePreview || '', createdAt: p.createdAt || '',
              authorName: (u.firstName || '') + ' ' + (u.lastName || ''),
              authorPicture: (u.metadata && u.metadata.pictureBubble) || '',
              videoIds: m.videoIds || '',
              videoLinks: m.videoLinks || '',
              videoLinksData: m.videoLinksData || ''
            };
          })};
        } catch(e) { return { total: 0, posts: [] }; }
      }
    });

    var result = (pageData && pageData[0]) ? pageData[0].result : { total: 0, posts: [] };
    var posts = result.posts || [];
    if (!posts.length) break;

    // Deduplicate (pinned posts repeat across pages)
    var newCount = 0;
    for (var pi = 0; pi < posts.length; pi++) {
      if (posts[pi].id && !seenIds[posts[pi].id]) {
        seenIds[posts[pi].id] = true;
        allPosts.push(posts[pi]);
        newCount++;
      }
    }

    await postBackend('/course-progress', {
      courseIndex: -1,
      courseName: 'Community: page ' + page + ' (' + allPosts.length + '/' + result.total + ' posts)',
      status: 'community'
    });

    page++;
    if (newCount === 0 || allPosts.length >= (result.total || 9999)) break;
  }

  console.log('[SDC] Collected ' + allPosts.length + ' unique posts from ' + (page - 1) + ' pages');

  // Step 2: For each post, navigate to it, scrape comments + extract video info
  for (var i = 0; i < allPosts.length; i++) {
    if (stopRequested) break;
    if (pauseRequested && !stopRequested) {
      await new Promise(function(resolve) { pauseResolver = resolve; });
    }

    var post = allPosts[i];
    var needsVisit = post.commentCount > 0 || post.videoIds || post.videoLinks;

    await postBackend('/course-progress', {
      courseIndex: i, courseName: 'Community ' + (i+1) + '/' + allPosts.length + ': ' + (post.title || post.name || '').substring(0, 40),
      status: 'processing'
    });

    if (needsVisit && post.name) {
      var postUrl = groupUrl + '/' + post.name;
      await goToPage(tabId, postUrl);

      // Scroll down + wait to trigger lazy comment loading
      await chrome.scripting.executeScript({
        target: { tabId: tabId },
        func: function() { window.scrollTo(0, document.body.scrollHeight); }
      });
      await sleep(3000);

      // Scrape comments
      var comments = await chrome.scripting.executeScript({
        target: { tabId: tabId }, world: 'MAIN',
        func: function() {
          try {
            var results = [];
            var commentEls = document.querySelectorAll('[class*="CommentItemContainer"]');
            for (var i = 0; i < commentEls.length; i++) {
              var el = commentEls[i];
              var bubble = el.querySelector('[class*="CommentItemBubble"]');
              if (!bubble) continue;
              var authorEl = bubble.querySelector('a[href*="/u/"], [class*="UserName"], [class*="PostedBy"]');
              var dateEl = bubble.querySelector('[class*="PostedDate"]');
              var bodyEl = bubble.querySelector('[class*="PostBody"]');
              var content = '';
              var contentHtml = '';
              if (bodyEl) {
                content = bodyEl.textContent.trim();
                contentHtml = bodyEl.innerHTML;
              } else {
                var clone = bubble.cloneNode(true);
                var toRemove = clone.querySelectorAll('a[href*="/u/"], [class*="PostedDate"], [class*="PostActi"]');
                for (var r = 0; r < toRemove.length; r++) toRemove[r].remove();
                content = clone.textContent.trim();
              }
              results.push({
                author: authorEl ? authorEl.textContent.trim() : '',
                content: content,
                time: dateEl ? dateEl.textContent.trim().replace('• ', '') : '',
                html: contentHtml
              });
            }
            if (results.length === 0) {
              var section = document.querySelector('[class*="CommentsSection"]');
              if (section) results.push({ author: '', content: '', time: '', html: section.innerHTML });
            }
            return results;
          } catch(e) { return []; }
        }
      });
      allPosts[i].comments = (comments && comments[0]) ? comments[0].result : [];

      // Extract video info from the post page __NEXT_DATA__
      if (post.videoIds || post.videoLinks) {
        var videoInfo = await extractPostVideo(tabId, post);
        if (videoInfo) allPosts[i].videoInfo = videoInfo;
      }

      // Also extract videos array from postTree (Skool native with signed tokens)
      if (post.videoIds && !allPosts[i].videoInfo) {
        var nativeVideo = await chrome.scripting.executeScript({
          target: { tabId: tabId }, world: 'MAIN',
          func: function() {
            try {
              var pp = (window.__NEXT_DATA__ || JSON.parse(document.getElementById('__NEXT_DATA__').textContent)).props.pageProps;
              var tree = pp.postTree;
              if (!tree || !tree.videos || !tree.videos.length) return null;
              var v = tree.videos[0];
              return {
                playbackId: v.playbackId || '',
                playbackToken: v.playbackToken || '',
                duration: v.duration || 0,
                aspectRatio: v.aspectRatio || ''
              };
            } catch(e) { return null; }
          }
        });
        var vid = nativeVideo && nativeVideo[0] ? nativeVideo[0].result : null;
        if (vid && vid.playbackId) {
          var m3u8 = 'https://stream.mux.com/' + vid.playbackId + '.m3u8?token=' + vid.playbackToken;
          allPosts[i].videoInfo = {
            type: 'Skool', m3u8Url: m3u8, playbackId: vid.playbackId,
            customDomain: 'stream.mux.com', title: post.title || '',
            duration: vid.duration
          };
        }
      }
    }

    console.log('[SDC] Post ' + (i + 1) + '/' + allPosts.length + ': ' + (post.title || '').substring(0, 40) +
      ' (' + (allPosts[i].comments ? allPosts[i].comments.length : 0) + ' comments' +
      (allPosts[i].videoInfo ? ', video: ' + allPosts[i].videoInfo.type : '') + ')');
  }

  // Step 3: Send all posts to backend to generate HTML + download videos
  await postBackend('/community-data', {
    posts: allPosts,
    groupUrl: groupUrl,
    maxPosts: maxPosts
  });

  await postBackend('/course-progress', {
    courseIndex: allPosts.length - 1,
    courseName: 'Community download complete (' + allPosts.length + ' posts)',
    status: 'community_done'
  });

  console.log('[SDC] Community download complete: ' + allPosts.length + ' posts');
}

// Extract video from a community post page (Skool native or external)
async function extractPostVideo(tabId, post) {
  try {
    // Check for external video links (YouTube, Loom, Vimeo)
    var videoLinks = post.videoLinks || '';
    var videoLinksData = post.videoLinksData || '';
    if (videoLinks && typeof videoLinks === 'string' && videoLinks.indexOf('http') !== -1) {
      var vl = videoLinks.toLowerCase();
      var type = vl.indexOf('youtube') !== -1 || vl.indexOf('youtu.be') !== -1 ? 'YouTube' :
                 vl.indexOf('loom') !== -1 ? 'Loom' :
                 vl.indexOf('vimeo') !== -1 ? 'Vimeo' : 'Video';
      return { type: type, url: videoLinks, title: post.title || '' };
    }

    // Parse videoLinksData for external URLs
    if (videoLinksData) {
      try {
        var linksArr = typeof videoLinksData === 'string' ? JSON.parse(videoLinksData) : videoLinksData;
        if (Array.isArray(linksArr) && linksArr.length > 0) {
          var link = linksArr[0];
          var url = link.url || link.href || '';
          if (url) {
            var vl2 = url.toLowerCase();
            var type2 = vl2.indexOf('youtube') !== -1 || vl2.indexOf('youtu.be') !== -1 ? 'YouTube' :
                        vl2.indexOf('loom') !== -1 ? 'Loom' : 'Video';
            return { type: type2, url: url, title: post.title || '' };
          }
        }
      } catch(e) {}
    }

    // Skool native video (videoIds present) — click play and get M3U8
    if (post.videoIds) {
      // Find the video thumbnail / play area
      var posResults = await chrome.scripting.executeScript({
        target: { tabId: tabId },
        func: function() {
          // Skool community video: VideoThumbnailWrapper or VideoDuration
          var thumb = document.querySelector('[class*="VideoThumbnailWrapper"], [class*="VideoDuration"]');
          if (thumb) {
            var el = thumb;
            for (var i = 0; i < 5; i++) {
              el = el.parentElement;
              if (!el) break;
              var r = el.getBoundingClientRect();
              if (r.width > 200 && r.height > 100)
                return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
            }
            var r2 = thumb.getBoundingClientRect();
            return { x: Math.round(r2.x + r2.width / 2), y: Math.round(r2.y + r2.height / 2) };
          }
          return null;
        }
      });

      var pos = posResults && posResults[0] ? posResults[0].result : null;
      if (pos && pos.x) {
        // Scroll video into view first
        await chrome.scripting.executeScript({
          target: { tabId: tabId },
          func: function() { window.scrollTo(0, 0); }
        });
        await sleep(500);

        await cdpClick(tabId, pos.x, pos.y);
        await sleep(4000);

        // Poll for mux-player + M3U8
        for (var attempt = 0; attempt < 10; attempt++) {
          await sleep(2000);
          var checkResults = await chrome.scripting.executeScript({
            target: { tabId: tabId },
            func: function() {
              var mux = document.querySelector('mux-player');
              if (!mux) return null;
              var urls = [];
              var entries = performance.getEntries();
              for (var i = 0; i < entries.length; i++) {
                if (entries[i].name.indexOf('.m3u8') !== -1) urls.push(entries[i].name);
              }
              return {
                playbackId: mux.getAttribute('playback-id'),
                customDomain: mux.getAttribute('custom-domain') || 'video.skool.com',
                m3u8Url: urls.length > 0 ? urls[0] : ''
              };
            }
          });
          var data = checkResults && checkResults[0] ? checkResults[0].result : null;
          if (data && data.m3u8Url) {
            return { type: 'Skool', m3u8Url: data.m3u8Url, playbackId: data.playbackId,
                     customDomain: data.customDomain, title: post.title || '' };
          }
          if (data && data.playbackId) {
            var m3u8 = 'https://' + data.customDomain + '/' + data.playbackId + '.m3u8';
            return { type: 'Skool', m3u8Url: m3u8, playbackId: data.playbackId,
                     customDomain: data.customDomain, title: post.title || '' };
          }
        }
      }

      // Also check for iframe embeds (YouTube in post body)
      var iframeSrc = await getVideoIframe(tabId);
      if (iframeSrc) {
        var vl3 = iframeSrc.toLowerCase();
        var type3 = vl3.indexOf('youtube') !== -1 ? 'YouTube' :
                    vl3.indexOf('loom') !== -1 ? 'Loom' :
                    vl3.indexOf('vimeo') !== -1 ? 'Vimeo' : 'Video';
        return { type: type3, url: iframeSrc, embedUrl: iframeSrc, title: post.title || '' };
      }
    }
  } catch(err) {
    console.error('[SDC] extractPostVideo error:', err.message);
  }
  return null;
}

console.log('[SDC] Service worker loaded');
