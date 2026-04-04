// SKOOL-DOWN-COURSE - Content Script
// Runs on skool.com pages. Handles course scanning, navigation, and data extraction.
//
// PROVEN SELECTORS (verified against live https://www.skool.com/ai-video-creators/classroom):
//
// Classroom main page (/classroom):
//   - __NEXT_DATA__ -> props.pageProps.allCourses = top-level modules (17 items)
//   - Each module: { id, name (hash slug), metadata: { title, description } }
//   - URL to enter module: /{group}/classroom/{module.name}
//   - Module cards are <div> not <a>, no href - use URL navigation
//   - Pagination buttons: "Previous", "1", "Next"
//   - courseRoute setting is False (default routing)
//
// Inside a module (/classroom/{slug}):
//   - __NEXT_DATA__ -> props.pageProps.course.children = sections with lessons
//   - data-rbd-draggable-id="set-*" = section containers (chapter groups)
//   - a[href*="/classroom/"] = lesson links
//   - svg[viewBox="0 0 40 25"] = expand chevrons for collapsed sections
//   - [class*="CourseMenuTopTitleText"] = module title
//   - Lesson URL: /{group}/classroom/{lesson.name}?md={lesson.id}
//
// Inside a lesson:
//   - mux-player = Skool native video (playback-id, custom-domain)
//   - video[src^="blob:"] = blob video elements
//   - __NEXT_DATA__ -> selectedModule = current lesson ID
//   - Mark complete: button > svg[viewBox="0 0 40 40"] (green=done) or "0 0 24 24" (not done)

const BACKEND = 'http://localhost:9876';

let stopRequested = false;

// ============================================================
// UTILITIES
// ============================================================

function wait(ms) { return new Promise(r => setTimeout(r, ms)); }

async function poll(desc, fn, maxMs, intervalMs = 600) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    const result = fn();
    if (result) return result;
    await wait(intervalMs);
  }
  console.log(`[SDC] Poll timeout: ${desc} after ${maxMs}ms`);
  return null;
}

function sanitize(name) {
  return name.replace(/[<>:"/\\|?*]/g, '_').replace(/\s+/g, ' ').trim().substring(0, 100);
}

function extractNextData() {
  try {
    const el = document.getElementById('__NEXT_DATA__');
    if (el) return JSON.parse(el.textContent);
  } catch {}
  return null;
}

function getGroupName() {
  // Extract group name from URL: /group-name/classroom/...
  const parts = window.location.pathname.split('/');
  return parts[1] || '';
}

// ============================================================
// PHASE 1: SCAN - Extract all modules from classroom main page
// ============================================================

async function scanCourseStructure() {
  console.log('[SDC] Scanning course structure from __NEXT_DATA__...');

  const nd = extractNextData();
  if (!nd?.props?.pageProps) {
    return { success: false, error: 'No __NEXT_DATA__ pageProps found. Are you on a Skool classroom page?' };
  }

  const pp = nd.props.pageProps;
  const groupName = pp.currentGroup?.name || getGroupName();
  const groupId = pp.currentGroup?.id || '';

  // allCourses contains the top-level modules
  const allCourses = pp.allCourses || [];
  if (allCourses.length === 0) {
    return { success: false, error: 'No courses found in __NEXT_DATA__.allCourses' };
  }

  // Build module list - skip locked modules
  // PROVEN (verified 2026-04-03): locked modules have hasAccess missing/falsy
  // and minAccessLevel set. DOM shows "Unlock at Level X" text.
  const allModules = [];
  const skippedModules = [];
  for (const c of allCourses) {
    const meta = c.metadata || {};
    const hasAccess = meta.hasAccess;
    const minLevel = meta.minAccessLevel;

    // Skip locked: hasAccess is missing/falsy AND minAccessLevel is set
    if (!hasAccess && minLevel != null) {
      skippedModules.push(meta.title || c.name);
      continue;
    }

    allModules.push({
      id: c.id,
      slug: c.name, // hash slug like "e22d16af"
      title: meta.title || c.name,
      description: (meta.description || meta.desc || '').substring(0, 200),
      url: `https://www.skool.com/${groupName}/classroom/${c.name}`,
      unitType: c.unitType,
      isPublic: c.public,
      state: c.state
    });
  }

  // Get site name
  const courseSiteName = pp.currentGroup?.metadata?.name
    || document.title.replace(' · ', ' - ').split(' - ')[0]
    || 'SkoolCourse';

  if (skippedModules.length > 0) {
    console.log(`[SDC] Skipped ${skippedModules.length} locked modules: ${skippedModules.join(', ')}`);
  }
  console.log(`[SDC] Found ${allModules.length} accessible modules for group "${groupName}"`);

  return {
    success: true,
    courseSiteName,
    groupName,
    groupId,
    modules: allModules,
    skippedModules,
    courses: [],
    totalModules: allModules.length
  };
}

// ============================================================
// PHASE 2: DEEP SCAN - Enter each module to get its lessons
// ============================================================

async function scanModuleLessons(moduleSlug, groupName) {
  // Navigate to the module page to load its lesson list
  const moduleUrl = `https://www.skool.com/${groupName}/classroom/${moduleSlug}`;
  console.log(`[SDC] Scanning module: ${moduleUrl}`);

  window.location.href = moduleUrl;
  await wait(4000);

  // Wait for __NEXT_DATA__ to have course.children
  let nd = null;
  for (let attempt = 0; attempt < 10; attempt++) {
    nd = extractNextData();
    if (nd?.props?.pageProps?.course?.children?.length > 0) break;
    await wait(1000);
  }

  if (!nd?.props?.pageProps?.course?.children) {
    console.log('[SDC] No course.children found for this module');
    return [];
  }

  const pp = nd.props.pageProps;
  const course = pp.course;
  const groupId = pp.currentGroup?.id || '';
  const lessons = [];

  // PROVEN STRUCTURE (verified on live site 2026-04-03):
  // course.children can be FLAT (each child IS a lesson with unitType="module")
  // or NESTED (children have their own children = lessons).
  // Check both patterns.
  for (const child of course.children) {
    if (!child.course) continue;
    const meta = child.course.metadata || {};

    if (child.children && child.children.length > 0) {
      // NESTED: child is a section, its children are lessons
      const sectionTitle = meta.title || child.course.name || 'Section';
      for (const lesson of child.children) {
        if (!lesson.course) continue;
        const lMeta = lesson.course.metadata || {};
        // Skip locked lessons (hasAccess === false)
        if (lMeta.hasAccess === false) {
          console.log(`[SDC] Skipping locked lesson: ${lMeta.title || lesson.course.name}`);
          continue;
        }
        lessons.push({
          id: lesson.course.id,
          slug: lesson.course.name,
          name: lMeta.title || lesson.course.name,
          sectionTitle,
          href: `/${groupName}/classroom/${moduleSlug}?md=${lesson.course.id}`,
          videoLink: lMeta.videoLink || null,
          videoStream: lMeta.videoStream || null,
          videoId: lMeta.videoId || null,
          videoLenMs: lMeta.videoLenMs || null,
          videoThumbnail: lMeta.videoThumbnail || null,
          hasResources: !!(lMeta.resources),
          resources: lMeta.resources || null,
          completed: !!lMeta.completed,
          description: lMeta.desc || lMeta.description || ''
        });
      }
    } else {
      // FLAT: child IS a lesson directly (verified: unitType="module", hasChildren=false)
      // Skip locked lessons (hasAccess === false)
      if (meta.hasAccess === false) {
        console.log(`[SDC] Skipping locked lesson: ${meta.title || child.course.name}`);
        continue;
      }
      lessons.push({
        id: child.course.id,
        slug: child.course.name,
        name: meta.title || child.course.name,
        sectionTitle: '', // flat structure, no section grouping
        href: `/${groupName}/classroom/${moduleSlug}?md=${child.course.id}`,
        videoLink: meta.videoLink || null,
        videoStream: meta.videoStream || null,
        videoId: meta.videoId || null,
        videoLenMs: meta.videoLenMs || null,
        videoThumbnail: meta.videoThumbnail || null,
        hasResources: !!(meta.resources),
        resources: meta.resources || null,
        completed: !!meta.completed,
        description: meta.desc || meta.description || ''
      });
    }
  }

  console.log(`[SDC] Module has ${lessons.length} lessons across ${course.children.length} sections`);
  return lessons;
}

// ============================================================
// PHASE 3: PROCESS EACH LESSON
// ============================================================

function extractCurrentPageMetadata() {
  const nd = extractNextData();
  if (!nd?.props?.pageProps) return null;

  const pp = nd.props.pageProps;
  const selectedModule = pp.selectedModule;
  const course = pp.course;

  if (!course?.children || !selectedModule) return null;

  function findModule(children, id) {
    for (const child of children) {
      if (child.course?.id === id) return child.course;
      if (child.children) {
        const found = findModule(child.children, id);
        if (found) return found;
      }
    }
    return null;
  }

  const mod = findModule(course.children, selectedModule);
  if (!mod?.metadata) return null;

  const m = mod.metadata;
  const groupId = pp.currentGroup?.id || '';
  return {
    title: m.title || document.title,
    videoUrl: m.videoLink || '',
    videoStream: m.videoStream || '',
    videoType: m.videoLink?.includes('loom') ? 'Loom' :
               m.videoLink?.includes('youtube') || m.videoLink?.includes('youtu.be') ? 'YouTube' :
               m.videoLink?.includes('vimeo') ? 'Vimeo' :
               (m.videoLink?.includes('skool') || m.videoStream?.includes('b-cdn')) ? 'Skool' : 'Video',
    videoDurationMs: m.videoLenMs || 0,
    videoThumbnail: m.videoThumbnail || '',
    resources: m.resources || null,
    groupId,
    description: m.description || ''
  };
}

async function capturePageHtml() {
  // PROVEN (tested 2026-04-03): Single-file HTML with active resource links.
  // - All CSS inlined into one <style> block
  // - <base> tag added so relative URLs resolve correctly
  // - Images keep absolute URLs (not base64) so links stay active
  // - Scripts removed (won't work offline)
  // - YouTube/Loom embeds injected as playable iframes
  const clone = document.documentElement.cloneNode(true);

  // Remove overlays / extension junk
  const removeSelectors = ['#automa-progress', '#automa-options-dialog', '#sdc-overlay', '[data-skool-dl-btn]'];
  for (const sel of removeSelectors) {
    clone.querySelectorAll(sel).forEach(el => el.remove());
  }

  // Inline all CSS
  let allCSS = '';
  for (const sheet of document.styleSheets) {
    try {
      if (sheet.cssRules) {
        for (const rule of sheet.cssRules) allCSS += rule.cssText + '\n';
      }
    } catch {
      if (sheet.href) {
        try { allCSS += (await (await fetch(sheet.href)).text()) + '\n'; } catch {}
      }
    }
  }
  for (const style of document.querySelectorAll('style')) {
    allCSS += style.textContent + '\n';
  }

  // Replace stylesheets with single inlined <style>
  clone.querySelectorAll('link[rel="stylesheet"]').forEach(l => l.remove());
  clone.querySelectorAll('head > style').forEach(s => s.remove());
  const styleEl = document.createElement('style');
  styleEl.textContent = allCSS;
  let head = clone.querySelector('head');
  if (head) head.appendChild(styleEl);

  // Add <base> tag for relative resource resolution
  const baseTag = document.createElement('base');
  baseTag.href = window.location.origin;
  head = clone.querySelector('head');
  if (head) head.insertBefore(baseTag, head.firstChild);

  // Fix links to absolute
  clone.querySelectorAll('a[href]').forEach(link => {
    const href = link.getAttribute('href');
    if (href && !href.startsWith('http') && !href.startsWith('data:') && !href.startsWith('#') && !href.startsWith('javascript:')) {
      try { link.setAttribute('href', new URL(href, window.location.href).href); } catch {}
    }
  });

  // Fix images to absolute (keep URLs active, don't base64)
  clone.querySelectorAll('img').forEach(img => {
    const src = img.getAttribute('src');
    if (src && !src.startsWith('http') && !src.startsWith('data:')) {
      try { img.setAttribute('src', new URL(src, window.location.href).href); } catch {}
    }
    // Fix srcset
    const srcset = img.getAttribute('srcset');
    if (srcset) {
      const fixed = srcset.split(',').map(part => {
        const pieces = part.trim().split(/\s+/);
        if (pieces[0] && !pieces[0].startsWith('http') && !pieces[0].startsWith('data:')) {
          try { pieces[0] = new URL(pieces[0], window.location.href).href; } catch {}
        }
        return pieces.join(' ');
      }).join(', ');
      img.setAttribute('srcset', fixed);
    }
  });

  // Embed video player (YouTube/Loom) as playable iframe
  try {
    const nd = extractNextData();
    const pp = nd?.props?.pageProps;
    const selMod = pp?.selectedModule;
    if (pp?.course?.children && selMod) {
      for (const child of pp.course.children) {
        if (child.course?.id !== selMod) continue;
        const videoLink = child.course?.metadata?.videoLink || '';
        const target = clone.querySelector('[class*="VideoPlayerWrapper"]') ||
                       clone.querySelector('[class*="VideoWrapper"]') ||
                       clone.querySelector('[class*="CoverImage"]')?.parentElement;
        if (!target) break;

        // YouTube embed
        const ytMatch = videoLink.match(/(?:youtu\.be\/|youtube\.com\/(?:watch\?v=|embed\/|v\/))([^?&\/]+)/);
        if (ytMatch) {
          target.innerHTML = '<div style="position:relative;width:100%;padding-bottom:56.25%;background:#000;border-radius:10px;overflow:hidden;">' +
            '<iframe style="position:absolute;top:0;left:0;width:100%;height:100%;border:none;" ' +
            'src="https://www.youtube.com/embed/' + ytMatch[1] + '?rel=0&modestbranding=1" ' +
            'frameborder="0" allowfullscreen></iframe></div>';
        }
        // Loom embed
        if (videoLink.includes('loom.com')) {
          const loomUrl = videoLink.replace('/share/', '/embed/');
          target.innerHTML = '<div style="position:relative;width:100%;padding-bottom:56.25%;background:#000;border-radius:10px;overflow:hidden;">' +
            '<iframe style="position:absolute;top:0;left:0;width:100%;height:100%;border:none;" ' +
            'src="' + loomUrl + '" frameborder="0" allowfullscreen></iframe></div>';
        }
        break;
      }
    }
  } catch {}

  // Remove scripts (won't work offline)
  clone.querySelectorAll('script').forEach(s => s.remove());

  const langAttr = clone.getAttribute('lang') ? ` lang="${clone.getAttribute('lang')}"` : '';
  return `<!DOCTYPE html>\n<html${langAttr}>\n${clone.innerHTML}\n</html>`;
}

async function captureFullPageScreenshot() {
  try {
    const result = await chrome.runtime.sendMessage({ action: 'captureFullPage' });
    return result?.dataUrl || null;
  } catch (err) {
    console.error('[SDC] Screenshot error:', err);
    return null;
  }
}

async function sendCourseData(courseIndex, courseName, moduleName, data) {
  try {
    const response = await fetch(`${BACKEND}/course-data`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        courseIndex,
        courseName: sanitize(courseName),
        moduleName: sanitize(moduleName),
        ...data
      })
    });
    return await response.json();
  } catch (err) {
    console.error('[SDC] Backend send error:', err);
    return { success: false, error: err.message };
  }
}

// Detect video type from videoLink URL
function detectVideoType(url) {
  if (!url) return '';
  const u = url.toLowerCase();
  if (u.includes('youtube.com') || u.includes('youtu.be')) return 'YouTube';
  if (u.includes('loom.com')) return 'Loom';
  if (u.includes('vimeo.com')) return 'Vimeo';
  if (u.includes('wistia.com') || u.includes('wistia.net')) return 'Wistia';
  if (u.includes('skool.com') || u.includes('b-cdn.net')) return 'Skool';
  return 'Video';
}

// Extract video info with proven multi-format approach
async function extractVideoInfo(lesson, meta) {
  const info = {
    title: lesson.name,
    thumbnail: meta.videoThumbnail || '',
    durationMs: meta.videoDurationMs || 0,
    videoId: lesson.videoId || null,
    url: '',
    stream: '',
    m3u8Url: '',
    type: '',
    playbackId: null,
    customDomain: null
  };

  // === CASE 1: External video (YouTube, Loom, Vimeo, Wistia) ===
  // videoLink in metadata points to the external service.
  // Also check for iframe embeds in the DOM.
  const videoLink = meta.videoUrl || lesson.videoLink || '';
  if (videoLink) {
    info.type = detectVideoType(videoLink);
    info.url = videoLink;
    console.log(`[SDC] Video type: ${info.type} - ${videoLink.substring(0, 80)}`);

    // For external videos, also look for iframe src (may have embed URL)
    if (['YouTube', 'Loom', 'Vimeo', 'Wistia'].includes(info.type)) {
      const iframes = document.querySelectorAll('iframe');
      for (const iframe of iframes) {
        const src = iframe.src || '';
        if (src.includes('youtube.com') || src.includes('youtu.be') ||
            src.includes('loom.com') || src.includes('vimeo.com') ||
            src.includes('wistia.com') || src.includes('wistia.net')) {
          info.embedUrl = src;
          console.log(`[SDC] Found embed iframe: ${src.substring(0, 100)}`);
          break;
        }
      }
      return info;
    }
  }

  // === CASE 2: Skool native video (videoId, no videoLink) ===
  // Must click the thumbnail to trigger mux-player load, then capture M3U8 from network.
  if (lesson.videoId || (meta.videoStream && meta.videoStream.includes('skool.com'))) {
    info.type = 'Skool';
    console.log(`[SDC] Skool native video - clicking play to trigger player...`);

    // Find the video thumbnail/play area via VideoDuration element
    const durEl = document.querySelector('[class*="VideoDuration"]');
    let clickTarget = null;
    if (durEl) {
      let container = durEl;
      for (let k = 0; k < 10; k++) {
        container = container.parentElement;
        if (!container) break;
        const r = container.getBoundingClientRect();
        if (r.width > 300 && r.height > 150) {
          clickTarget = { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
          break;
        }
      }
    }

    // Fallback: look for CoverImage or any large image area in the content
    if (!clickTarget) {
      const covers = document.querySelectorAll('[class*="CoverImage"], [class*="VideoPlayer"], [class*="videoWrapper"]');
      for (const c of covers) {
        const r = c.getBoundingClientRect();
        if (r.width > 200 && r.height > 100) {
          clickTarget = { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
          break;
        }
      }
    }

    if (clickTarget) {
      // Send CDP click via service worker
      try {
        await chrome.runtime.sendMessage({
          action: 'cdpClick',
          x: clickTarget.x,
          y: clickTarget.y
        });
      } catch {
        // Fallback: direct DOM click at position
        const el = document.elementFromPoint(clickTarget.x, clickTarget.y);
        if (el) el.click();
      }

      // Wait for mux-player to appear and M3U8 to load
      for (let attempt = 0; attempt < 15; attempt++) {
        await wait(2000);

        const muxPlayer = document.querySelector('mux-player');
        if (muxPlayer) {
          info.playbackId = muxPlayer.getAttribute('playback-id');
          info.customDomain = muxPlayer.getAttribute('custom-domain') || 'video.skool.com';
          console.log(`[SDC] Mux player found: ${info.playbackId} @ ${info.customDomain}`);
          break;
        }
      }

      // Capture authenticated M3U8 URL from performance entries
      if (info.playbackId) {
        const entries = performance.getEntries();
        for (const entry of entries) {
          const name = entry.name || '';
          if (name.includes('.m3u8') && name.includes(info.playbackId)) {
            info.m3u8Url = name;
            info.stream = name;
            console.log(`[SDC] M3U8 captured: ${name.substring(0, 120)}`);
            break;
          }
        }
        // Fallback: construct URL without token (may still work)
        if (!info.m3u8Url) {
          info.m3u8Url = `https://${info.customDomain}/${info.playbackId}.m3u8`;
          info.stream = info.m3u8Url;
          console.log(`[SDC] M3U8 constructed (no token): ${info.m3u8Url}`);
        }
      }
    } else {
      console.log('[SDC] Could not find video play area to click');
    }

    return info;
  }

  // === CASE 3: Direct stream URL in metadata ===
  if (meta.videoStream) {
    info.type = 'Skool';
    info.stream = meta.videoStream;
    info.m3u8Url = meta.videoStream;
    console.log(`[SDC] Direct stream: ${meta.videoStream.substring(0, 100)}`);
  }

  return info;
}

async function processSingleLesson(lesson, index, total, moduleTitle, options, groupId) {
  const courseNum = index + 1;
  console.log(`[SDC] Processing lesson ${courseNum}/${total}: ${lesson.name}`);

  // Navigate to the lesson page
  const fullUrl = lesson.href.startsWith('http') ? lesson.href : `https://www.skool.com${lesson.href}`;
  window.location.href = fullUrl;
  await wait(4000);

  // Wait for content to load
  await poll('Lesson content', () => {
    const nd = extractNextData();
    return nd?.props?.pageProps?.selectedModule;
  }, 15000);
  await wait(1000);

  // Notify backend
  await fetch(`${BACKEND}/course-progress`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ courseIndex: index, courseName: lesson.name, status: 'processing' })
  }).catch(() => {});

  // Re-extract metadata from current page
  const currentMeta = extractCurrentPageMetadata();
  const meta = {
    title: lesson.name,
    videoUrl: lesson.videoLink || currentMeta?.videoUrl || '',
    videoStream: lesson.videoStream || currentMeta?.videoStream || '',
    videoType: currentMeta?.videoType || '',
    videoDurationMs: lesson.videoLenMs || currentMeta?.videoDurationMs || 0,
    videoThumbnail: lesson.videoThumbnail || currentMeta?.videoThumbnail || '',
    resources: [],
    description: lesson.description || currentMeta?.description || '',
    pageUrl: window.location.href
  };

  // Parse resources
  const rawRes = lesson.resources || currentMeta?.resources;
  if (rawRes) {
    try {
      const resList = typeof rawRes === 'string' ? JSON.parse(rawRes) : rawRes;
      if (Array.isArray(resList)) {
        for (const r of resList) {
          meta.resources.push({
            title: r.title || 'Untitled',
            fileName: r.file_name || null,
            contentType: r.file_content_type || null,
            downloadUrl: r.file_id && groupId ? `https://assets.skool.com/f/${groupId}/${r.file_id}` : null
          });
        }
      }
    } catch {}
  }

  const courseData = { meta, html: null, screenshotDataUrl: null, videoInfo: null };

  // 1. Capture HTML
  if (options.html) {
    console.log(`[SDC] Capturing HTML for: ${lesson.name}`);
    try { courseData.html = await capturePageHtml(); } catch (err) {
      console.error('[SDC] HTML capture error:', err);
    }
  }

  // 2. Screenshot
  if (options.screenshot) {
    console.log(`[SDC] Capturing screenshot for: ${lesson.name}`);
    try { courseData.screenshotDataUrl = await captureFullPageScreenshot(); } catch (err) {
      console.error('[SDC] Screenshot error:', err);
    }
  }

  // 3. Video extraction (multi-format)
  // PROVEN FORMATS (verified live 2026-04-03):
  //   - Skool native: metadata has videoId (no videoLink). Click thumbnail -> mux-player
  //     appears with playback-id. M3U8 at stream.video.skool.com/{playbackId}.m3u8?token=JWT
  //     Download via ffmpeg. Token comes from performance.getEntries().
  //   - YouTube: metadata.videoLink contains youtube.com/youtu.be URL. Embedded via iframe.
  //     Download via yt-dlp.
  //   - Loom: metadata.videoLink contains loom.com URL. Embedded via iframe.
  //     Download via yt-dlp.
  //   - Vimeo: metadata.videoLink contains vimeo.com URL. Embedded via iframe.
  //     Download via yt-dlp.
  //   - Wistia: metadata.videoLink contains wistia.com URL.
  //     Download via yt-dlp.
  const hasVideo = meta.videoUrl || meta.videoStream || lesson.videoId;
  if (options.video && hasVideo) {
    courseData.videoInfo = await extractVideoInfo(lesson, meta);
  }

  // 4. Download resources via chrome.downloads (bypasses CORS)
  // PROVEN: assets.skool.com blocks browser fetch, but chrome.downloads works
  if (meta.resources.length > 0) {
    const safeMod = sanitize(moduleTitle);
    const safeLes = sanitize(lesson.name);
    const subfolder = `${safeMod}/${safeLes}`;
    for (const r of meta.resources) {
      if (r.downloadUrl) {
        const fname = r.fileName || `${r.title}.pdf`;
        console.log(`[SDC] Downloading resource: ${fname} from ${r.downloadUrl}`);
        try {
          await chrome.runtime.sendMessage({
            action: 'downloadResource',
            url: r.downloadUrl,
            filename: fname,
            subfolder
          });
        } catch (err) {
          console.error(`[SDC] Resource download error: ${err.message}`);
        }
        await wait(1000);
      }
    }
  }

  // 5. Send to backend
  const result = await sendCourseData(index, lesson.name, moduleTitle, courseData);
  console.log(`[SDC] Lesson ${courseNum} sent to backend:`, result);

  // 6. Mark complete
  if (options.markComplete) {
    await markLessonComplete();
  }

  return result;
}

async function markLessonComplete() {
  for (let attempt = 0; attempt < 5; attempt++) {
    const btns = document.querySelectorAll('button');
    let checkBtn = null;
    let isGreen = false;

    for (const btn of btns) {
      const svg = btn.querySelector('svg');
      if (!svg) continue;
      const vb = svg.getAttribute('viewBox');
      if (vb === '0 0 40 40') { checkBtn = btn; isGreen = true; break; }
      if (vb === '0 0 24 24' && svg.querySelectorAll('path').length >= 2) { checkBtn = btn; break; }
    }

    if (!checkBtn) { await wait(1500); continue; }
    if (isGreen) break;
    checkBtn.click();
    await wait(2000);
    if (checkBtn.querySelector('svg')?.getAttribute('viewBox') === '0 0 40 40') break;
  }
}

// ============================================================
// MAIN DOWNLOAD LOOP
// ============================================================

async function startDownloadLoop(scanData, options) {
  stopRequested = false;
  const { modules, groupName, groupId } = scanData;
  const startIdx = (options.startFrom || 1) - 1;

  console.log(`[SDC] Starting download. ${modules.length} modules, group="${groupName}"`);

  let globalLessonIdx = 0;
  let totalLessons = 0; // Will be computed as we go

  for (let mi = 0; mi < modules.length; mi++) {
    if (stopRequested) break;

    const mod = modules[mi];
    console.log(`[SDC] Entering module ${mi + 1}/${modules.length}: ${mod.title}`);

    // Navigate to the module to discover its lessons
    const lessons = await scanModuleLessons(mod.slug, groupName);

    if (lessons.length === 0) {
      console.log(`[SDC] Module "${mod.title}" has no lessons, skipping`);
      continue;
    }

    totalLessons += lessons.length;

    // Send module info to backend
    await fetch(`${BACKEND}/course-progress`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        courseIndex: mi,
        courseName: `[Module] ${mod.title}`,
        status: 'module_start',
        lessonCount: lessons.length
      })
    }).catch(() => {});

    // Process each lesson in this module
    for (let li = 0; li < lessons.length; li++) {
      if (stopRequested) break;

      // Skip if before startFrom
      if (globalLessonIdx < startIdx) {
        globalLessonIdx++;
        continue;
      }

      const lesson = lessons[li];
      try {
        await processSingleLesson(lesson, globalLessonIdx, totalLessons, mod.title, options, groupId);
      } catch (err) {
        console.error(`[SDC] Error processing lesson ${globalLessonIdx + 1}:`, err);
        await sendCourseData(globalLessonIdx, lesson.name, mod.title, {
          meta: { error: err.message },
          html: null, screenshotDataUrl: null, videoInfo: null
        });
      }

      globalLessonIdx++;
      await wait(2000);
    }
  }

  await fetch(`${BACKEND}/download-complete`, { method: 'POST' }).catch(() => {});
  console.log('[SDC] Download loop complete');
}

// ============================================================
// MESSAGE HANDLER
// ============================================================

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // HTML capture - called by service worker after injecting this script
  if (msg.action === 'captureHtml') {
    capturePageHtml().then(html => {
      sendResponse({ html });
    }).catch(err => {
      sendResponse({ html: null, error: err.message });
    });
    return true;
  }
});

console.log('[SDC] Content script loaded on:', window.location.href);
